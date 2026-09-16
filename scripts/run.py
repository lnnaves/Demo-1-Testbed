#!/usr/bin/env python3

import argparse
import copy
import ipaddress
import json
import os
import posixpath
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "PyYAML is required.\n"
        "Install it with: python3 -m pip install PyYAML"
    ) from exc

from containernet.net import Containernet
from containernet.node import DockerSta
from mininet.log import info, setLogLevel
from mn_wifi.cli import CLI
from mn_wifi.link import adhoc, wmediumd
from mn_wifi.telemetry import telemetry
from mn_wifi.wmediumdConnector import interference


# =============================================================================
# Project paths
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BIN_HOST_DIR = PROJECT_ROOT / "bin"

DEFAULT_SCENARIO_PATH = SCRIPT_DIR / "scenario.example.yml"

# Writable directory mounted inside every protocol container.
RESULTS_CONTAINER_DIR = "/opt/testbed/results"


# =============================================================================
# Defaults
# =============================================================================

DEFAULT_BSSID = "02:11:22:33:44:55"

WLAN_MTU = 1500
BAT_MTU_DESIRED = 5000
MTU_FALLBACK = 1500

DEFAULT_NODE_MEMORY = "4g"
DEFAULT_GCS_MEMORY = "16g"
DEFAULT_NODE_RANGE_METERS = 25
DEFAULT_TXPOWER_DBM = 10

SUPPORTED_ROLES = {"sender", "receiver"}
SUPPORTED_MEDIUM_MODES = {"interference"}
SUPPORTED_COMMUNICATION_MODES = {"unicast", "broadcast"}
SUPPORTED_MISSING_BINARY_POLICIES = {"idle", "skip", "fail"}
SUPPORTED_TERMINATION_CONDITIONS = {
    "senders_completed",
    "duration_elapsed",
}

CONTAINER_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
)

INTERFACE_COUNTER_NAMES = (
    "rx_bytes",
    "tx_bytes",
    "rx_packets",
    "tx_packets",
    "rx_dropped",
    "tx_dropped",
    "rx_errors",
    "tx_errors",
)

NODE_COMMAND_LOCKS = {}
NODE_COMMAND_LOCKS_GUARD = threading.Lock()


def get_node_command_lock(node):
    """
    Return a lock dedicated to one Mininet/Containernet node.

    node.cmd() uses the node's interactive shell and must not be called
    concurrently by multiple threads.
    """

    node_key = getattr(node, "did", None) or node.name

    with NODE_COMMAND_LOCKS_GUARD:
        if node_key not in NODE_COMMAND_LOCKS:
            NODE_COMMAND_LOCKS[node_key] = threading.RLock()

        return NODE_COMMAND_LOCKS[node_key]

# =============================================================================
# Utility functions
# =============================================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_now_iso():
    return utc_now().isoformat()


def safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value))
    value = value.strip(".-")
    return value or "unnamed"


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as output:
        json.dump(
            data,
            output,
            indent=2,
            sort_keys=False,
            ensure_ascii=False,
        )
        output.write("\n")


def address_without_prefix(ip_cidr):
    return str(ipaddress.ip_interface(ip_cidr).ip)


def container_binary_path(container_directory, binary):
    parts = [
        part
        for part in PurePosixPath(binary).parts
        if part not in ("", ".")
    ]

    return posixpath.join(container_directory, *parts)


def host_binary_path(binary):
    parts = [
        part
        for part in PurePosixPath(binary).parts
        if part not in ("", ".")
    ]

    return BIN_HOST_DIR.joinpath(*parts)


# =============================================================================
# Arguments
# =============================================================================

def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Run a protocol experiment using "
            "Containernet, Mininet-WiFi and wmediumd."
        )
    )

    parser.add_argument(
        "scenario",
        nargs="?",
        default=str(DEFAULT_SCENARIO_PATH),
        help="Path to the YAML scenario.",
    )

    parser.add_argument(
        "--render",
        action="store_true",
        help=(
            "Validate and print the normalized scenario without "
            "creating the network."
        ),
    )

    parser.add_argument(
        "--cli",
        action="store_true",
        help=(
            "Create the topology and open the Mininet-WiFi CLI "
            "without executing the protocol experiment."
        ),
    )

    parser.add_argument(
        "--telemetry",
        action="store_true",
        help="Enable Mininet-WiFi position telemetry.",
    )

    return parser.parse_args()


# =============================================================================
# Scenario loading and node-group expansion
# =============================================================================

def load_scenario(path):
    scenario_path = Path(path).resolve()

    if not scenario_path.is_file():
        raise ValueError(
            f"Scenario file not found: {scenario_path}"
        )

    try:
        with scenario_path.open(
            "r",
            encoding="utf-8",
        ) as scenario_file:
            scenario = yaml.safe_load(scenario_file)
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Invalid YAML in {scenario_path}: {exc}"
        ) from exc

    if not isinstance(scenario, dict):
        raise ValueError(
            "The scenario root must be a YAML mapping."
        )

    scenario["_scenario_file"] = str(scenario_path)
    return scenario


def deep_merge(base, override):
    result = copy.deepcopy(base)

    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(
                result[key],
                value,
            )
        else:
            result[key] = copy.deepcopy(value)

    return result


def render_template_value(value, context):
    if isinstance(value, dict):
        return {
            key: render_template_value(item, context)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            render_template_value(item, context)
            for item in value
        ]

    if not isinstance(value, str):
        return value

    try:
        rendered = value.format_map(context)
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"Invalid node-group template value: {value!r}"
        ) from exc

    if rendered == value:
        return rendered

    try:
        parsed = yaml.safe_load(rendered)
    except yaml.YAMLError:
        return rendered

    if isinstance(parsed, (int, float, bool)) or parsed is None:
        return parsed

    return rendered


def normalize_group_overrides(raw_overrides, group_name):
    if raw_overrides is None:
        return {}

    normalized = {}

    if isinstance(raw_overrides, dict):
        iterable = raw_overrides.items()

        for raw_index, override in iterable:
            try:
                index = int(raw_index)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid override index in group "
                    f"{group_name!r}: {raw_index!r}"
                ) from exc

            if not isinstance(override, dict):
                raise ValueError(
                    f"Override {index} in group "
                    f"{group_name!r} must be a mapping."
                )

            normalized[index] = copy.deepcopy(override)

        return normalized

    if isinstance(raw_overrides, list):
        for override in raw_overrides:
            if not isinstance(override, dict):
                raise ValueError(
                    f"Overrides in group {group_name!r} "
                    "must be mappings."
                )

            if "index" not in override:
                raise ValueError(
                    f"An override in group {group_name!r} "
                    "does not contain index."
                )

            try:
                index = int(override["index"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid override index in group "
                    f"{group_name!r}."
                ) from exc

            value = copy.deepcopy(override)
            value.pop("index", None)
            normalized[index] = value

        return normalized

    raise ValueError(
        f"node_groups[{group_name}].overrides must be "
        "a list or mapping."
    )


def expand_node_groups(scenario):
    explicit_nodes = scenario.get("nodes") or []
    groups = scenario.get("node_groups") or []

    if not isinstance(explicit_nodes, list):
        raise ValueError("nodes must be a list.")

    if not isinstance(groups, list):
        raise ValueError("node_groups must be a list.")

    expanded = copy.deepcopy(explicit_nodes)

    for position, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(
                f"node_groups[{position}] must be a mapping."
            )

        group_name = group.get("name")
        count = group.get("count")
        start_index = group.get("start_index", 1)
        template = group.get("template")

        if not isinstance(group_name, str) or not group_name:
            raise ValueError(
                f"node_groups[{position}].name is invalid."
            )

        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 1
        ):
            raise ValueError(
                f"Group {group_name!r} must have a positive count."
            )

        if (
            not isinstance(start_index, int)
            or isinstance(start_index, bool)
            or start_index < 0
        ):
            raise ValueError(
                f"Group {group_name!r} has an invalid start_index."
            )

        if not isinstance(template, dict):
            raise ValueError(
                f"Group {group_name!r} must contain a template."
            )

        overrides = normalize_group_overrides(
            group.get("overrides"),
            group_name,
        )

        valid_indexes = set(
            range(start_index, start_index + count)
        )

        invalid_indexes = set(overrides) - valid_indexes

        if invalid_indexes:
            raise ValueError(
                f"Group {group_name!r} has overrides outside "
                f"its range: {sorted(invalid_indexes)}"
            )

        for ordinal in range(count):
            index = start_index + ordinal

            context = {
                "group": group_name,
                "index": index,
                "ordinal": ordinal,
            }

            node = render_template_value(
                copy.deepcopy(template),
                context,
            )

            if index in overrides:
                override = render_template_value(
                    overrides[index],
                    context,
                )
                node = deep_merge(node, override)

            expanded.append(node)

    normalized = copy.deepcopy(scenario)
    normalized["nodes"] = expanded
    normalized.pop("node_groups", None)

    return normalized


