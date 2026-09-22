from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "scripts" / "config.yml"
CONTAINER_BIN_DIR = "/opt/protocol/bin"
_NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PROTOCOL_UNICAST = "unicast"
PROTOCOL_BROADCAST = "broadcast"
SUPPORTED_PROTOCOL_MODES = {PROTOCOL_UNICAST, PROTOCOL_BROADCAST}


class ConfigError(ValueError):
    """Raised when scripts/config.yml is invalid."""


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string")
    return value.strip()


def _require_number(value: Any, path: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"{path} must be a number")
    return value


def _positive_float(value: Any, path: str) -> float:
    number = float(_require_number(value, path))
    if number <= 0:
        raise ConfigError(f"{path} must be greater than zero")
    return number


def _positive_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{path} must be a positive integer")
    return value


def _path_from_root(root: Path, value: Any, path: str) -> Path:
    raw = Path(_require_string(value, path))
    resolved = raw if raw.is_absolute() else root / raw
    return resolved.resolve()


def _executable(root: Path, value: str, path: str) -> dict[str, str]:
    host_path = _path_from_root(root, value, path)
    if not host_path.is_file() or not os.access(host_path, os.X_OK):
        raise ConfigError(f"{path} must point to an executable file: {host_path}")

    bin_dir = (root / "bin").resolve()
    try:
        relative = host_path.relative_to(bin_dir)
    except ValueError:
        raise ConfigError(f"{path} must be located under {bin_dir}") from None

    return {
        "host": str(host_path),
        "container": f"{CONTAINER_BIN_DIR}/{relative.as_posix()}",
    }


def _plain_ip(interface: str) -> str:
    return str(ipaddress.ip_interface(interface).ip)


