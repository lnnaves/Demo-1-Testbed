from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any


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

    try:
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
                txpower=node_cfg["txpower"],
                volumes=[volume],
            )

        net.configureNodes()
        for node_cfg in config["nodes"].values():
            station = nodes[node_cfg["id"]]
            net.addLink(
                station,
                cls=adhoc,
                intf=f"{node_cfg['container_name']}-wlan0",
                ssid=wireless["ssid"],
                proto="batman_adv",
                mode=wireless["mode"],
                channel=wireless["channel"],
                ht_cap=wireless["ht_cap"],
            )

        net.start()
        for node_cfg in config["nodes"].values():
            _configure_batman(
                nodes[node_cfg["id"]],
                wireless["batman_bridge_loop_avoidance"],
                wireless["batman_orig_interval_ms"],
            )
            _assign_application_ip(
                nodes[node_cfg["id"]],
                wireless["interface"],
                node_cfg["ip"],
            )

    except Exception:
        try:
            net.stop()
        except Exception:
            pass
        raise

    return NetworkContext(net, nodes)


_IP_ASSIGN_OK = "__IP_ASSIGN_OK__"
_PING_OK = "__PING_OK__"
_BATMAN_OK = "__BATMAN_OK__"
# Pause between readiness attempts (D8 in docs/DECISOES.md). Each ping still
# waits up to 1 s for a reply, so slow replies across the mesh are accepted.
_READY_POLL_SECONDS = 0.2
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _has_sentinel(output: str | None, sentinel: str) -> bool:
    # node.cmd() only returns the shell output, not an exit code, so success is
    # detected via a sentinel line that only appears if every command in the
    # chain succeeded. The shell may wrap it in terminal control sequences
    # (e.g. bash bracketed-paste on ubuntu:24.04), so it is matched as a whole
    # line anywhere in the output rather than as the last line.
    return sentinel in [line.strip() for line in (output or "").splitlines()]


def _configure_batman(node: Any, bridge_loop_avoidance: bool, orig_interval_ms: int) -> None:
    # Bridge Loop Avoidance is on by default and drops broadcasts (ARP
    # included) for ~30 s after start while it looks for other backbone
    # gateways. The scenario has no bridge to a LAN, so it only delays the mesh.
    output = node.cmd(
        f"batctl bl {1 if bridge_loop_avoidance else 0} && "
        f"batctl it {int(orig_interval_ms)} && "
        f"echo {_BATMAN_OK}"
    )
    if not _has_sentinel(output, _BATMAN_OK):
        node_name = getattr(node, "name", "<unknown>")
        raise RuntimeError(f"failed to configure BATMAN-adv on node {node_name!r}: {(output or '').strip()!r}")


def _assign_application_ip(node: Any, interface: str, ip_cidr: str) -> None:
    quoted_interface = shlex.quote(interface)
    quoted_ip = shlex.quote(ip_cidr)
    output = node.cmd(
        f"ip link set dev {quoted_interface} up && "
        f"ip addr flush dev {quoted_interface} && "
        f"ip addr add {quoted_ip} dev {quoted_interface} && "
        f"echo {_IP_ASSIGN_OK}"
    )
    if not _has_sentinel(output, _IP_ASSIGN_OK):
        node_name = getattr(node, "name", "<unknown>")
        raise RuntimeError(
            f"failed to assign application IP {ip_cidr} to {interface} on node "
            f"{node_name!r}: {(output or '').strip()!r}"
        )


def _ping(node: Any, address: str) -> bool:
    output = node.cmd(f"ping -c 1 -W 1 {shlex.quote(address)} >/dev/null 2>&1 && echo {_PING_OK}")
    return _has_sentinel(output, _PING_OK)


def wait_for_network(config: dict[str, Any], nodes: dict[str, Any]) -> float:
    """Block until the sender reaches every receiver over the mesh.

    BATMAN-adv needs a few seconds after start to learn routes; traffic sent
    before that is silently dropped while ARP is unresolved. Readiness is
    checked with ping from the sender, then the neighbour caches are flushed so
    the protocol does not inherit ARP entries created by this check. Returns
    the seconds spent waiting.
    """
    protocol = config["protocol"]
    timeout = config["experiment"]["network_ready_timeout_seconds"]
    sender = nodes[protocol["sender"]]
    pending = {name: config["nodes"][name]["address"] for name in protocol["receivers"]}

    start = time.monotonic()
    while True:
        for name, address in list(pending.items()):
            if _ping(sender, address):
                del pending[name]
        if not pending:
            break
        if time.monotonic() - start >= timeout:
            unreachable = ", ".join(f"{name} ({address})" for name, address in pending.items())
            raise RuntimeError(
                f"network not ready: {protocol['sender']} could not reach {unreachable} "
                f"within {timeout:g}s"
            )
        time.sleep(_READY_POLL_SECONDS)
    elapsed = time.monotonic() - start

    quoted_interface = shlex.quote(config["wireless"]["interface"])
    for node in nodes.values():
        node.cmd(f"ip neigh flush dev {quoted_interface}")
    return elapsed


def container_popen(node: Any, command: list[str], **kwargs: Any) -> subprocess.Popen:
    """Run command inside the node's container as a direct child process.

    DockerSta.popen() goes through "docker exec -t": the TTY merges stderr into
    stdout and signals reach only the docker client, never the process in the
    container. nsenter execs the command itself in the container's mount, UTS,
    IPC and network namespaces (with its root and working directory), so the
    returned Popen is the real process: terminate()/kill() reach it and
    stdout/stderr stay separate. The container's configured environment is
    reused so images that set PATH/LD_LIBRARY_PATH keep working.
    """
    info = getattr(node, "dcinfo", None) or {}
    env_items = (info.get("Config") or {}).get("Env") or []
    env = dict(item.split("=", 1) for item in env_items if "=" in item)
    env.setdefault("PATH", _DEFAULT_PATH)
    return subprocess.Popen(
        ["nsenter", "-t", str(node.pid), "-m", "-u", "-i", "-n", "--root", "--wd", "--", *command],
        env=env,
        **kwargs,
    )


def cleanup_mininet() -> None:
    subprocess.run(["mn", "-c"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cleanup_stale_environment(config: dict[str, Any]) -> None:
    """Remove leftovers of an interrupted run before building the network.

    "mn -c" from Mininet-WiFi does not remove Docker containers, and
    Containernet names them "mn.<container_name>"; a leftover one makes the
    next addStation() fail.
    """
    cleanup_mininet()
    names = [f"mn.{node['container_name']}" for node in config["nodes"].values()]
    try:
        subprocess.run(
            ["docker", "rm", "-f", *names],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass
