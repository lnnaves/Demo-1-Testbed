from __future__ import annotations

import shlex
import subprocess
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
                range=node_cfg["range"],
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


def _assign_application_ip(node: Any, interface: str, ip_cidr: str) -> None:
    quoted_interface = shlex.quote(interface)
    quoted_ip = shlex.quote(ip_cidr)
    output = node.cmd(
        f"ip link set dev {quoted_interface} up && "
        f"ip addr flush dev {quoted_interface} && "
        f"ip addr add {quoted_ip} dev {quoted_interface} && "
        f"echo {_IP_ASSIGN_OK}"
    )
    if not (output or "").strip().endswith(_IP_ASSIGN_OK):
        node_name = getattr(node, "name", "<unknown>")
        raise RuntimeError(
            f"failed to assign application IP {ip_cidr} to {interface} on node "
            f"{node_name!r}: {(output or '').strip()!r}"
        )


def cleanup_mininet() -> None:
    subprocess.run(["mn", "-c"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
