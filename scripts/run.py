#!/usr/bin/env python3

import argparse
import copy
import ipaddress
import os
import posixpath
import re
import shlex
import subprocess
import sys
from pathlib import PurePosixPath

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "PyYAML is required to load scenario files.\n"
        "Install it with: python3 -m pip install PyYAML"
    ) from exc

from containernet.net import Containernet
from containernet.node import DockerSta
from mininet.log import info, setLogLevel
from mn_wifi.cli import CLI
from mn_wifi.link import adhoc, wmediumd
from mn_wifi.telemetry import telemetry
from mn_wifi.wmediumdConnector import interference


# ====== Project paths ======

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

# Host directory mounted inside every container.
BIN_HOST_DIR = os.path.join(PROJECT_ROOT, "bin")

DEFAULT_SCENARIO_PATH = os.path.join(
    SCRIPT_DIR,
    "scenario.example.yml",
)


# ====== Network defaults ======

BSSID_CELL = "02:11:22:33:44:55"

WLAN_MTU = 1500
BAT_MTU_DESIRED = 5000
MTU_FALLBACK = 1500

DEFAULT_NOISE_THRESHOLD = -91
DEFAULT_FADING_COEFFICIENT = 3

DEFAULT_NODE_MEMORY = "4g"
DEFAULT_GCS_MEMORY = "16g"
DEFAULT_NODE_RANGE = 25
DEFAULT_TX_POWER = 10

SUPPORTED_ROLES = {
    "sender",
    "receiver",
}

SUPPORTED_COMMUNICATION_MODES = {
    "unicast",
    "broadcast",
}

SUPPORTED_TERMINATION_CONDITIONS = {
    "senders_completed",
    "duration_elapsed",
}

CONTAINER_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
)

EXIT_CODE_MARKER = "__TESTBED_EXIT_CODE__="


# ====== Command-line arguments ======

def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Create a Containernet/Mininet-WiFi topology from "
            "a YAML scenario."
        ),
    )

    parser.add_argument(
        "scenario",
        nargs="?",
        default=DEFAULT_SCENARIO_PATH,
        help=(
            "Path to the scenario YAML file. "
            f"Default: {DEFAULT_SCENARIO_PATH}"
        ),
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
            "Open the Containernet CLI after configuring the topology."
        ),
    )

    parser.add_argument(
        "--telemetry",
        action="store_true",
        help=(
            "Start Mininet-WiFi position telemetry. "
            "This option is normally used together with --cli."
        ),
    )

    return parser.parse_args()


# ====== Scenario loading and normalization ======

def load_scenario(path):
    scenario_path = os.path.abspath(path)

    if not os.path.isfile(scenario_path):
        raise ValueError(
            f"Scenario file not found: {scenario_path}"
        )

    try:
        with open(scenario_path, "r", encoding="utf-8") as scenario_file:
            scenario = yaml.safe_load(scenario_file)
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Invalid YAML in scenario file {scenario_path}: {exc}"
        ) from exc

    if scenario is None:
        raise ValueError(
            f"Scenario file is empty: {scenario_path}"
        )

    if not isinstance(scenario, dict):
        raise ValueError(
            "The scenario root must be a YAML mapping."
        )

    scenario["_scenario_file"] = scenario_path

    return scenario


def deep_merge(base, override):
    """
    Recursively merge override into base.

    Dictionaries are merged recursively. Other values, including lists,
    replace the corresponding value in base.
    """

    result = copy.deepcopy(base)

    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result


def render_template_value(value, context):
    """
    Render strings used by node group templates.

    Available variables:

      {group}
      {index}
      {ordinal}

    Examples:

      identity: "drone-{index}"
      container_name: "dr{index}"
      ip: "192.168.123.{index}/24"
    """

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
    except KeyError as exc:
        raise ValueError(
            f"Unknown template variable {exc} in value: {value!r}"
        ) from exc
    except ValueError as exc:
        raise ValueError(
            f"Invalid template expression in value: {value!r}"
        ) from exc

    if rendered == value:
        return rendered

    # Recover numeric and boolean YAML scalars when the rendered template
    # consists entirely of such a value. Addresses and names remain strings.
    try:
        parsed = yaml.safe_load(rendered)
    except yaml.YAMLError:
        return rendered

    if isinstance(parsed, (int, float, bool)) or parsed is None:
        return parsed

    return rendered


