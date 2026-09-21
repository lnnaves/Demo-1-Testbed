from __future__ import annotations

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
    net.setPropagationModel(
        model=wireless["propagation_model"],
        exp=wireless["propagation_exponent"],
    )

    volume = f"{config['binaries']['host_directory']}:{config['binaries']['container_directory']}:ro"
    nodes: dict[str, Any] = {}
    for index, node_cfg in enumerate(config["nodes"].values(), start=1):
        nodes[node_cfg["id"]] = net.addStation(
            node_cfg["id"],
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
            intf=f"{station.name}-wlan0",
            ssid=wireless["ssid"],
            proto="batman_adv",
            mode=wireless["mode"],
            channel=wireless["channel"],
            ht_cap=wireless["ht_cap"],
        )

    net.start()
    for node_cfg in config["nodes"].values():
        _configure_node(nodes[node_cfg["id"]], node_cfg, wireless)

    return NetworkContext(net, nodes)


def _configure_node(node: Any, node_cfg: dict[str, Any], wireless: dict[str, Any]) -> None:
    wlan = f"{node.name}-wlan0"
    interface = wireless["interface"]
    commands = [
        f"ip link set {wlan} up",
        f"iw dev {wlan} ibss join {wireless['ssid']} {wireless['channel']} HT40+ fixed-freq {wireless['bssid']} || true",
        "modprobe batman-adv || true",
        f"batctl if add {wlan} || true",
        f"ip link set {interface} up",
        f"ip addr flush dev {interface} || true",
        f"ip addr add {node_cfg['ip']} dev {interface}",
    ]
    for command in commands:
        node.cmd(command)


def cleanup_mininet() -> None:
    subprocess.run(["mn", "-c"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