def load_config(path: str | Path | None = None, root: str | Path | None = None) -> dict[str, Any]:
    project_root = Path(root).resolve() if root is not None else PROJECT_ROOT
    config_path = Path(path).resolve() if path is not None else CONFIG_PATH

    if not config_path.is_file():
        raise ConfigError(f"configuration file not found: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    data = _require_mapping(raw, "config")
    return normalize_config(data, project_root, config_path)


def normalize_config(data: dict[str, Any], root: Path, config_path: Path | None = None) -> dict[str, Any]:
    experiment = _require_mapping(data.get("experiment", {}), "experiment")
    protocol = _require_mapping(data.get("protocol", {}), "protocol")
    wireless = _require_mapping(data.get("wireless", {}), "wireless")
    output = _require_mapping(data.get("output", {}), "output")

    experiment_id = _require_string(experiment.get("id"), "experiment.id")
    if not _NODE_ID.match(experiment_id):
        raise ConfigError("experiment.id contains invalid characters")

    duration_seconds = _positive_float(
        experiment.get("duration_seconds", 60),
        "experiment.duration_seconds",
    )
    receiver_startup_seconds = _positive_float(
        experiment.get("receiver_startup_seconds", 1.0),
        "experiment.receiver_startup_seconds",
    )
    shutdown_timeout_seconds = _positive_float(
        experiment.get("shutdown_timeout_seconds", 5.0),
        "experiment.shutdown_timeout_seconds",
    )

    mode = _require_string(protocol.get("mode", "unicast"), "protocol.mode")
    if mode not in SUPPORTED_PROTOCOL_MODES:
        raise ConfigError("protocol.mode must be 'unicast' or 'broadcast'")

    port = _positive_int(protocol.get("port"), "protocol.port")
    if port > 65535:
        raise ConfigError("protocol.port must be between 1 and 65535")

    count = _positive_int(protocol.get("count"), "protocol.count")
    sender = _require_string(protocol.get("sender"), "protocol.sender")
    receivers = protocol.get("receivers")
    if not isinstance(receivers, list):
        raise ConfigError("protocol.receivers must be a list")
    receivers = [_require_string(item, "protocol.receivers[]") for item in receivers]
    if len(receivers) != len(set(receivers)):
        raise ConfigError("protocol.receivers must not contain duplicates")
    if sender in receivers:
        raise ConfigError("protocol.sender cannot also be listed in protocol.receivers")
    if mode == PROTOCOL_UNICAST and len(receivers) != 1:
        raise ConfigError("protocol.receivers must contain exactly one receiver in unicast mode")
    if mode == PROTOCOL_BROADCAST and not receivers:
        raise ConfigError("protocol.receivers must contain at least one receiver in broadcast mode")

    try:
        subnet = ipaddress.ip_network(_require_string(wireless.get("subnet"), "wireless.subnet"), strict=False)
    except ValueError as exc:
        raise ConfigError(f"wireless.subnet is invalid: {exc}") from exc
    if subnet.version != 4:
        raise ConfigError("wireless.subnet must be an IPv4 subnet")

    try:
        broadcast_ip = ipaddress.ip_address(
            _require_string(wireless.get("broadcast_ip", str(subnet.broadcast_address)), "wireless.broadcast_ip")
        )
    except ValueError as exc:
        raise ConfigError(f"wireless.broadcast_ip is invalid: {exc}") from exc
    if broadcast_ip != subnet.broadcast_address:
        raise ConfigError(
            "wireless.broadcast_ip must match the broadcast address derived from "
            f"wireless.subnet: got {broadcast_ip}, expected {subnet.broadcast_address}"
        )

    normalized_wireless = {
        "ssid": _require_string(wireless.get("ssid", "meshNet"), "wireless.ssid"),
        "mode": _require_string(wireless.get("mode", "g"), "wireless.mode"),
        "channel": _positive_int(wireless.get("channel", 5), "wireless.channel"),
        "ht_cap": _require_string(wireless.get("ht_cap", "HT40+"), "wireless.ht_cap"),
        "noise_threshold_dbm": float(_require_number(wireless.get("noise_threshold_dbm", -91), "wireless.noise_threshold_dbm")),
        "fading_coefficient": float(_require_number(wireless.get("fading_coefficient", 3), "wireless.fading_coefficient")),
        "propagation_model": _require_string(wireless.get("propagation_model", "logDistance"), "wireless.propagation_model"),
        "propagation_exponent": float(_require_number(wireless.get("propagation_exponent", 3.5), "wireless.propagation_exponent")),
        "subnet": str(subnet),
        "broadcast_ip": str(broadcast_ip),
        "interface": _require_string(wireless.get("interface", "bat0"), "wireless.interface"),
    }

    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ConfigError("nodes must be a non-empty list")

    normalized_nodes: dict[str, dict[str, Any]] = {}
    container_names: dict[str, str] = {}
    node_ips: dict[str, str] = {}
    for index, node_data in enumerate(nodes):
        node = _require_mapping(node_data, f"nodes[{index}]")
        name = _require_string(node.get("id"), f"nodes[{index}].id")
        if not _NODE_ID.match(name):
            raise ConfigError(f"nodes[{index}].id contains invalid characters")
        if name in normalized_nodes:
            raise ConfigError(f"duplicate node id: {name}")

        container_name = _require_string(
            node.get("container_name", name), f"nodes[{index}].container_name"
        )
        if not _NODE_ID.match(container_name):
            raise ConfigError(f"nodes[{index}].container_name contains invalid characters")
        if not re.search(r"\d", container_name):
            # Mininet-WiFi/BATMAN internally derives node numbering from the
            # station name (see manetRoutingProtocols.setIP), so a name
            # without any digit crashes with "list index out of range".
            raise ConfigError(f"nodes[{index}].container_name must contain at least one digit")
        if container_name in container_names:
            raise ConfigError(
                f"duplicate container_name: {container_name} "
                f"(nodes[{index}] and node {container_names[container_name]!r})"
            )
        container_names[container_name] = name

        wlan_name = f"{container_name}-wlan0"
        if len(wlan_name) > 15:
            raise ConfigError(
                f"nodes[{index}].container_name produces an interface name "
                f"longer than 15 characters: {wlan_name}"
            )

        ip = _require_string(node.get("ip"), f"nodes[{index}].ip")
        try:
            interface = ipaddress.ip_interface(ip)
        except ValueError as exc:
            raise ConfigError(f"nodes[{index}].ip is invalid: {exc}") from exc
        if interface.ip not in subnet:
            raise ConfigError(f"nodes[{index}].ip for node {name!r} ({interface.ip}) is outside wireless.subnet")
        if interface.ip in {subnet.network_address, subnet.broadcast_address}:
            raise ConfigError(
                f"nodes[{index}].ip for node {name!r} ({interface.ip}) "
                "cannot be the network or broadcast address"
            )
        ip_address = str(interface.ip)
        if ip_address in node_ips:
            raise ConfigError(
                f"duplicate node IP address: {ip_address} "
                f"(nodes[{index}] and node {node_ips[ip_address]!r})"
            )
        node_ips[ip_address] = name

        position = node.get("position")
        if not isinstance(position, list) or len(position) != 3:
            raise ConfigError(f"nodes[{index}].position must have three numeric values")
        position = [float(_require_number(value, f"nodes[{index}].position[]")) for value in position]

        normalized_nodes[name] = {
            "id": name,
            "container_name": container_name,
            "ip": ip,
            "address": _plain_ip(ip),
            "position": position,
            "image": _require_string(node.get("image", "drone:latest"), f"nodes[{index}].image"),
            "memory": _require_string(node.get("memory", "512m"), f"nodes[{index}].memory"),
            "txpower": float(_require_number(node.get("txpower", 10), f"nodes[{index}].txpower")),
            "range": float(_require_number(node.get("range", 25), f"nodes[{index}].range")),
        }

    if sender not in normalized_nodes:
        raise ConfigError(f"protocol.sender references unknown node: {sender}")
    for receiver in receivers:
        if receiver not in normalized_nodes:
            raise ConfigError(f"protocol.receivers[] references unknown node: {receiver}")

    binaries = _require_mapping(data.get("binaries", {}), "binaries")
    normalized_binaries = {
        "sender": _executable(root, binaries.get("sender", "bin/sender"), "binaries.sender"),
        "receiver": _executable(root, binaries.get("receiver", "bin/receiver"), "binaries.receiver"),
        "container_directory": CONTAINER_BIN_DIR,
        "host_directory": str((root / "bin").resolve()),
    }

    normalized_output = {
        "pcap": str(_path_from_root(root, output.get("pcap", f"logs/{experiment_id}.pcap"), "output.pcap")),
        "csv": str(_path_from_root(root, output.get("csv", f"logs/{experiment_id}.csv"), "output.csv")),
        "summary": str(_path_from_root(root, output.get("summary", f"logs/{experiment_id}-summary.json"), "output.summary")),
    }

    return {
        "root": str(root),
        "config_path": str(config_path) if config_path is not None else None,
        "experiment": {
            "id": experiment_id,
            "duration_seconds": duration_seconds,
            "receiver_startup_seconds": receiver_startup_seconds,
            "shutdown_timeout_seconds": shutdown_timeout_seconds,
        },
        "protocol": {
            "mode": mode,
            "port": port,
            "count": count,
            "sender": sender,
            "receivers": receivers,
        },
        "wireless": normalized_wireless,
        "nodes": normalized_nodes,
        "binaries": normalized_binaries,
        "output": normalized_output,
    }