def normalize_group_overrides(raw_overrides, group_name):
    """
    Convert node group overrides into a dictionary indexed by node index.

    Supported list format:

      overrides:
        - index: 2
          roles:
            - sender

    Supported mapping format:

      overrides:
        2:
          roles:
            - sender
    """

    if raw_overrides is None:
        return {}

    normalized = {}

    if isinstance(raw_overrides, dict):
        items = raw_overrides.items()

        for raw_index, override in items:
            try:
                index = int(raw_index)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"node_groups[{group_name}].overrides has an "
                    f"invalid index: {raw_index!r}"
                ) from exc

            if not isinstance(override, dict):
                raise ValueError(
                    f"Override {index} in node group {group_name!r} "
                    "must be a mapping."
                )

            normalized[index] = copy.deepcopy(override)

        return normalized

    if isinstance(raw_overrides, list):
        for position, override in enumerate(raw_overrides):
            if not isinstance(override, dict):
                raise ValueError(
                    f"Override #{position + 1} in node group "
                    f"{group_name!r} must be a mapping."
                )

            if "index" not in override:
                raise ValueError(
                    f"Override #{position + 1} in node group "
                    f"{group_name!r} must contain an index."
                )

            try:
                index = int(override["index"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid override index in node group "
                    f"{group_name!r}: {override['index']!r}"
                ) from exc

            override_without_index = copy.deepcopy(override)
            override_without_index.pop("index", None)

            normalized[index] = override_without_index

        return normalized

    raise ValueError(
        f"node_groups[{group_name}].overrides must be "
        "a mapping or a list."
    )


def expand_node_groups(scenario):
    """
    Expand node_groups into the regular nodes list.

    Example:

      node_groups:
        - name: drones
          count: 3
          start_index: 1
          template:
            identity: "drone-{index}"
            container_name: "dr{index}"
            type: "drone"
            ip: "192.168.123.{index}/24"
            roles:
              - receiver
            position:
              x: "{index}"
              y: 20
              z: 0
          overrides:
            - index: 1
              roles:
                - sender
    """

    explicit_nodes = scenario.get("nodes") or []
    node_groups = scenario.get("node_groups") or []

    if not isinstance(explicit_nodes, list):
        raise ValueError("nodes must be a list.")

    if not isinstance(node_groups, list):
        raise ValueError("node_groups must be a list.")

    expanded_nodes = copy.deepcopy(explicit_nodes)

    for group_position, group in enumerate(node_groups):
        if not isinstance(group, dict):
            raise ValueError(
                f"node_groups[{group_position}] must be a mapping."
            )

        group_name = group.get("name")

        if not isinstance(group_name, str) or not group_name.strip():
            raise ValueError(
                f"node_groups[{group_position}].name must be "
                "a non-empty string."
            )

        count = group.get("count")

        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(
                f"node group {group_name!r} must have a positive "
                "integer count."
            )

        start_index = group.get("start_index", 1)

        if (
            not isinstance(start_index, int)
            or isinstance(start_index, bool)
            or start_index < 0
        ):
            raise ValueError(
                f"node group {group_name!r} must have a non-negative "
                "integer start_index."
            )

        template = group.get("template")

        if not isinstance(template, dict):
            raise ValueError(
                f"node group {group_name!r} must contain a "
                "template mapping."
            )

        overrides = normalize_group_overrides(
            group.get("overrides"),
            group_name,
        )

        valid_indexes = set(
            range(start_index, start_index + count)
        )

        invalid_override_indexes = (
            set(overrides.keys()) - valid_indexes
        )

        if invalid_override_indexes:
            invalid_text = ", ".join(
                str(index)
                for index in sorted(invalid_override_indexes)
            )
            raise ValueError(
                f"node group {group_name!r} contains overrides "
                f"outside its generated index range: {invalid_text}"
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
                rendered_override = render_template_value(
                    copy.deepcopy(overrides[index]),
                    context,
                )
                node = deep_merge(node, rendered_override)

            expanded_nodes.append(node)

    normalized = copy.deepcopy(scenario)
    normalized["nodes"] = expanded_nodes
    normalized.pop("node_groups", None)

    return normalized


def apply_scenario_defaults(scenario):
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
    containers.setdefault("sender_binary", "./sender")
    containers.setdefault("receiver_binary", "./receiver")

    network = normalized.setdefault("network", {})
    network.setdefault("interface", "bat0")
    network.setdefault("subnet", "192.168.123.0/24")
    network.setdefault(
        "noise_threshold",
        DEFAULT_NOISE_THRESHOLD,
    )
    network.setdefault(
        "fading_coefficient",
        DEFAULT_FADING_COEFFICIENT,
    )

    wireless = network.setdefault("wireless", {})
    wireless.setdefault("ssid", "adhocNet")
    wireless.setdefault("mode", "g")
    wireless.setdefault("channel", 5)
    wireless.setdefault("bssid", BSSID_CELL)
    wireless.setdefault("ht_cap", "HT40+")

    propagation = network.setdefault("propagation", {})
    propagation.setdefault("model", "logDistance")
    propagation.setdefault("exponent", 3.5)

    network.setdefault("link", {})

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
    transmission.setdefault("interval_ms", 1000)

    readiness = normalized.setdefault("readiness", {})
    readiness.setdefault("expected_output", "READY")
    readiness.setdefault("timeout_seconds", 10)

    termination = normalized.setdefault("termination", {})
    termination.setdefault("condition", "senders_completed")
    termination.setdefault("shutdown_timeout_seconds", 5)

    results = normalized.setdefault("results", {})
    results.setdefault("directory", "./logs")

    for node in normalized.get("nodes", []):
        if not isinstance(node, dict):
            continue

        node.setdefault("type", "drone")
        node.setdefault("roles", [])
        node.setdefault(
            "image",
            containers["image"],
        )
        node.setdefault(
            "memory",
            (
                DEFAULT_GCS_MEMORY
                if node.get("type") == "gcs"
                else DEFAULT_NODE_MEMORY
            ),
        )
        node.setdefault("range", DEFAULT_NODE_RANGE)
        node.setdefault("txpower", DEFAULT_TX_POWER)

    return normalized


def normalize_scenario(scenario):
    normalized = expand_node_groups(scenario)
    normalized = apply_scenario_defaults(normalized)
    return normalized


# ====== Scenario validation ======

def require_mapping(parent, key, path):
    value = parent.get(key)

    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a mapping.")

    return value


def require_non_empty_string(parent, key, path):
    value = parent.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string.")

    return value


def require_positive_integer(parent, key, path):
    value = parent.get(key)

    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{path} must be a positive integer.")

    return value


def require_non_negative_number(parent, key, path):
    value = parent.get(key)

    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError(
            f"{path} must be a non-negative number."
        )

    return value


def validate_binary_path(binary, path):
    if not isinstance(binary, str) or not binary.strip():
        raise ValueError(f"{path} must be a non-empty string.")

    pure_path = PurePosixPath(binary)

    if pure_path.is_absolute():
        raise ValueError(
            f"{path} must be relative to containers."
            "binaries_directory."
        )

    if ".." in pure_path.parts:
        raise ValueError(
            f"{path} must not contain '..'."
        )


def validate_position(position, node_identity):
    if not isinstance(position, dict):
        raise ValueError(
            f"Node {node_identity!r} must contain a position mapping."
        )

    for axis in ("x", "y", "z"):
        value = position.get(axis)

        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
        ):
            raise ValueError(
                f"Node {node_identity!r} position.{axis} "
                "must be numeric."
            )


def validate_nodes(scenario, subnet):
    nodes = scenario.get("nodes")

    if not isinstance(nodes, list) or not nodes:
        raise ValueError(
            "The scenario must contain at least one node."
        )

    identities = set()
    container_names = set()
    node_addresses = set()
    nodes_by_identity = {}

    for index, node in enumerate(nodes):
        path = f"nodes[{index}]"

        if not isinstance(node, dict):
            raise ValueError(f"{path} must be a mapping.")

        identity = require_non_empty_string(
            node,
            "identity",
            f"{path}.identity",
        )

        container_name = require_non_empty_string(
            node,
            "container_name",
            f"{path}.container_name",
        )

        if identity in identities:
            raise ValueError(
                f"Duplicate node identity: {identity!r}"
            )

        if container_name in container_names:
            raise ValueError(
                f"Duplicate container name: {container_name!r}"
            )

        if not CONTAINER_NAME_PATTERN.fullmatch(container_name):
            raise ValueError(
                f"Invalid container name {container_name!r}. "
                "Use letters, numbers, periods, underscores or hyphens."
            )

        wireless_interface = f"{container_name}-wlan0"

        if len(wireless_interface) > 15:
            raise ValueError(
                f"Generated wireless interface "
                f"{wireless_interface!r} exceeds Linux's 15-character "
                "interface name limit. Use a shorter container_name."
            )

        identities.add(identity)
        container_names.add(container_name)

        ip_value = require_non_empty_string(
            node,
            "ip",
            f"{path}.ip",
        )

        try:
            node_interface = ipaddress.ip_interface(ip_value)
        except ValueError as exc:
            raise ValueError(
                f"Invalid IP address for node {identity!r}: {ip_value}"
            ) from exc

        if node_interface.version != subnet.version:
            raise ValueError(
                f"Node {identity!r} and network subnet use "
                "different IP versions."
            )

        if node_interface.ip not in subnet:
            raise ValueError(
                f"Node {identity!r} address {node_interface.ip} "
                f"is outside subnet {subnet}."
            )

        if node_interface.ip in node_addresses:
            raise ValueError(
                f"Duplicate node IP address: {node_interface.ip}"
            )

        node_addresses.add(node_interface.ip)

        roles = node.get("roles")

        if not isinstance(roles, list):
            raise ValueError(
                f"{path}.roles must be a list."
            )

        invalid_roles = set(roles) - SUPPORTED_ROLES

        if invalid_roles:
            raise ValueError(
                f"Node {identity!r} has unsupported roles: "
                f"{', '.join(sorted(invalid_roles))}"
            )

        if len(roles) != len(set(roles)):
            raise ValueError(
                f"Node {identity!r} contains duplicate roles."
            )

        validate_position(
            node.get("position"),
            identity,
        )

        require_non_empty_string(
            node,
            "image",
            f"{path}.image",
        )

        require_non_empty_string(
            node,
            "memory",
            f"{path}.memory",
        )

        require_non_negative_number(
            node,
            "range",
            f"{path}.range",
        )

        require_non_negative_number(
            node,
            "txpower",
            f"{path}.txpower",
        )

        nodes_by_identity[identity] = node

    return nodes_by_identity


def validate_communication(scenario, nodes_by_identity):
    communication = scenario["communication"]

    mode = require_non_empty_string(
        communication,
        "mode",
        "communication.mode",
    )

    if mode not in SUPPORTED_COMMUNICATION_MODES:
        raise ValueError(
            f"Unsupported communication mode: {mode!r}. "
            f"Supported values: "
            f"{', '.join(sorted(SUPPORTED_COMMUNICATION_MODES))}"
        )

    port = require_positive_integer(
        communication,
        "port",
        "communication.port",
    )

    if port > 65535:
        raise ValueError(
            "communication.port must be between 1 and 65535."
        )

    transmission = require_mapping(
        communication,
        "transmission",
        "communication.transmission",
    )

    require_positive_integer(
        transmission,
        "count",
        "communication.transmission.count",
    )

    require_non_negative_number(
        transmission,
        "interval_ms",
        "communication.transmission.interval_ms",
    )

    flows = communication.get("flows")
    broadcasts = communication.get("broadcasts")

    if not isinstance(flows, list):
        raise ValueError(
            "communication.flows must be a list."
        )

    if not isinstance(broadcasts, list):
        raise ValueError(
            "communication.broadcasts must be a list."
        )

    for index, flow in enumerate(flows):
        path = f"communication.flows[{index}]"

        if not isinstance(flow, dict):
            raise ValueError(f"{path} must be a mapping.")

        source = require_non_empty_string(
            flow,
            "source",
            f"{path}.source",
        )

        destination = require_non_empty_string(
            flow,
            "destination",
            f"{path}.destination",
        )

        if source not in nodes_by_identity:
            raise ValueError(
                f"{path}.source references unknown node {source!r}."
            )

        if destination not in nodes_by_identity:
            raise ValueError(
                f"{path}.destination references unknown node "
                f"{destination!r}."
            )

        if "sender" not in nodes_by_identity[source]["roles"]:
            raise ValueError(
                f"Flow source {source!r} does not have the "
                "sender role."
            )

        if "receiver" not in nodes_by_identity[destination]["roles"]:
            raise ValueError(
                f"Flow destination {destination!r} does not have "
                "the receiver role."
            )

    if mode == "unicast" and not flows:
        raise ValueError(
            "Unicast communication requires at least one flow."
        )

    for index, broadcast in enumerate(broadcasts):
        path = f"communication.broadcasts[{index}]"

        if isinstance(broadcast, str):
            source = broadcast
        elif isinstance(broadcast, dict):
            source = require_non_empty_string(
                broadcast,
                "source",
                f"{path}.source",
            )
        else:
            raise ValueError(
                f"{path} must be a node identity string or mapping."
            )

        if source not in nodes_by_identity:
            raise ValueError(
                f"{path} references unknown node {source!r}."
            )

        if "sender" not in nodes_by_identity[source]["roles"]:
            raise ValueError(
                f"Broadcast source {source!r} does not have the "
                "sender role."
            )

    if mode == "broadcast" and not broadcasts:
        raise ValueError(
            "Broadcast communication requires at least one "
            "broadcast source."
        )


def validate_scenario(scenario):
    experiment = require_mapping(
        scenario,
        "experiment",
        "experiment",
    )

    require_non_empty_string(
        experiment,
        "id",
        "experiment.id",
    )

    seed = experiment.get("seed")

    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(
            "experiment.seed must be an integer."
        )

    require_positive_integer(
        experiment,
        "duration_seconds",
        "experiment.duration_seconds",
    )

    containers = require_mapping(
        scenario,
        "containers",
        "containers",
    )

    require_non_empty_string(
        containers,
        "image",
        "containers.image",
    )

    binary_directory = require_non_empty_string(
        containers,
        "binaries_directory",
        "containers.binaries_directory",
    )

    if not PurePosixPath(binary_directory).is_absolute():
        raise ValueError(
            "containers.binaries_directory must be an absolute "
            "container path."
        )

    validate_binary_path(
        containers.get("sender_binary"),
        "containers.sender_binary",
    )

    validate_binary_path(
        containers.get("receiver_binary"),
        "containers.receiver_binary",
    )

    network = require_mapping(
        scenario,
        "network",
        "network",
    )

    network_interface = require_non_empty_string(
        network,
        "interface",
        "network.interface",
    )

    # Mininet-WiFi's batman_adv protocol creates bat0. Supporting a
    # differently named BATMAN interface requires additional commands.
    if network_interface != "bat0":
        raise ValueError(
            "This first implementation currently supports only "
            "network.interface: bat0."
        )

    subnet_value = require_non_empty_string(
        network,
        "subnet",
        "network.subnet",
    )

    try:
        subnet = ipaddress.ip_network(
            subnet_value,
            strict=False,
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid network.subnet: {subnet_value}"
        ) from exc

    wireless = require_mapping(
        network,
        "wireless",
        "network.wireless",
    )

    require_non_empty_string(
        wireless,
        "ssid",
        "network.wireless.ssid",
    )

    require_non_empty_string(
        wireless,
        "mode",
        "network.wireless.mode",
    )

    channel = require_positive_integer(
        wireless,
        "channel",
        "network.wireless.channel",
    )

    if channel > 196:
        raise ValueError(
            "network.wireless.channel is outside the supported "
            "Wi-Fi channel range."
        )

    bssid = require_non_empty_string(
        wireless,
        "bssid",
        "network.wireless.bssid",
    )

    try:
        bssid_parts = bssid.split(":")
        valid_bssid = (
            len(bssid_parts) == 6
            and all(
                len(part) == 2
                and 0 <= int(part, 16) <= 255
                for part in bssid_parts
            )
        )
    except ValueError:
        valid_bssid = False

    if not valid_bssid:
        raise ValueError(
            f"Invalid network.wireless.bssid: {bssid!r}"
        )

    propagation = require_mapping(
        network,
        "propagation",
        "network.propagation",
    )

    require_non_empty_string(
        propagation,
        "model",
        "network.propagation.model",
    )

    require_non_negative_number(
        propagation,
        "exponent",
        "network.propagation.exponent",
    )

    noise_threshold = network.get("noise_threshold")

    if (
        not isinstance(noise_threshold, (int, float))
        or isinstance(noise_threshold, bool)
    ):
        raise ValueError(
            "network.noise_threshold must be numeric."
        )

    fading_coefficient = network.get("fading_coefficient")

    if (
        not isinstance(fading_coefficient, (int, float))
        or isinstance(fading_coefficient, bool)
    ):
        raise ValueError(
            "network.fading_coefficient must be numeric."
        )

    nodes_by_identity = validate_nodes(
        scenario,
        subnet,
    )

    mobility = require_mapping(
        scenario,
        "mobility",
        "mobility",
    )

    enabled = mobility.get("enabled")

    if not isinstance(enabled, bool):
        raise ValueError(
            "mobility.enabled must be true or false."
        )

    model = require_non_empty_string(
        mobility,
        "model",
        "mobility.model",
    )

    if enabled and model != "static":
        raise ValueError(
            "Dynamic mobility is not implemented in this first "
            "delivery. Use mobility.enabled: false and "
            "mobility.model: static."
        )

    validate_communication(
        scenario,
        nodes_by_identity,
    )

    readiness = require_mapping(
        scenario,
        "readiness",
        "readiness",
    )

    require_non_empty_string(
        readiness,
        "expected_output",
        "readiness.expected_output",
    )

    require_positive_integer(
        readiness,
        "timeout_seconds",
        "readiness.timeout_seconds",
    )

    termination = require_mapping(
        scenario,
        "termination",
        "termination",
    )

    condition = require_non_empty_string(
        termination,
        "condition",
        "termination.condition",
    )

    if condition not in SUPPORTED_TERMINATION_CONDITIONS:
        raise ValueError(
            f"Unsupported termination condition: {condition!r}."
        )

    require_positive_integer(
        termination,
        "shutdown_timeout_seconds",
        "termination.shutdown_timeout_seconds",
    )

    results = require_mapping(
        scenario,
        "results",
        "results",
    )

    require_non_empty_string(
        results,
        "directory",
        "results.directory",
    )


# ====== Binary validation ======

def relative_binary_path(binary):
    parts = [
        part
        for part in PurePosixPath(binary).parts
        if part not in ("", ".")
    ]

    return os.path.join(*parts)


def container_binary_path(container_directory, binary):
    parts = [
        part
        for part in PurePosixPath(binary).parts
        if part not in ("", ".")
    ]

    return posixpath.join(
        container_directory,
        *parts,
    )


def required_binary_roles(scenario):
    roles = set()

    for node in scenario["nodes"]:
        roles.update(node["roles"])

    return roles


def validate_environment(scenario):
    """
    Validate the host directory and binaries required by the scenario.

    The directory is mandatory because it is mounted in the containers.
    Missing binaries produce warnings in this first delivery, allowing
    topology and network development before the binaries are finished.
    """

    if not os.path.isdir(BIN_HOST_DIR):
        raise RuntimeError(
            f"Binary directory not found: {BIN_HOST_DIR}\n"
            "Create the bin/ directory and place sender/receiver "
            "inside it before starting the topology."
        )

    containers = scenario["containers"]
    roles = required_binary_roles(scenario)

    binaries = []

    if "sender" in roles:
        binaries.append(
            (
                "sender",
                containers["sender_binary"],
            )
        )

    if "receiver" in roles:
        binaries.append(
            (
                "receiver",
                containers["receiver_binary"],
            )
        )

    for role, configured_binary in binaries:
        host_binary = os.path.join(
            BIN_HOST_DIR,
            relative_binary_path(configured_binary),
        )

        if not os.path.isfile(host_binary):
            info(
                f"*** WARNING: {role} binary not found: "
                f"{host_binary}\n"
            )
        elif not os.access(host_binary, os.X_OK):
            info(
                f"*** WARNING: {role} binary is not executable: "
                f"{host_binary}\n"
            )


# ====== Commands executed inside containers ======

def run(
    node,
    command,
    description="",
    must_succeed=True,
):
    """
    Run a command inside a node and retrieve its exit code.

    Returns:
        tuple[str, int]: command output and exit code.
    """

    wrapped_command = (
        f"{command}\n"
        "testbed_exit_code=$?\n"
        f"printf '\\n{EXIT_CODE_MARKER}%s\\n' "
        "\"$testbed_exit_code\""
    )

    raw_output = node.cmd(
        f"sh -c {shlex.quote(wrapped_command)}"
    )

    output = raw_output or ""
    exit_code = None

    lines = output.splitlines()
    visible_lines = []

    for line in lines:
        if line.startswith(EXIT_CODE_MARKER):
            try:
                exit_code = int(
                    line[len(EXIT_CODE_MARKER):]
                )
            except ValueError:
                exit_code = None
        else:
            visible_lines.append(line)

    clean_output = "\n".join(visible_lines).strip()

    if exit_code is None:
        message = (
            f"Could not determine command exit code on {node.name}: "
            f"{description or command}"
        )

        if must_succeed:
            raise RuntimeError(message)

        info(f"*** WARNING: {message}\n")
        return clean_output, -1

    if exit_code != 0:
        info(
            f"\n*** ERROR on {node.name}: "
            f"{description or command}\n"
        )
        info(f"*** Command: {command}\n")
        info(f"*** Exit code: {exit_code}\n")

        if clean_output:
            info(f"*** Output:\n{clean_output}\n")

        if must_succeed:
            raise RuntimeError(
                f"Command failed on {node.name}: "
                f"{description or command} "
                f"(exit code {exit_code})"
            )

        info(
            f"*** WARNING: continuing despite the error "
            f"on {node.name}\n"
        )

    return clean_output, exit_code


def ensure_iface_exists(node, interface):
    run(
        node,
        f"ip link show dev {shlex.quote(interface)}",
        f"check whether {interface} exists",
    )


def force_adhoc_cell(
    node,
    interface,
    ssid,
    bssid,
    channel,
):
    quoted_interface = shlex.quote(interface)

    run(
        node,
        "command -v iwconfig",
        "check whether iwconfig is installed",
    )

    run(
        node,
        f"ip link set dev {quoted_interface} down",
        f"bring {interface} down",
        must_succeed=False,
    )

    run(
        node,
        (
            f"iwconfig {quoted_interface} "
            f"mode ad-hoc "
            f"essid {shlex.quote(ssid)} "
            f"ap {shlex.quote(bssid)} "
            f"channel {int(channel)}"
        ),
        f"configure ad-hoc mode on {interface}",
    )

    run(
        node,
        f"ip link set dev {quoted_interface} up",
        f"bring {interface} up",
    )


def set_mtu_required(node, interface, mtu):
    ensure_iface_exists(node, interface)

    run(
        node,
        f"ip link set dev {shlex.quote(interface)} mtu {int(mtu)}",
        f"set MTU {mtu} on {interface}",
    )


def set_mtu_tolerant(
    node,
    interface,
    desired_mtu,
    fallback_mtu,
):
    ensure_iface_exists(node, interface)

    _, exit_code = run(
        node,
        (
            f"ip link set dev {shlex.quote(interface)} "
            f"mtu {int(desired_mtu)}"
        ),
        f"set MTU {desired_mtu} on {interface}",
        must_succeed=False,
    )

    if exit_code != 0:
        info(
            f"*** {node.name}: {interface} did not accept "
            f"MTU {desired_mtu}. Using MTU {fallback_mtu}.\n"
        )

        run(
            node,
            (
                f"ip link set dev {shlex.quote(interface)} "
                f"mtu {int(fallback_mtu)}"
            ),
            f"set fallback MTU {fallback_mtu} on {interface}",
        )


def assign_interface_ip(
    node,
    interface,
    ip_cidr,
):
    ensure_iface_exists(node, interface)

    run(
        node,
        f"ip link set dev {shlex.quote(interface)} up",
        f"bring {interface} up",
    )

    run(
        node,
        (
            f"ip addr flush dev {shlex.quote(interface)} && "
            f"ip addr add {shlex.quote(ip_cidr)} "
            f"dev {shlex.quote(interface)}"
        ),
        f"assign {ip_cidr} to {interface}",
    )


# ====== Topology helpers ======

def generate_mac(index):
    """
    Generate a deterministic locally administered MAC address.
    """

    if index < 1 or index >= (1 << 40):
        raise ValueError(
            f"Cannot generate MAC address for node index {index}."
        )

    return "02:{:02x}:{:02x}:{:02x}:{:02x}:{:02x}".format(
        (index >> 32) & 0xFF,
        (index >> 24) & 0xFF,
        (index >> 16) & 0xFF,
        (index >> 8) & 0xFF,
        index & 0xFF,
    )


def generate_management_ip(index):
    """
    Generate the internal Mininet-WiFi address.

    The operational experiment address is assigned separately to bat0.
    """

    management_network = ipaddress.ip_network(
        "10.0.0.0/8"
    )

    if index < 1 or index >= management_network.num_addresses - 1:
        raise ValueError(
            f"Cannot generate management IP for node index {index}."
        )

    address = management_network.network_address + index
    return f"{address}/{management_network.prefixlen}"


def format_position(position):
    return "{},{},{}".format(
        position["x"],
        position["y"],
        position["z"],
    )


def create_network(scenario):
    network = scenario["network"]
    propagation = network["propagation"]

    net = Containernet(
        link=wmediumd,
        wmediumd_mode=interference,
        noise_th=network["noise_threshold"],
        fading_cof=network["fading_coefficient"],
    )

    net.setPropagationModel(
        model=propagation["model"],
        exp=propagation["exponent"],
    )

    return net


def create_stations(
    net,
    scenario,
    binary_volume,
):
    stations = {}

    info("*** Adding Docker stations\n")

    for index, node_config in enumerate(
        scenario["nodes"],
        start=1,
    ):
        identity = node_config["identity"]
        container_name = node_config["container_name"]

        station_arguments = {
            "cls": DockerSta,
            "mac": generate_mac(index),
            "ip": generate_management_ip(index),
            "position": format_position(
                node_config["position"]
            ),
            "dimage": node_config["image"],
            "privileged": True,
            "mem_limit": node_config["memory"],
            "range": node_config["range"],
            "txpower": node_config["txpower"],
            "volumes": binary_volume,
        }

        info(
            f"*** Adding {identity} as container "
            f"{container_name}\n"
        )

        station = net.addStation(
            container_name,
            **station_arguments,
        )

        stations[identity] = station

    return stations


def configure_adhoc_links(
    net,
    scenario,
    stations,
):
    wireless = scenario["network"]["wireless"]

    info("*** Creating BATMAN-adv ad-hoc links\n")

    for node_config in scenario["nodes"]:
        identity = node_config["identity"]
        station = stations[identity]
        wireless_interface = f"{station.name}-wlan0"

        link_arguments = {
            "cls": adhoc,
            "intf": wireless_interface,
            "ssid": wireless["ssid"],
            "proto": "batman_adv",
            "mode": wireless["mode"],
            "channel": wireless["channel"],
        }

        ht_cap = wireless.get("ht_cap")

        if ht_cap:
            link_arguments["ht_cap"] = ht_cap

        net.addLink(
            station,
            **link_arguments,
        )


def check_mounted_binaries(
    scenario,
    stations,
):
    containers = scenario["containers"]
    container_directory = containers["binaries_directory"]
    roles = required_binary_roles(scenario)

    info("*** Checking mounted binary directory\n")

    for node_config in scenario["nodes"]:
        station = stations[node_config["identity"]]

        run(
            station,
            f"test -d {shlex.quote(container_directory)}",
            "check the mounted binary directory",
        )

    binaries = []

    if "sender" in roles:
        binaries.append(
            (
                "sender",
                container_binary_path(
                    container_directory,
                    containers["sender_binary"],
                ),
            )
        )

    if "receiver" in roles:
        binaries.append(
            (
                "receiver",
                container_binary_path(
                    container_directory,
                    containers["receiver_binary"],
                ),
            )
        )

    for node_config in scenario["nodes"]:
        station = stations[node_config["identity"]]

        for role in node_config["roles"]:
            binary = next(
                binary_path
                for binary_role, binary_path in binaries
                if binary_role == role
            )

            _, exit_code = run(
                station,
                f"test -x {shlex.quote(binary)}",
                f"check the {role} binary",
                must_succeed=False,
            )

            if exit_code != 0:
                info(
                    f"*** WARNING: {station.name} requires the "
                    f"{role} binary, but it is missing or not "
                    f"executable: {binary}\n"
                )


def configure_node_interfaces(
    scenario,
    stations,
):
    network = scenario["network"]
    wireless = network["wireless"]
    batman_interface = network["interface"]

    info("*** Checking WLAN interfaces\n")

    for node_config in scenario["nodes"]:
        station = stations[node_config["identity"]]
        wireless_interface = f"{station.name}-wlan0"

        ensure_iface_exists(
            station,
            wireless_interface,
        )

    info("*** Configuring the ad-hoc cell\n")

    for node_config in scenario["nodes"]:
        station = stations[node_config["identity"]]
        wireless_interface = f"{station.name}-wlan0"

        force_adhoc_cell(
            station,
            wireless_interface,
            wireless["ssid"],
            wireless["bssid"],
            wireless["channel"],
        )

    info("*** Configuring WLAN MTU\n")

    for node_config in scenario["nodes"]:
        station = stations[node_config["identity"]]
        wireless_interface = f"{station.name}-wlan0"

        set_mtu_required(
            station,
            wireless_interface,
            WLAN_MTU,
        )

    info(
        f"*** Configuring {batman_interface} MTU\n"
    )

    for node_config in scenario["nodes"]:
        station = stations[node_config["identity"]]

        set_mtu_tolerant(
            station,
            batman_interface,
            BAT_MTU_DESIRED,
            MTU_FALLBACK,
        )

    info(
        f"*** Assigning IP addresses to "
        f"{batman_interface}\n"
    )

    for node_config in scenario["nodes"]:
        station = stations[node_config["identity"]]

        assign_interface_ip(
            station,
            batman_interface,
            node_config["ip"],
        )


def print_topology_summary(
    scenario,
    stations,
):
    network_interface = scenario["network"]["interface"]

    info("\n*** Topology ready\n")
    info(
        "*** Identity             Container       "
        "Operational address        Roles\n"
    )
    info(
        "*** -------------------- ---------------- "
        "-------------------------- ----------------\n"
    )

    for node_config in scenario["nodes"]:
        identity = node_config["identity"]
        station = stations[identity]
        roles = ",".join(node_config["roles"]) or "-"

        info(
            f"*** {identity:<20} "
            f"{station.name:<16} "
            f"{node_config['ip']:<26} "
            f"{roles}\n"
        )

    info(
        f"*** Operational interface: "
        f"{network_interface}\n"
    )


def warn_about_deferred_link_configuration(scenario):
    link = scenario["network"].get("link") or {}

    configured_fields = [
        field
        for field in (
            "bandwidth_mbps",
            "delay_ms",
            "loss_percent",
        )
        if field in link
    ]

    if configured_fields:
        info(
            "*** NOTE: network.link bandwidth, delay and loss "
            "are present in the scenario but are not applied in "
            "this first delivery.\n"
        )


# ====== Topology lifecycle ======

def topology(
    scenario,
    open_cli=False,
    enable_telemetry=False,
):
    setLogLevel("info")
    validate_environment(scenario)

    container_directory = scenario["containers"][
        "binaries_directory"
    ]

    info(
        f"*** Scenario: {scenario['experiment']['id']}\n"
    )
    info(
        f"*** Scenario file: "
        f"{scenario['_scenario_file']}\n"
    )
    info(
        f"*** Mounting binaries: "
        f"{BIN_HOST_DIR} -> {container_directory}\n"
    )

    binary_volume = [
        f"{BIN_HOST_DIR}:{container_directory}:ro",
    ]

    net = None

    try:
        net = create_network(scenario)

        stations = create_stations(
            net,
            scenario,
            binary_volume,
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

        check_mounted_binaries(
            scenario,
            stations,
        )

        configure_node_interfaces(
            scenario,
            stations,
        )

        warn_about_deferred_link_configuration(
            scenario
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
            info("*** Running Containernet CLI\n")
            CLI(net)
        else:
            info(
                "*** Topology validation completed successfully.\n"
            )
            info(
                "*** The network will now be stopped because "
                "--cli was not specified.\n"
            )
            info(
                "*** Binary orchestration will be implemented "
                "in the next delivery.\n"
            )

    finally:
        if net is not None:
            info("*** Stopping network\n")
            net.stop()


# ====== Rendering ======

def scenario_for_rendering(scenario):
    rendered = copy.deepcopy(scenario)
    rendered.pop("_scenario_file", None)
    return rendered


def render_scenario(scenario):
    print(
        yaml.safe_dump(
            scenario_for_rendering(scenario),
            sort_keys=False,
            allow_unicode=True,
        ),
        end="",
    )


# ====== Main ======

def main():
    args = parse_arguments()

    try:
        scenario = load_scenario(args.scenario)
        scenario = normalize_scenario(scenario)
        validate_scenario(scenario)
    except (OSError, ValueError) as exc:
        print(
            f"Scenario error: {exc}",
            file=sys.stderr,
        )
        return 2

    if args.render:
        render_scenario(scenario)
        return 0

    if os.geteuid() != 0:
        print(
            "This command must normally run as root because "
            "Containernet and Mininet configure network namespaces.",
            file=sys.stderr,
        )
        return 1

    try:
        topology(
            scenario,
            open_cli=args.cli,
            enable_telemetry=args.telemetry,
        )
    except KeyboardInterrupt:
        info("\n*** Execution interrupted by the user\n")
        return 130
    except Exception as exc:
        print(
            f"\nTestbed error: {exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        print("\nCleaning the Mininet environment...")

        subprocess.run(
            ["mn", "-c"],
            check=False,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())