from __future__ import annotations

import ipaddress
import re
import subprocess
from dataclasses import dataclass
from typing import Any

_EXIT_MARKER = "__TESTBED_EXIT_CODE__"
_EXIT_PATTERN = re.compile(rf"{_EXIT_MARKER}:(-?\d+)")


class NodeCommandError(RuntimeError):
    """Raised when a command executed on a Containernet node fails."""

    def __init__(self, node_name: str, operation: str, command: str, exit_code: int | None, output: str) -> None:
        self.node_name = node_name
        self.operation = operation
        self.command = command
        self.exit_code = exit_code
        self.output = output
        super().__init__(
            f"node {node_name}: {operation} failed "
            f"(command={command!r}, exit_code={exit_code}): {output.strip()}"
        )


class NetworkValidationError(RuntimeError):
    """Raised when a post-configuration network health check fails."""


@dataclass
class NetworkContext:
    net: Any
    nodes: dict[str, Any]

    def stop(self) -> None:
        try:
            if self.net is not None:
                self.net.stop()
        except Exception:
            pass


def run_node_command(node: Any, operation: str, command: str, check: bool = True) -> tuple[int | None, str]:
    """Run ``command`` on ``node`` and return its exit code and output.

    Mininet-WiFi/Containernet nodes only expose ``cmd()``, which returns the
    combined stdout/stderr without an exit code. We append a shell marker to
    recover the real exit code instead of silently swallowing failures.
    """
    output = node.cmd(f"{command}; echo {_EXIT_MARKER}:$?")
    match = _EXIT_PATTERN.search(output)
    exit_code = int(match.group(1)) if match else None
    cleaned_output = _EXIT_PATTERN.sub("", output).strip()

    if check and exit_code != 0:
        raise NodeCommandError(node.name, operation, command, exit_code, cleaned_output)

    return exit_code, cleaned_output


def channel_to_frequency(channel: int) -> int:
    """Convert a 2.4GHz Wi-Fi channel number to its center frequency in MHz."""
    if channel == 14:
        return 2484
    if 1 <= channel <= 13:
        return 2407 + channel * 5
    raise ValueError(f"unsupported 2.4GHz channel: {channel}")


def build_network(config: dict[str, Any]) -> NetworkContext:
    from containernet.net import Containernet
    from containernet.node import DockerSta
    from mn_wifi.link import adhoc, wmediumd
    from mn_wifi.wmediumdConnector import interference

    wireless = config["wireless"]
    net = Containernet(
        link=wmediumd,
        wmediumd_mode=interference,
        noise_th=wireless["noise_threshold_dbm"],
        fading_cof=wireless["fading_coefficient"],
    )
    net.setPropagationModel(
        model=wireless["propagation_model"],
        exp=wireless["propagation_exponent"],
    )

    volume = f"{config['binaries']['host_directory']}:{config['binaries']['container_directory']}:ro"
    nodes: dict[str, Any] = {}
    for index, node_cfg in enumerate(config["nodes"].values(), start=1):
        nodes[node_cfg["id"]] = net.addStation(
            node_cfg["container_name"],
            cls=DockerSta,
            mac=f"02:00:00:00:00:{index:02x}",
            ip=f"10.0.0.{index}/8",
            position=",".join(str(value) for value in node_cfg["position"]),
            dimage=node_cfg["image"],
            privileged=True,
            mem_limit=node_cfg["memory"],
            range=node_cfg["range"],
            txpower=node_cfg["txpower"],
            volumes=[volume],
        )

    net.configureNodes()
    for node_cfg in config["nodes"].values():
        station = nodes[node_cfg["id"]]
        # NOTE: proto is intentionally omitted here. Passing
        # proto="batman_adv" makes mn_wifi's manetRoutingProtocols configure
        # BATMAN-adv on its own, which duplicates (and can conflict with) the
        # explicit, validated configuration performed in _configure_node().
        net.addLink(
            station,
            cls=adhoc,
            intf=f"{node_cfg['container_name']}-wlan0",
            ssid=wireless["ssid"],
            mode=wireless["mode"],
            channel=wireless["channel"],
            ht_cap=wireless["ht_cap"],
        )

    net.start()
    try:
        for node_cfg in config["nodes"].values():
            _configure_node(nodes[node_cfg["id"]], node_cfg, wireless)
        validate_network(config, nodes)
    except Exception:
        try:
            net.stop()
        except Exception:
            pass
        raise

    return NetworkContext(net, nodes)