# =============================================================================
# Defaults and normalization
# =============================================================================

def apply_defaults(scenario):
    normalized = copy.deepcopy(scenario)

    experiment = normalized.setdefault("experiment", {})
    experiment.setdefault("id", "testbed-experiment")
    experiment.setdefault("seed", 42)
    experiment.setdefault("duration_seconds", 60)

    containers = normalized.setdefault("containers", {})
    containers.setdefault("image", "drone:latest")
    containers.setdefault(
        "binaries_directory",
        "/opt/protocol/bin",
    )
    containers.setdefault("sender_binary", "sender")
    containers.setdefault("receiver_binary", "receiver")

    execution = normalized.setdefault("execution", {})
    execution.setdefault("enabled", True)
    execution.setdefault("missing_binaries", "idle")

    network = normalized.setdefault("network", {})
    network.setdefault("interface", "bat0")
    network.setdefault("subnet", "192.168.123.0/24")

    wireless = network.setdefault("wireless", {})
    wireless.setdefault("ssid", "adhocNet")
    wireless.setdefault("mode", "g")
    wireless.setdefault("channel", 5)
    wireless.setdefault("bssid", DEFAULT_BSSID)
    wireless.setdefault("ht_cap", "HT40+")

    medium = network.setdefault("medium", {})
    medium.setdefault("mode", "interference")
    medium.setdefault("noise_threshold_dbm", -91)
    medium.setdefault("fading_coefficient", 3)

    propagation = medium.setdefault("propagation", {})
    propagation.setdefault("model", "logDistance")
    propagation.setdefault("exponent", 3.5)

    mobility = normalized.setdefault("mobility", {})
    mobility.setdefault("enabled", False)
    mobility.setdefault("model", "static")

    communication = normalized.setdefault("communication", {})
    communication.setdefault("mode", "unicast")
    communication.setdefault("port", 9000)
    communication.setdefault("flows", [])
    communication.setdefault("broadcasts", [])

    transmission = communication.setdefault("transmission", {})
    transmission.setdefault("count", 1)
    #transmission.setdefault("interval_ms", 0) removido para deixar o YAML honesto

    readiness = normalized.setdefault("readiness", {})
    readiness.setdefault("expected_output", "READY")
    readiness.setdefault("timeout_seconds", 10)

    termination = normalized.setdefault("termination", {})
    termination.setdefault("condition", "senders_completed")
    termination.setdefault("shutdown_timeout_seconds", 5)

    measurements = normalized.setdefault("measurements", {})
    measurements.setdefault("enabled", True)
    measurements.setdefault("sampling_interval_seconds", 1)

    process_config = measurements.setdefault("process", {})
    #process_config.setdefault("enabled", True) - valor criado mas nao eh utilizado
    process_config.setdefault("capture_stdout", True)
    process_config.setdefault("capture_stderr", True)

    packet_capture = measurements.setdefault(
        "packet_capture",
        {},
    )
    packet_capture.setdefault("enabled", True)
    packet_capture.setdefault("interfaces", ["bat0"])
    packet_capture.setdefault("snaplen", 0)
    packet_capture.setdefault("immediate_mode", True)
    packet_capture.setdefault("filter", "")

    interface_counters = measurements.setdefault(
        "interface_counters",
        {},
    )
    interface_counters.setdefault("enabled", True)
    interface_counters.setdefault(
        "interfaces",
        ["bat0", "wlan0"],
    )

    batman = measurements.setdefault("batman", {})
    batman.setdefault("enabled", True)
    batman.setdefault("collect_periodically", False)

    wmediumd_config = measurements.setdefault("wmediumd", {})
    wmediumd_config.setdefault("capture_log", False)

    active_probes = measurements.setdefault(
        "active_probes",
        {},
    )
    active_probes.setdefault("enabled", False)

    results = normalized.setdefault("results", {})
    results.setdefault("directory", "./logs")

    for node in normalized.get("nodes", []):
        if not isinstance(node, dict):
            continue

        node.setdefault("type", "drone")
        node.setdefault("roles", [])
        node.setdefault("image", containers["image"])

        node.setdefault(
            "memory",
            (
                DEFAULT_GCS_MEMORY
                if node.get("type") == "gcs"
                else DEFAULT_NODE_MEMORY
            ),
        )

        node.setdefault(
            "range_meters",
            DEFAULT_NODE_RANGE_METERS,
        )

        node.setdefault(
            "txpower_dbm",
            DEFAULT_TXPOWER_DBM,
        )

    return normalized


def normalize_scenario(scenario):
    return apply_defaults(
        expand_node_groups(scenario)
    )


# =============================================================================
# Validation
# =============================================================================

def positive_number(value, path, allow_zero=False):
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        raise ValueError(f"{path} must be numeric.")

    if allow_zero:
        if value < 0:
            raise ValueError(
                f"{path} must be zero or greater."
            )
    elif value <= 0:
        raise ValueError(
            f"{path} must be greater than zero."
        )


def validate_binary_path(value, path):
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{path} must be a non-empty string."
        )

    binary_path = PurePosixPath(value)

    if binary_path.is_absolute():
        raise ValueError(
            f"{path} must be relative to "
            "containers.binaries_directory."
        )

    if ".." in binary_path.parts:
        raise ValueError(
            f"{path} must not contain '..'."
        )