def _configure_node(node: Any, node_cfg: dict[str, Any], wireless: dict[str, Any]) -> None:
    wlan = f"{node_cfg['container_name']}-wlan0"
    interface = wireless["interface"]
    frequency = channel_to_frequency(wireless["channel"])
    subnet = ipaddress.ip_network(wireless["subnet"])

    run_node_command(node, "bring up wlan interface", f"ip link set {wlan} up")
    run_node_command(
        node,
        "join ad hoc network",
        f"iw dev {wlan} ibss join {wireless['ssid']} {frequency} "
        f"{wireless['ht_cap']} fixed-freq {wireless['bssid']}",
    )
    run_node_command(node, "load batman-adv kernel module", "modprobe batman-adv")
    _attach_wlan_to_batman(node, wlan, interface)
    run_node_command(node, "bring up bat0", f"ip link set {interface} up")
    run_node_command(node, "flush existing bat0 addresses", f"ip addr flush dev {interface}")
    run_node_command(node, "assign node address to bat0", f"ip addr add {node_cfg['ip']} dev {interface}")
    run_node_command(
        node,
        "add mesh subnet route via bat0",
        f"ip route replace {subnet} dev {interface}",
    )


def _attach_wlan_to_batman(node: Any, wlan: str, interface: str) -> None:
    # batctl's CLI changed between versions ("batctl if add" vs
    # "batctl meshif <mesh> interface add"). Try the modern syntax first and
    # fall back to the legacy one, but never hide the final failure.
    commands = [
        f"batctl meshif {interface} interface add {wlan}",
        f"batctl if add {wlan}",
    ]
    last_error: NodeCommandError | None = None
    for command in commands:
        try:
            run_node_command(node, "attach wlan interface to batman-adv", command)
            return
        except NodeCommandError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def validate_network(config: dict[str, Any], nodes: dict[str, Any]) -> None:
    """Validate essential network state before the protocol is executed.

    This intentionally fails loudly (instead of silently continuing with a
    broken mesh) so a experiment never reports success when receivers never
    actually got any packets.
    """
    wireless = config["wireless"]
    interface = wireless["interface"]
    subnet = ipaddress.ip_network(wireless["subnet"])

    for node_cfg in config["nodes"].values():
        node = nodes[node_cfg["id"]]
        wlan = f"{node_cfg['container_name']}-wlan0"

        _, wlan_state = run_node_command(node, "check wlan interface state", f"ip link show {wlan}")
        if not _has_flag(wlan_state, "UP"):
            raise NetworkValidationError(f"node {node.name}: interface {wlan} is not UP")

        _, bat_state = run_node_command(node, "check bat0 interface state", f"ip link show {interface}")
        if not _has_flag(bat_state, "UP"):
            raise NetworkValidationError(f"node {node.name}: interface {interface} is not UP")

        expected_ip = ipaddress.ip_interface(node_cfg["ip"]).ip
        _, addr_state = run_node_command(node, "check bat0 address", f"ip addr show {interface}")
        if not _has_address(addr_state, expected_ip):
            raise NetworkValidationError(
                f"node {node.name}: expected address {expected_ip} not found on {interface}"
            )

        _, route_state = run_node_command(node, "check mesh route", f"ip route show dev {interface}")
        if not _has_route_to_subnet(route_state, subnet):
            raise NetworkValidationError(
                f"node {node.name}: missing route to {subnet} via {interface}"
            )

    _validate_sender_reachability(config, nodes)


def _has_flag(ip_link_output: str, flag: str) -> bool:
    match = re.search(r"<([^>]*)>", ip_link_output)
    if not match:
        return False
    return flag in match.group(1).split(",")


def _has_address(ip_addr_output: str, expected_ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    for match in re.finditer(r"inet6?\s+([0-9a-fA-F:.]+)(?:/\d+)?", ip_addr_output):
        try:
            if ipaddress.ip_address(match.group(1)) == expected_ip:
                return True
        except ValueError:
            continue
    return False


def _has_route_to_subnet(ip_route_output: str, expected_subnet: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    for line in ip_route_output.splitlines():
        candidate = line.strip().split()[0] if line.strip() else ""
        try:
            if ipaddress.ip_network(candidate, strict=False) == expected_subnet:
                return True
        except ValueError:
            continue
    return False


def _validate_sender_reachability(config: dict[str, Any], nodes: dict[str, Any]) -> None:
    sender_id = config["protocol"]["sender"]
    sender_node = nodes[sender_id]

    for receiver_id in config["protocol"]["receivers"]:
        destination = config["nodes"][receiver_id]["address"]

        run_node_command(
            sender_node,
            f"check route from sender to receiver {receiver_id}",
            f"ip route get {destination}",
        )
        run_node_command(
            sender_node,
            f"ping receiver {receiver_id} over bat0",
            f"ping -c 1 -W 2 {destination}",
        )


def cleanup_mininet() -> None:
    subprocess.run(["mn", "-c"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