def validate_scenario(scenario):
    experiment = scenario["experiment"]

    if not isinstance(experiment.get("id"), str):
        raise ValueError(
            "experiment.id must be a string."
        )

    if not isinstance(experiment.get("seed"), int):
        raise ValueError(
            "experiment.seed must be an integer."
        )

    positive_number(
        experiment.get("duration_seconds"),
        "experiment.duration_seconds",
    )

    containers = scenario["containers"]

    if not isinstance(containers.get("image"), str):
        raise ValueError(
            "containers.image must be a string."
        )

    binaries_directory = containers.get(
        "binaries_directory"
    )

    if (
        not isinstance(binaries_directory, str)
        or not PurePosixPath(binaries_directory).is_absolute()
    ):
        raise ValueError(
            "containers.binaries_directory must be "
            "an absolute container path."
        )

    validate_binary_path(
        containers.get("sender_binary"),
        "containers.sender_binary",
    )

    validate_binary_path(
        containers.get("receiver_binary"),
        "containers.receiver_binary",
    )

    execution = scenario["execution"]

    if not isinstance(execution.get("enabled"), bool):
        raise ValueError(
            "execution.enabled must be true or false."
        )

    missing_policy = execution.get("missing_binaries")

    if missing_policy not in SUPPORTED_MISSING_BINARY_POLICIES:
        raise ValueError(
            "execution.missing_binaries must be one of: "
            + ", ".join(
                sorted(SUPPORTED_MISSING_BINARY_POLICIES)
            )
        )

    network = scenario["network"]

    if network.get("interface") != "bat0":
        raise ValueError(
            "This implementation currently requires "
            "network.interface: bat0."
        )

    try:
        subnet = ipaddress.ip_network(
            network.get("subnet"),
            strict=False,
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid network.subnet: {network.get('subnet')}"
        ) from exc

    wireless = network["wireless"]

    if not isinstance(wireless.get("ssid"), str):
        raise ValueError(
            "network.wireless.ssid must be a string."
        )

    if not isinstance(wireless.get("mode"), str):
        raise ValueError(
            "network.wireless.mode must be a string."
        )

    positive_number(
        wireless.get("channel"),
        "network.wireless.channel",
    )

    medium = network["medium"]
    medium_mode = medium.get("mode")

    if medium_mode not in SUPPORTED_MEDIUM_MODES:
        raise ValueError(
            f"Unsupported network.medium.mode: "
            f"{medium_mode!r}. "
            "Currently supported: interference."
        )

    positive_number(
        medium.get("fading_coefficient"),
        "network.medium.fading_coefficient",
        allow_zero=True,
    )

    if not isinstance(
        medium.get("noise_threshold_dbm"),
        (int, float),
    ):
        raise ValueError(
            "network.medium.noise_threshold_dbm "
            "must be numeric."
        )

    propagation = medium["propagation"]

    if not isinstance(propagation.get("model"), str):
        raise ValueError(
            "network.medium.propagation.model "
            "must be a string."
        )

    positive_number(
        propagation.get("exponent"),
        "network.medium.propagation.exponent",
    )

    nodes = scenario.get("nodes")

    if not isinstance(nodes, list) or not nodes:
        raise ValueError(
            "The scenario must contain at least one node."
        )

    identities = set()
    container_names = set()
    addresses = set()
    nodes_by_identity = {}

    for index, node in enumerate(nodes):
        path = f"nodes[{index}]"

        if not isinstance(node, dict):
            raise ValueError(
                f"{path} must be a mapping."
            )

        identity = node.get("identity")
        container_name = node.get("container_name")

        if not isinstance(identity, str) or not identity:
            raise ValueError(
                f"{path}.identity is invalid."
            )

        if identity in identities:
            raise ValueError(
                f"Duplicate identity: {identity!r}"
            )

        if (
            not isinstance(container_name, str)
            or not CONTAINER_NAME_PATTERN.fullmatch(
                container_name
            )
        ):
            raise ValueError(
                f"{path}.container_name is invalid."
            )

        if container_name in container_names:
            raise ValueError(
                f"Duplicate container name: "
                f"{container_name!r}"
            )

        wireless_interface = f"{container_name}-wlan0"

        if len(wireless_interface) > 15:
            raise ValueError(
                f"Interface name {wireless_interface!r} "
                "exceeds Linux's 15-character limit."
            )

        try:
            node_address = ipaddress.ip_interface(
                node.get("ip")
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid IP for node {identity!r}: "
                f"{node.get('ip')}"
            ) from exc

        if node_address.ip not in subnet:
            raise ValueError(
                f"Node {identity!r} is outside "
                f"subnet {subnet}."
            )

        if node_address.ip in addresses:
            raise ValueError(
                f"Duplicate IP address: {node_address.ip}"
            )

        roles = node.get("roles")

        if not isinstance(roles, list):
            raise ValueError(
                f"{path}.roles must be a list."
            )

        invalid_roles = set(roles) - SUPPORTED_ROLES

        if invalid_roles:
            raise ValueError(
                f"Invalid roles for {identity!r}: "
                f"{sorted(invalid_roles)}"
            )

        position = node.get("position")

        if not isinstance(position, dict):
            raise ValueError(
                f"{path}.position must be a mapping."
            )

        for axis in ("x", "y", "z"):
            if not isinstance(
                position.get(axis),
                (int, float),
            ):
                raise ValueError(
                    f"{path}.position.{axis} must be numeric."
                )

        positive_number(
            node.get("range_meters"),
            f"{path}.range_meters",
        )

        positive_number(
            node.get("txpower_dbm"),
            f"{path}.txpower_dbm",
            allow_zero=True,
        )

        identities.add(identity)
        container_names.add(container_name)
        addresses.add(node_address.ip)
        nodes_by_identity[identity] = node

    communication = scenario["communication"]
    communication_mode = communication.get("mode")

    if communication_mode not in SUPPORTED_COMMUNICATION_MODES:
        raise ValueError(
            "communication.mode must be unicast or broadcast."
        )

    port = communication.get("port")

    if (
        not isinstance(port, int)
        or isinstance(port, bool)
        or port < 1
        or port > 65535
    ):
        raise ValueError(
            "communication.port must be between 1 and 65535."
        )

    count = communication["transmission"].get("count")

    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 1
    ):
        raise ValueError(
            "communication.transmission.count "
            "must be a positive integer."
        )

    flows = communication.get("flows")

    if not isinstance(flows, list):
        raise ValueError(
            "communication.flows must be a list."
        )

    for index, flow in enumerate(flows):
        if not isinstance(flow, dict):
            raise ValueError(
                f"communication.flows[{index}] "
                "must be a mapping."
            )

        source = flow.get("source")
        destination = flow.get("destination")

        if source not in nodes_by_identity:
            raise ValueError(
                f"Unknown flow source: {source!r}"
            )

        if destination not in nodes_by_identity:
            raise ValueError(
                f"Unknown flow destination: {destination!r}"
            )

        if "sender" not in nodes_by_identity[source]["roles"]:
            raise ValueError(
                f"Flow source {source!r} does not have "
                "the sender role."
            )

        if (
            "receiver"
            not in nodes_by_identity[destination]["roles"]
        ):
            raise ValueError(
                f"Flow destination {destination!r} "
                "does not have the receiver role."
            )

    broadcasts = communication.get("broadcasts")

    if not isinstance(broadcasts, list):
        raise ValueError(
            "communication.broadcasts must be a list."
        )

    if communication_mode == "unicast" and not flows:
        raise ValueError(
            "Unicast mode requires at least one flow."
        )

    if communication_mode == "broadcast" and not broadcasts:
        raise ValueError(
            "Broadcast mode requires at least one source."
        )

    for broadcast in broadcasts:
        source = (
            broadcast
            if isinstance(broadcast, str)
            else broadcast.get("source")
            if isinstance(broadcast, dict)
            else None
        )

        if source not in nodes_by_identity:
            raise ValueError(
                f"Unknown broadcast source: {source!r}"
            )

        if "sender" not in nodes_by_identity[source]["roles"]:
            raise ValueError(
                f"Broadcast source {source!r} does not have "
                "the sender role."
            )

    mobility = scenario["mobility"]

    if mobility.get("enabled"):
        raise ValueError(
            "Dynamic mobility is not implemented yet. "
            "Use mobility.enabled: false."
        )

    readiness = scenario["readiness"]

    if not isinstance(
        readiness.get("expected_output"),
        str,
    ):
        raise ValueError(
            "readiness.expected_output must be a string."
        )

    positive_number(
        readiness.get("timeout_seconds"),
        "readiness.timeout_seconds",
    )

    termination = scenario["termination"]

    if (
        termination.get("condition")
        not in SUPPORTED_TERMINATION_CONDITIONS
    ):
        raise ValueError(
            "termination.condition must be "
            "senders_completed or duration_elapsed."
        )

    positive_number(
        termination.get("shutdown_timeout_seconds"),
        "termination.shutdown_timeout_seconds",
    )

    measurements = scenario["measurements"]

    positive_number(
        measurements.get("sampling_interval_seconds"),
        "measurements.sampling_interval_seconds",
    )

    packet_capture = measurements["packet_capture"]

    if not isinstance(
        packet_capture.get("interfaces"),
        list,
    ):
        raise ValueError(
            "measurements.packet_capture.interfaces "
            "must be a list."
        )

    counters = measurements["interface_counters"]

    if not isinstance(counters.get("interfaces"), list):
        raise ValueError(
            "measurements.interface_counters.interfaces "
            "must be a list."
        )


# =============================================================================
# Environment and result directories
# =============================================================================

def create_result_directory(scenario):
    configured = Path(
        scenario["results"]["directory"]
    )

    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured

    experiment_name = safe_name(
        scenario["experiment"]["id"]
    )


    timestamp = utc_now().strftime(
        "%Y%m%dT%H%M%S.%fZ"
    )

    experiment_directory = (
        configured.resolve()
        / experiment_name
    )

    experiment_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    run_directory = experiment_directory / timestamp

    suffix = 1

    while True:
        try:
            run_directory.mkdir(
                parents=False,
                exist_ok=False,
            )
            break
        except FileExistsError:
            run_directory = (
                experiment_directory
                / f"{timestamp}-{suffix}"
            )
            suffix += 1

    for directory in (
        run_directory / "processes",
        run_directory / "pcaps",
        run_directory / "counters",
        run_directory / "batman",
        run_directory / "tcpdump",
        run_directory / "wmediumd",
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    normalized_scenario = copy.deepcopy(scenario)
    normalized_scenario.pop("_scenario_file", None)

    with (
        run_directory / "scenario.yml"
    ).open("w", encoding="utf-8") as output:
        yaml.safe_dump(
            normalized_scenario,
            output,
            sort_keys=False,
            allow_unicode=True,
        )

    return run_directory


def required_binary_roles(scenario):
    roles = set()

    for node in scenario["nodes"]:
        roles.update(node["roles"])

    return roles


def inspect_host_binaries(scenario):
    containers = scenario["containers"]
    roles = required_binary_roles(scenario)

    result = {
        "sender": {
            "required": "sender" in roles,
            "path": str(
                host_binary_path(
                    containers["sender_binary"]
                )
            ),
            "available": True,
        },
        "receiver": {
            "required": "receiver" in roles,
            "path": str(
                host_binary_path(
                    containers["receiver_binary"]
                )
            ),
            "available": True,
        },
    }

    for role, entry in result.items():
        if not entry["required"]:
            continue

        binary_path = Path(entry["path"])

        entry["available"] = (
            binary_path.is_file()
            and os.access(binary_path, os.X_OK)
        )

    return result


def missing_required_binaries(binary_status):
    return [
        role
        for role, entry in binary_status.items()
        if entry["required"] and not entry["available"]
    ]


# =============================================================================
# Container commands
# =============================================================================

def run_command(
    node,
    command,
    description="",
    must_succeed=True,
):
    marker = "__TESTBED_EXIT_CODE__="

    wrapped = (
        f"{command}\n"
        "testbed_status=$?\n"
        f"printf '\\n{marker}%s\\n' \"$testbed_status\""
    )

    command_lock = get_node_command_lock(node)

    with command_lock:
        output = node.cmd(
            f"sh -c {shlex.quote(wrapped)}"
        ) or ""

    exit_code = None
    visible_lines = []

    for line in output.splitlines():
        if line.startswith(marker):
            try:
                exit_code = int(
                    line[len(marker):]
                )
            except ValueError:
                exit_code = None
        else:
            visible_lines.append(line)

    clean_output = "\n".join(visible_lines).strip()

    if exit_code is None:
        if must_succeed:
            raise RuntimeError(
                f"Could not determine exit code on "
                f"{node.name}: {description or command}"
            )

        return clean_output, -1

    if exit_code != 0:
        if must_succeed:
            raise RuntimeError(
                f"Command failed on {node.name}: "
                f"{description or command}; "
                f"exit code={exit_code}; "
                f"output={clean_output!r}"
            )

    return clean_output, exit_code


def ensure_interface(node, interface):
    run_command(
        node,
        f"ip link show dev {shlex.quote(interface)}",
        f"check interface {interface}",
    )


def force_adhoc_cell(
    node,
    interface,
    ssid,
    bssid,
    channel,
):
    quoted_interface = shlex.quote(interface)

    run_command(
        node,
        "command -v iwconfig",
        "check iwconfig",
    )

    run_command(
        node,
        f"ip link set dev {quoted_interface} down",
        f"bring {interface} down",
        must_succeed=False,
    )

    run_command(
        node,
        (
            f"iwconfig {quoted_interface} "
            "mode ad-hoc "
            f"essid {shlex.quote(ssid)} "
            f"ap {shlex.quote(bssid)} "
            f"channel {int(channel)}"
        ),
        f"configure ad-hoc cell on {interface}",
    )

    run_command(
        node,
        f"ip link set dev {quoted_interface} up",
        f"bring {interface} up",
    )


def configure_mtu(
    node,
    interface,
    desired,
    fallback=None,
):
    ensure_interface(node, interface)

    _, exit_code = run_command(
        node,
        (
            f"ip link set dev {shlex.quote(interface)} "
            f"mtu {int(desired)}"
        ),
        f"set MTU {desired} on {interface}",
        must_succeed=fallback is None,
    )

    if exit_code != 0 and fallback is not None:
        info(
            f"*** {node.name}: using MTU "
            f"{fallback} on {interface}\n"
        )

        run_command(
            node,
            (
                f"ip link set dev "
                f"{shlex.quote(interface)} "
                f"mtu {int(fallback)}"
            ),
            f"set fallback MTU on {interface}",
        )


def assign_interface_ip(
    node,
    interface,
    ip_cidr,
):
    ensure_interface(node, interface)

    run_command(
        node,
        f"ip link set dev {shlex.quote(interface)} up",
        f"bring {interface} up",
    )

    run_command(
        node,
        (
            f"ip addr flush dev {shlex.quote(interface)} && "
            f"ip addr add {shlex.quote(ip_cidr)} "
            f"dev {shlex.quote(interface)}"
        ),
        f"assign {ip_cidr} to {interface}",
    )


# =============================================================================
# Network construction
# =============================================================================

def generate_mac(index):
    return "02:{:02x}:{:02x}:{:02x}:{:02x}:{:02x}".format(
        (index >> 32) & 0xFF,
        (index >> 24) & 0xFF,
        (index >> 16) & 0xFF,
        (index >> 8) & 0xFF,
        index & 0xFF,
    )


def generate_management_ip(index):
    network = ipaddress.ip_network("10.0.0.0/8")
    address = network.network_address + index
    return f"{address}/{network.prefixlen}"


def format_position(position):
    return "{},{},{}".format(
        position["x"],
        position["y"],
        position["z"],
    )


def create_network(scenario):
    medium = scenario["network"]["medium"]
    propagation = medium["propagation"]

    net = Containernet(
        link=wmediumd,
        wmediumd_mode=interference,
        noise_th=medium["noise_threshold_dbm"],
        fading_cof=medium["fading_coefficient"],
    )

    net.setPropagationModel(
        model=propagation["model"],
        exp=propagation["exponent"],
    )

    return net


def create_stations(
    net,
    scenario,
    run_directory,
):
    container_directory = scenario["containers"][
        "binaries_directory"
    ]

    volumes = [
        (
            f"{BIN_HOST_DIR.resolve()}:"
            f"{container_directory}:ro"
        ),
        (
            f"{run_directory.resolve()}:"
            f"{RESULTS_CONTAINER_DIR}:rw"
        ),
    ]

    stations = {}

    info("*** Adding Docker stations\n")

    for index, node in enumerate(
        scenario["nodes"],
        start=1,
    ):
        identity = node["identity"]
        container_name = node["container_name"]

        stations[identity] = net.addStation(
            container_name,
            cls=DockerSta,
            mac=generate_mac(index),
            ip=generate_management_ip(index),
            position=format_position(node["position"]),
            dimage=node["image"],
            privileged=True,
            mem_limit=node["memory"],
            range=node["range_meters"],
            txpower=node["txpower_dbm"],
            volumes=volumes,
        )

        info(
            f"*** Added {identity} as "
            f"{container_name}\n"
        )

    return stations


def configure_adhoc_links(
    net,
    scenario,
    stations,
):
    wireless = scenario["network"]["wireless"]

    info("*** Creating BATMAN-adv ad-hoc links\n")

    for node in scenario["nodes"]:
        station = stations[node["identity"]]
        wlan = f"{station.name}-wlan0"

        arguments = {
            "cls": adhoc,
            "intf": wlan,
            "ssid": wireless["ssid"],
            "proto": "batman_adv",
            "mode": wireless["mode"],
            "channel": wireless["channel"],
        }

        if wireless.get("ht_cap"):
            arguments["ht_cap"] = wireless["ht_cap"]

        net.addLink(station, **arguments)


def configure_interfaces(
    scenario,
    stations,
):
    network = scenario["network"]
    wireless = network["wireless"]
    operational_interface = network["interface"]

    info("*** Configuring node interfaces\n")

    for node in scenario["nodes"]:
        station = stations[node["identity"]]
        wlan = f"{station.name}-wlan0"

        force_adhoc_cell(
            station,
            wlan,
            wireless["ssid"],
            wireless["bssid"],
            wireless["channel"],
        )

        configure_mtu(
            station,
            wlan,
            WLAN_MTU,
        )

        configure_mtu(
            station,
            operational_interface,
            BAT_MTU_DESIRED,
            fallback=MTU_FALLBACK,
        )

        assign_interface_ip(
            station,
            operational_interface,
            node["ip"],
        )


def print_topology_summary(
    scenario,
    stations,
):
    info("\n*** Topology ready\n")
    info(
        "*** Identity             Container       "
        "Operational address        Roles\n"
    )
    info(
        "*** -------------------- ---------------- "
        "-------------------------- ----------------\n"
    )

    for node in scenario["nodes"]:
        station = stations[node["identity"]]
        roles = ",".join(node["roles"]) or "-"

        info(
            f"*** {node['identity']:<20} "
            f"{station.name:<16} "
            f"{node['ip']:<26} "
            f"{roles}\n"
        )


# =============================================================================
# Managed processes
# =============================================================================

class ManagedProcess:
    def __init__(
        self,
        process_id,
        node,
        role,
        command,
        process,
        pid_file,
        stdout_path,
        stderr_path,
        stdout_file,
        stderr_file,
    ):
        self.process_id = process_id
        self.node = node
        self.role = role
        self.command = command
        self.process = process
        self.pid_file = pid_file

        self.stdout_path = str(stdout_path)
        self.stderr_path = str(stderr_path)

        self.stdout_file = stdout_file
        self.stderr_file = stderr_file

        self.started_at = utc_now_iso()
        self.ready_at = None
        self.finished_at = None
        self.return_code = None
        self.stop_signal = None

        self.stdout_queue = queue.Queue()
        self.threads = []


def stream_reader(
    stream,
    output_file,
    output_queue=None,
):
    try:
        for line in iter(stream.readline, ""):
            if output_file is not None:
                output_file.write(line)
                output_file.flush()

            if output_queue is not None:
                output_queue.put(line.rstrip("\r\n"))
    finally:
        try:
            stream.close()
        except Exception:
            pass


def start_managed_process(
    node,
    process_id,
    role,
    command,
    run_directory,
    capture_stdout=True,
    capture_stderr=True,
):
    process_name = safe_name(process_id)

    stdout_path = (
        run_directory
        / "processes"
        / f"{process_name}.stdout.log"
    )

    stderr_path = (
        run_directory
        / "processes"
        / f"{process_name}.stderr.log"
    )

    stdout_file = (
        stdout_path.open(
            "w",
            encoding="utf-8",
            buffering=1,
        )
        if capture_stdout
        else None
    )

    stderr_file = (
        stderr_path.open(
            "w",
            encoding="utf-8",
            buffering=1,
        )
        if capture_stderr
        else None
    )

    pid_file = (
        f"/tmp/testbed-{process_name}.pid"
    )

    shell_command = (
        f"echo $$ > {shlex.quote(pid_file)}; "
        f"exec {shlex.join(command)}"
    )

    docker_command = [
        "docker",
        "exec",
        "-i",
        node.did,
        "sh",
        "-c",
        shell_command,
    ]

    process = subprocess.Popen(
        docker_command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    managed = ManagedProcess(
        process_id=process_id,
        node=node,
        role=role,
        command=command,
        process=process,
        pid_file=pid_file,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_file=stdout_file,
        stderr_file=stderr_file,
    )

    stdout_thread = threading.Thread(
        target=stream_reader,
        args=(
            process.stdout,
            stdout_file,
            managed.stdout_queue,
        ),
        daemon=True,
    )

    stderr_thread = threading.Thread(
        target=stream_reader,
        args=(
            process.stderr,
            stderr_file,
            None,
        ),
        daemon=True,
    )

    stdout_thread.start()
    stderr_thread.start()

    managed.threads.extend([
        stdout_thread,
        stderr_thread,
    ])

    return managed


def finalize_managed_process(managed):
    if managed.finished_at is None:
        managed.finished_at = utc_now_iso()

    managed.return_code = managed.process.poll()

    for thread in managed.threads:
        thread.join(timeout=1)

    for output_file in (
        managed.stdout_file,
        managed.stderr_file,
    ):
        if output_file is not None and not output_file.closed:
            output_file.close()


def stop_managed_process(
    managed,
    timeout_seconds,
):
    if managed.process.poll() is not None:
        finalize_managed_process(managed)
        return

    managed.stop_signal = "SIGTERM"

    run_command(
        managed.node,
        (
            f"test ! -f {shlex.quote(managed.pid_file)} || "
            f"kill -TERM "
            f"$(cat {shlex.quote(managed.pid_file)})"
        ),
        f"terminate {managed.process_id}",
        must_succeed=False,
    )

    try:
        managed.process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        managed.stop_signal = "SIGKILL"

        run_command(
            managed.node,
            (
                f"test ! -f {shlex.quote(managed.pid_file)} || "
                f"kill -KILL "
                f"$(cat {shlex.quote(managed.pid_file)})"
            ),
            f"kill {managed.process_id}",
            must_succeed=False,
        )

        try:
            managed.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            managed.process.kill()
            managed.process.wait(timeout=2)

    finalize_managed_process(managed)


def process_metadata(managed):
    return {
        "id": managed.process_id,
        "node": managed.node.name,
        "role": managed.role,
        "command": managed.command,
        "started_at": managed.started_at,
        "ready_at": managed.ready_at,
        "finished_at": managed.finished_at,
        "return_code": managed.return_code,
        "stop_signal": managed.stop_signal,
        "stdout": managed.stdout_path,
        "stderr": managed.stderr_path,
    }


# =============================================================================
# Binary availability and orchestration
# =============================================================================

def inspect_container_binaries(
    scenario,
    stations,
):
    containers = scenario["containers"]
    container_directory = containers[
        "binaries_directory"
    ]

    role_paths = {
        "sender": container_binary_path(
            container_directory,
            containers["sender_binary"],
        ),
        "receiver": container_binary_path(
            container_directory,
            containers["receiver_binary"],
        ),
    }

    missing = []

    for node in scenario["nodes"]:
        station = stations[node["identity"]]

        for role in node["roles"]:
            path = role_paths[role]

            _, exit_code = run_command(
                station,
                f"test -x {shlex.quote(path)}",
                f"check {role} binary",
                must_succeed=False,
            )

            if exit_code != 0:
                missing.append({
                    "identity": node["identity"],
                    "container": station.name,
                    "role": role,
                    "path": path,
                })

    return role_paths, missing


def start_receivers(
    scenario,
    stations,
    role_paths,
    run_directory,
    process_registry,
):
    processes = []
    process_config = scenario["measurements"]["process"]
    port = scenario["communication"]["port"]

    info("*** Starting receivers\n")

    for node in scenario["nodes"]:
        if "receiver" not in node["roles"]:
            continue

        identity = node["identity"]
        station = stations[identity]

        command = [
            role_paths["receiver"],
            "--address",
            address_without_prefix(node["ip"]),
            "--port",
            str(port),
        ]

        managed = start_managed_process(
            node=station,
            process_id=f"{identity}.receiver",
            role="receiver",
            command=command,
            run_directory=run_directory,
            capture_stdout=process_config[
                "capture_stdout"
            ],
            capture_stderr=process_config[
                "capture_stderr"
            ],
        )

        processes.append(managed)
        process_registry.append(managed)

    return processes


def wait_for_receivers(
    receiver_processes,
    scenario,
):
    expected = scenario["readiness"]["expected_output"]
    timeout = scenario["readiness"]["timeout_seconds"]
    deadline = time.monotonic() + timeout

    info(
        f"*** Waiting for receiver state "
        f"{expected!r}\n"
    )

    pending = set(receiver_processes)

    while pending:
        if time.monotonic() >= deadline:
            pending_names = [
                process.process_id
                for process in pending
            ]

            raise RuntimeError(
                "Receiver readiness timeout. Pending: "
                + ", ".join(pending_names)
            )

        for managed in list(pending):
            return_code = managed.process.poll()

            if return_code is not None:
                finalize_managed_process(managed)

                raise RuntimeError(
                    f"Receiver {managed.process_id} exited "
                    f"before READY with code {return_code}."
                )

            try:
                line = managed.stdout_queue.get_nowait()
            except queue.Empty:
                continue

            if line.strip() == expected:
                managed.ready_at = utc_now_iso()
                pending.remove(managed)

                info(
                    f"*** {managed.process_id}: READY\n"
                )

        time.sleep(0.05)


def start_senders(
    scenario,
    stations,
    role_paths,
    run_directory,
    process_registry,
):
    communication = scenario["communication"]
    process_config = scenario["measurements"]["process"]

    mode = communication["mode"]
    port = communication["port"]
    count = communication["transmission"]["count"]

    nodes_by_identity = {
        node["identity"]: node
        for node in scenario["nodes"]
    }

    processes = []

    info("*** Starting senders\n")

    if mode == "unicast":
        for index, flow in enumerate(
            communication["flows"],
            start=1,
        ):
            source = flow["source"]
            destination = flow["destination"]

            destination_address = address_without_prefix(
                nodes_by_identity[destination]["ip"]
            )

            command = [
                role_paths["sender"],
                "--mode",
                "unicast",
                "--destination",
                destination_address,
                "--port",
                str(port),
                "--count",
                str(count),
            ]

            managed = start_managed_process(
                node=stations[source],
                process_id=(
                    f"{source}.sender."
                    f"{index}.to.{destination}"
                ),
                role="sender",
                command=command,
                run_directory=run_directory,
                capture_stdout=process_config[
                    "capture_stdout"
                ],
                capture_stderr=process_config[
                    "capture_stderr"
                ],
            )

            processes.append(managed)
            process_registry.append(managed)

    else:
        subnet = ipaddress.ip_network(
            scenario["network"]["subnet"],
            strict=False,
        )

        broadcast_address = str(
            subnet.broadcast_address
        )

        for index, broadcast in enumerate(
            communication["broadcasts"],
            start=1,
        ):
            source = (
                broadcast
                if isinstance(broadcast, str)
                else broadcast["source"]
            )

            destination = (
                broadcast.get(
                    "destination",
                    broadcast_address,
                )
                if isinstance(broadcast, dict)
                else broadcast_address
            )

            command = [
                role_paths["sender"],
                "--mode",
                "broadcast",
                "--destination",
                destination,
                "--port",
                str(port),
                "--count",
                str(count),
            ]

            managed = start_managed_process(
                node=stations[source],
                process_id=(
                    f"{source}.sender."
                    f"{index}.broadcast"
                ),
                role="sender",
                command=command,
                run_directory=run_directory,
                capture_stdout=process_config[
                    "capture_stdout"
                ],
                capture_stderr=process_config[
                    "capture_stderr"
                ],
            )

            processes.append(managed)
            process_registry.append(managed)

    return processes

def monitor_protocol_processes(
    sender_processes,
    receiver_processes,
    scenario,
):
    """
    Monitor senders and receivers until the experiment terminates.

    A sender exit code different from zero is considered a failure.
    A receiver that exits before the testbed terminates it is also
    considered a failure.
    """

    condition = scenario["termination"]["condition"]
    duration = scenario["experiment"]["duration_seconds"]
    deadline = time.monotonic() + duration

    while True:
        # Receivers must remain alive until the orchestrator stops them.
        for managed in receiver_processes:
            return_code = managed.process.poll()

            if return_code is None:
                continue

            if managed.finished_at is None:
                finalize_managed_process(managed)

            raise RuntimeError(
                f"Receiver {managed.process_id} exited "
                f"unexpectedly with code {return_code}."
            )

        running_senders = []

        for managed in sender_processes:
            return_code = managed.process.poll()

            if return_code is None:
                running_senders.append(managed)
                continue

            if managed.finished_at is None:
                finalize_managed_process(managed)

            if return_code != 0:
                raise RuntimeError(
                    f"Sender {managed.process_id} failed "
                    f"with exit code {return_code}."
                )

        if (
            condition == "senders_completed"
            and not running_senders
        ):
            return

        if time.monotonic() >= deadline:
            if (
                condition == "senders_completed"
                and running_senders
            ):
                running_names = [
                    process.process_id
                    for process in running_senders
                ]

                raise RuntimeError(
                    "Experiment duration elapsed before "
                    "the senders completed: "
                    + ", ".join(running_names)
                )

            # duration_elapsed intentionally ends here.
            return

        time.sleep(0.1)

#def wait_for_senders(
#    sender_processes,
#    scenario,
#):
#    condition = scenario["termination"]["condition"]
#    duration = scenario["experiment"]["duration_seconds"]
#    deadline = time.monotonic() + duration
#
#    while True:
#        running = [
#            process
#            for process in sender_processes
#            if process.process.poll() is None
#        ]
#
#        if condition == "senders_completed" and not running:
#            break
#
#        if time.monotonic() >= deadline:
#            break
#
#        time.sleep(0.1)
#
#    for managed in sender_processes:
#        if managed.process.poll() is not None:
#            finalize_managed_process(managed)


# =============================================================================
# Packet captures
# =============================================================================

def resolve_interface(node, logical_name):
    if logical_name == "wlan0":
        return f"{node.name}-wlan0"

    return logical_name


def start_packet_captures(
    scenario,
    stations,
    run_directory,
):
    packet_config = scenario["measurements"][
        "packet_capture"
    ]

    if (
        not scenario["measurements"]["enabled"]
        or not packet_config["enabled"]
    ):
        return []

    captures = []

    info("*** Starting packet captures\n")

    for node_config in scenario["nodes"]:
        identity = node_config["identity"]
        station = stations[identity]

        _, tcpdump_status = run_command(
            station,
            "command -v tcpdump",
            "check tcpdump",
            must_succeed=False,
        )

        if tcpdump_status != 0:
            info(
                f"*** WARNING: tcpdump is unavailable "
                f"on {station.name}\n"
            )
            continue

        for logical_interface in packet_config["interfaces"]:
            interface = resolve_interface(
                station,
                logical_interface,
            )

            _, interface_status = run_command(
                station,
                (
                    f"ip link show dev "
                    f"{shlex.quote(interface)}"
                ),
                f"check capture interface {interface}",
                must_succeed=False,
            )

            if interface_status != 0:
                info(
                    f"*** WARNING: cannot capture "
                    f"{station.name}:{interface}; "
                    "interface not found\n"
                )
                continue

            filename = (
                f"{safe_name(identity)}."
                f"{safe_name(interface)}.pcap"
            )

            container_pcap = (
                f"{RESULTS_CONTAINER_DIR}/pcaps/"
                f"{filename}"
            )

            command = [
                "tcpdump",
                "-n",
                "-U",
                "-i",
                interface,
                "-s",
                str(packet_config["snaplen"]),
                "-w",
                container_pcap,
            ]

            if packet_config["immediate_mode"]:
                command.insert(1, "--immediate-mode")

            capture_filter = packet_config.get(
                "filter",
                "",
            ).strip()

            if capture_filter:
                command.extend(
                    shlex.split(capture_filter)
                )

            managed = start_managed_process(
                node=station,
                process_id=(
                    f"{identity}.tcpdump.{interface}"
                ),
                role="tcpdump",
                command=command,
                run_directory=run_directory,
                capture_stdout=False,
                capture_stderr=True,
            )

            captures.append(managed)

    # Give tcpdump time to initialize before the protocol starts.
    #time.sleep(0.5)

    #return captures
    if not captures:
        raise RuntimeError(
            "Packet capture is enabled, but no tcpdump "
            "process could be started."
        )
    # New version of capture 
    time.sleep(0.5)

    startup_errors = []

    for managed in captures:
        return_code = managed.process.poll()

        if return_code is None:
            continue

        finalize_managed_process(managed)

        stderr_content = ""

        try:
            stderr_content = Path(
                managed.stderr_path
            ).read_text(
                encoding="utf-8",
                errors="replace",
            ).strip()
        except OSError:
            stderr_content = "(stderr unavailable)"

        startup_errors.append(
            f"{managed.process_id} exited with code "
            f"{return_code}: {stderr_content}"
        )

    if startup_errors:
        # Stop captures that started correctly because the complete
        # measurement set could not be initialized.
        for managed in captures:
            if managed.process.poll() is None:
                try:
                    stop_managed_process(
                        managed,
                        timeout_seconds=2,
                    )
                except Exception:
                    pass

        raise RuntimeError(
            "One or more packet captures failed to start:\n"
            + "\n".join(startup_errors)
        )

    return captures 

# =============================================================================
# Interface counters
# =============================================================================

def collect_interface_counters(
    scenario,
    stations,
):
    counter_config = scenario["measurements"][
        "interface_counters"
    ]

    snapshot = {
        "timestamp": utc_now_iso(),
        "nodes": {},
    }

    if (
        not scenario["measurements"]["enabled"]
        or not counter_config["enabled"]
    ):
        return snapshot

    for node_config in scenario["nodes"]:
        identity = node_config["identity"]
        station = stations[identity]

        node_snapshot = {}

        for logical_interface in counter_config["interfaces"]:
            interface = resolve_interface(
                station,
                logical_interface,
            )

            interface_snapshot = {}

            for counter_name in INTERFACE_COUNTER_NAMES:
                counter_path = (
                    f"/sys/class/net/{interface}/"
                    f"statistics/{counter_name}"
                )

                output, exit_code = run_command(
                    station,
                    f"cat {shlex.quote(counter_path)}",
                    f"read {interface} {counter_name}",
                    must_succeed=False,
                )

                if exit_code == 0:
                    try:
                        interface_snapshot[
                            counter_name
                        ] = int(output.strip())
                    except ValueError:
                        interface_snapshot[
                            counter_name
                        ] = None
                else:
                    interface_snapshot[
                        counter_name
                    ] = None

            node_snapshot[interface] = interface_snapshot

        snapshot["nodes"][identity] = node_snapshot

    return snapshot


def subtract_counter_snapshots(initial, final):
    differences = {}

    for identity, interfaces in final.get(
        "nodes",
        {},
    ).items():
        differences[identity] = {}

        for interface, counters in interfaces.items():
            differences[identity][interface] = {}

            initial_counters = (
                initial.get("nodes", {})
                .get(identity, {})
                .get(interface, {})
            )

            for name, final_value in counters.items():
                initial_value = initial_counters.get(name)

                if (
                    isinstance(initial_value, int)
                    and isinstance(final_value, int)
                ):
                    value = final_value - initial_value
                else:
                    value = None

                differences[identity][interface][
                    name
                ] = value

    return differences


class CounterSampler:
    def __init__(
        self,
        scenario,
        stations,
        output_path,
    ):
        self.scenario = scenario
        self.stations = stations
        self.output_path = Path(output_path)
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        config = self.scenario["measurements"][
            "interface_counters"
        ]

        if (
            not self.scenario["measurements"]["enabled"]
            or not config["enabled"]
        ):
            return

        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
        )
        self.thread.start()

    def _run(self):
        interval = self.scenario["measurements"][
            "sampling_interval_seconds"
        ]

        with self.output_path.open(
            "a",
            encoding="utf-8",
            buffering=1,
        ) as output:
            while not self.stop_event.is_set():
                snapshot = collect_interface_counters(
                    self.scenario,
                    self.stations,
                )

                output.write(
                    json.dumps(
                        snapshot,
                        ensure_ascii=False,
                    )
                    + "\n"
                )

                self.stop_event.wait(interval)

    def stop(self):
        self.stop_event.set()

        if self.thread is not None:
            self.thread.join(timeout=5)


# =============================================================================
# BATMAN-adv snapshots
# =============================================================================

def collect_batman_snapshot(
    scenario,
    stations,
    output_path,
):
    batman_config = scenario["measurements"]["batman"]

    if (
        not scenario["measurements"]["enabled"]
        or not batman_config["enabled"]
    ):
        return

    output_path = Path(output_path)

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as output:
        output.write(
            f"timestamp={utc_now_iso()}\n"
        )

        for node_config in scenario["nodes"]:
            identity = node_config["identity"]
            station = stations[identity]

            output.write(
                "\n"
                + "=" * 80
                + "\n"
                + f"node={identity} "
                + f"container={station.name}\n"
            )

            commands = [
                ("interfaces", "batctl if"),
                (
                    "neighbors",
                    "batctl neighbors 2>/dev/null "
                    "|| batctl n 2>/dev/null",
                ),
                (
                    "originators",
                    "batctl originators 2>/dev/null "
                    "|| batctl o 2>/dev/null",
                ),
                (
                    "statistics",
                    "batctl statistics 2>/dev/null "
                    "|| true",
                ),
            ]

            for title, command in commands:
                command_output, _ = run_command(
                    station,
                    command,
                    f"collect BATMAN {title}",
                    must_succeed=False,
                )

                output.write(
                    f"\n--- {title} ---\n"
                )

                output.write(
                    command_output
                    if command_output
                    else "(unavailable)"
                )

                output.write("\n")


# =============================================================================
# Experiment modes
# =============================================================================

def run_idle_mode(scenario):
    duration = scenario["experiment"]["duration_seconds"]

    info(
        "*** No protocol binaries will be executed.\n"
    )
    info(
        f"*** Keeping the topology idle for "
        f"{duration} seconds.\n"
    )

    deadline = time.monotonic() + duration

    while time.monotonic() < deadline:
        time.sleep(
            min(0.5, deadline - time.monotonic())
        )


def run_protocol_experiment(
    scenario,
    stations,
    role_paths,
    run_directory,
    process_registry,
):
    receiver_processes = []
    sender_processes = []

    shutdown_timeout = scenario["termination"][
        "shutdown_timeout_seconds"
    ]

    try:
        receiver_processes = start_receivers(
            scenario,
            stations,
            role_paths,
            run_directory,
            process_registry,
        )

        monitor_protocol_processes(
            sender_processes,
            receiver_processes,
            scenario,
        )
        wait_for_receivers(
            receiver_processes,
            scenario,
        )

        sender_processes = start_senders(
            scenario,
            stations,
            role_paths,
            run_directory,
        )

       # wait_for_senders(
       #     sender_processes,
       #     scenario,
       # )

    finally:
        for managed in sender_processes:
            stop_managed_process(
                managed,
                shutdown_timeout,
            )

        for managed in receiver_processes:
            stop_managed_process(
                managed,
                shutdown_timeout,
            )

    #return receiver_processes + sender_processes


# =============================================================================
# Main topology lifecycle
# =============================================================================

def topology(
    scenario,
    open_cli=False,
    enable_telemetry=False,
):
    setLogLevel("info")

    run_directory = create_result_directory(
        scenario
    )

    binary_status = inspect_host_binaries(
        scenario
    )

    summary = {
        "experiment_id": scenario["experiment"]["id"],
        "scenario_file": scenario["_scenario_file"],
        "started_at": utc_now_iso(),
        "finished_at": None,
        "status": "running",
        "mode": None,
        "results_directory": str(run_directory),
        "binary_status": binary_status,
        "missing_container_binaries": [],
        "processes": [],
        "counter_differences": {},
        "warnings": [],
        "error": None,
    }

    write_json(
        run_directory / "metadata.json",
        summary,
    )

    info(
        f"*** Results directory: {run_directory}\n"
    )

    net = None
    captures = []
    counter_sampler = None
    initial_counters = None
    final_counters = None
    protocol_processes = []

    try:
        net = create_network(scenario)

        stations = create_stations(
            net,
            scenario,
            run_directory,
        )

        info("*** Configuring nodes\n")
        net.configureNodes()

        configure_adhoc_links(
            net,
            scenario,
            stations,
        )

        info("*** Starting network\n")
        net.start()

        configure_interfaces(
            scenario,
            stations,
        )

        print_topology_summary(
            scenario,
            stations,
        )

        if enable_telemetry:
            info("*** Starting position telemetry\n")

            telemetry(
                nodes=net.stations,
                single=True,
                data_type="position",
            )

        if open_cli:
            summary["mode"] = "cli"
            info("*** Starting Mininet-WiFi CLI\n")
            CLI(net)
            summary["status"] = "completed"
            return summary

        role_paths, container_missing = (
            inspect_container_binaries(
                scenario,
                stations,
            )
        )

        summary[
            "missing_container_binaries"
        ] = container_missing

        captures = start_packet_captures(
            scenario,
            stations,
            run_directory,
        )

        initial_counters = collect_interface_counters(
            scenario,
            stations,
        )

        write_json(
            run_directory
            / "counters"
            / "initial.json",
            initial_counters,
        )

        collect_batman_snapshot(
            scenario,
            stations,
            run_directory
            / "batman"
            / "initial.txt",
        )

        counter_sampler = CounterSampler(
            scenario,
            stations,
            run_directory
            / "counters"
            / "samples.jsonl",
        )

        counter_sampler.start()

        host_missing = missing_required_binaries(
            binary_status
        )

        missing_anywhere = (
            bool(host_missing)
            or bool(container_missing)
        )

        execution = scenario["execution"]
        policy = execution["missing_binaries"]

        if not execution["enabled"]:
            summary["mode"] = "idle"
            run_idle_mode(scenario)

        elif missing_anywhere and policy == "fail":
            raise RuntimeError(
                "Required protocol binaries are missing "
                "or not executable."
            )

        elif missing_anywhere and policy == "skip":
            summary["mode"] = "skip"

            info(
                "*** Protocol binaries are unavailable. "
                "Skipping protocol execution.\n"
            )

        elif missing_anywhere and policy == "idle":
            summary["mode"] = "idle"

            info(
                "*** Protocol binaries are unavailable. "
                "Entering idle mode.\n"
            )

            run_idle_mode(scenario)

        else:
            summary["mode"] = "protocol"

            #protocol_processes = (
                #run_protocol_experiment(
                    #scenario,
                    #stations,
                    #role_paths,
                    #run_directory,
                #)
            #)

            run_protocol_experiment(
                scenario,
                stations,
                role_paths,
                run_directory,
                protocol_processes,
            )

            summary["processes"] = [
                process_metadata(process)
                for process in protocol_processes
            ]

        summary["status"] = "completed"

    except KeyboardInterrupt:
        summary["status"] = "interrupted"
        summary["error"] = "Interrupted by user."
        raise

    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
        raise

    finally:
        shutdown_timeout = scenario["termination"][
            "shutdown_timeout_seconds"
        ]

        if counter_sampler is not None:
            counter_sampler.stop()

        for managed in protocol_processes:
            try:
                if managed.process.poll() is None:
                    stop_managed_process(
                        managed,
                        shutdown_timeout,
                    )
                elif managed.finished_at is None:
                    finalize_managed_process(managed)
            except Exception as exc:
                summary["warnings"].append(
                    f"Could not finalize "
                    f"{managed.process_id}: {exc}"
                )
        #for managed in protocol_processes:
        #    if managed.process.poll() is None:
        #        stop_managed_process(
        #            managed,
        #            shutdown_timeout,
        #        )

        if protocol_processes:
            summary["processes"] = [
                process_metadata(process)
                for process in protocol_processes
            ]

        if net is not None:
            try:
                final_counters = collect_interface_counters(
                    scenario,
                    stations,
                )

                write_json(
                    run_directory
                    / "counters"
                    / "final.json",
                    final_counters,
                )

                if initial_counters is not None:
                    summary["counter_differences"] = (
                        subtract_counter_snapshots(
                            initial_counters,
                            final_counters,
                        )
                    )

                collect_batman_snapshot(
                    scenario,
                    stations,
                    run_directory
                    / "batman"
                    / "final.txt",
                )

            except Exception as exc:
                summary["warnings"].append(
                    f"Final metrics collection failed: {exc}"
                )

        for capture in captures:
            try:
                stop_managed_process(
                    capture,
                    shutdown_timeout,
                )
            except Exception as exc:
                summary["warnings"].append(
                    f"Could not stop "
                    f"{capture.process_id}: {exc}"
                )

        if (
            scenario["measurements"]["wmediumd"][
                "capture_log"
            ]
        ):
            summary["warnings"].append(
                "wmediumd log capture was requested, but the "
                "current Containernet/Mininet-WiFi integration "
                "does not expose a stable per-experiment log "
                "path. PCAPs and interface/BATMAN counters were "
                "captured normally."
            )

        if net is not None:
            info("*** Stopping network\n")
            net.stop()

        summary["finished_at"] = utc_now_iso()

        write_json(
            run_directory / "summary.json",
            summary,
        )

        info(
            f"*** Results saved in: {run_directory}\n"
        )

    return summary


# =============================================================================
# Render and main
# =============================================================================

def render_scenario(scenario):
    rendered = copy.deepcopy(scenario)
    rendered.pop("_scenario_file", None)

    print(
        yaml.safe_dump(
            rendered,
            sort_keys=False,
            allow_unicode=True,
        ),
        end="",
    )


def main():
    arguments = parse_arguments()

    try:
        scenario = normalize_scenario(
            load_scenario(arguments.scenario)
        )

        validate_scenario(scenario)

    except (OSError, ValueError) as exc:
        print(
            f"Scenario error: {exc}",
            file=sys.stderr,
        )
        return 2

    if arguments.render:
        render_scenario(scenario)
        return 0

    if os.geteuid() != 0:
        print(
            "Containernet must run as root.",
            file=sys.stderr,
        )
        return 1

    exit_code = 0

    try:
        summary = topology(
            scenario,
            open_cli=arguments.cli,
            enable_telemetry=arguments.telemetry,
        )

        if summary["status"] == "failed":
            exit_code = 1

    except KeyboardInterrupt:
        info("\n*** Execution interrupted\n")
        exit_code = 130

    except Exception as exc:
        print(
            f"\nTestbed error: {exc}",
            file=sys.stderr,
        )
        exit_code = 1

    finally:
        print("\nCleaning the Mininet environment...")

        subprocess.run(
            ["mn", "-c"],
            check=False,
        )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())