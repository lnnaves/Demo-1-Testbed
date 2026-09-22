import sys
import types
import unittest
from unittest.mock import patch

from scripts.testbed.network import _assign_application_ip, build_network


class FakeStation:
    def __init__(self, name):
        self.name = name
        self.commands = []

    def cmd(self, command):
        self.commands.append(command)
        return ""


class FakeNet:
    instances = []
    fail_on_start = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.propagation = None
        self.stations = []
        self.links = []
        self.configure_nodes_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        FakeNet.instances.append(self)

    def setPropagationModel(self, **kwargs):
        self.propagation = kwargs

    def addStation(self, name, **kwargs):
        station = FakeStation(name)
        self.stations.append((station, kwargs))
        return station

    def configureNodes(self):
        self.configure_nodes_calls += 1

    def addLink(self, station, **kwargs):
        self.links.append((station, kwargs))

    def start(self):
        self.start_calls += 1
        if FakeNet.fail_on_start:
            raise RuntimeError("start failed")

    def stop(self):
        self.stop_calls += 1


class FakeDockerSta:
    pass


class FakeAdhoc:
    pass


class FakeWmediumd:
    pass


class FakeInterference:
    pass


def _config():
    return {
        "wireless": {
            "ssid": "meshNet",
            "mode": "g",
            "channel": 5,
            "ht_cap": "HT40+",
            "noise_threshold_dbm": -91,
            "fading_coefficient": 3,
            "propagation_model": "logDistance",
            "propagation_exponent": 3.5,
            "interface": "bat0",
        },
        "nodes": {
            "gcs": {
                "id": "gcs",
                "container_name": "gcs0",
                "ip": "192.168.123.1/24",
                "position": [0, 0, 0],
                "image": "drone:latest",
                "memory": "512m",
                "range": 25,
                "txpower": 10,
            },
            "drone1": {
                "id": "drone1",
                "container_name": "dr1",
                "ip": "192.168.123.2/24",
                "position": [25, 0, 0],
                "image": "drone:latest",
                "memory": "512m",
                "range": 25,
                "txpower": 10,
            },
        },
        "binaries": {
            "host_directory": "/project/bin",
            "container_directory": "/opt/protocol/bin",
        },
    }


def _fake_modules():
    containernet_net = types.ModuleType("containernet.net")
    containernet_net.Containernet = FakeNet
    containernet_node = types.ModuleType("containernet.node")
    containernet_node.DockerSta = FakeDockerSta
    mn_wifi_link = types.ModuleType("mn_wifi.link")
    mn_wifi_link.adhoc = FakeAdhoc
    mn_wifi_link.wmediumd = FakeWmediumd
    mn_wifi_wmediumd = types.ModuleType("mn_wifi.wmediumdConnector")
    mn_wifi_wmediumd.interference = FakeInterference
    return {
        "containernet": types.ModuleType("containernet"),
        "containernet.net": containernet_net,
        "containernet.node": containernet_node,
        "mn_wifi": types.ModuleType("mn_wifi"),
        "mn_wifi.link": mn_wifi_link,
        "mn_wifi.wmediumdConnector": mn_wifi_wmediumd,
    }


class NetworkBuildTests(unittest.TestCase):
    def setUp(self):
        FakeNet.instances = []
        FakeNet.fail_on_start = False

    def test_build_network_delegates_adhoc_batman_lifecycle_to_mininet_wifi(self):
        with patch.dict(sys.modules, _fake_modules()):
            context = build_network(_config())

        net = FakeNet.instances[0]
        self.assertIs(context.net, net)
        self.assertEqual(set(context.nodes), {"gcs", "drone1"})
        self.assertEqual(context.nodes["gcs"].name, "gcs0")
        self.assertEqual(context.nodes["drone1"].name, "dr1")
        self.assertEqual(net.configure_nodes_calls, 1)
        self.assertEqual(net.start_calls, 1)

        self.assertEqual(
            [kwargs["intf"] for _, kwargs in net.links],
            ["gcs0-wlan0", "dr1-wlan0"],
        )
        for _, kwargs in net.links:
            self.assertIs(kwargs["cls"], FakeAdhoc)
            self.assertEqual(kwargs["ssid"], "meshNet")
            self.assertEqual(kwargs["proto"], "batman_adv")
            self.assertEqual(kwargs["mode"], "g")
            self.assertEqual(kwargs["channel"], 5)
            self.assertEqual(kwargs["ht_cap"], "HT40+")

        commands = "\n".join(command for station in context.nodes.values() for command in station.commands)
        self.assertNotIn("iw ", commands)
        self.assertNotIn("modprobe", commands)
        self.assertNotIn("batctl", commands)
        self.assertNotIn("ip route", commands)
        self.assertNotIn("ping", commands)
        self.assertIn("ip addr add 192.168.123.1/24 dev bat0", context.nodes["gcs"].commands[0])
        self.assertIn("ip addr add 192.168.123.2/24 dev bat0", context.nodes["drone1"].commands[0])

    def test_build_network_stops_net_when_construction_fails(self):
        FakeNet.fail_on_start = True
        with patch.dict(sys.modules, _fake_modules()):
            with self.assertRaises(RuntimeError):
                build_network(_config())

        self.assertEqual(FakeNet.instances[0].stop_calls, 1)


class AssignApplicationIpTests(unittest.TestCase):
    def test_assign_application_ip_only_updates_configured_interface_address(self):
        node = FakeStation("gcs0")
        _assign_application_ip(node, "bat0", "192.168.123.1/24")

        self.assertEqual(
            node.commands,
            [
                "ip link set dev bat0 up && "
                "ip addr flush dev bat0 && "
                "ip addr add 192.168.123.1/24 dev bat0"
            ],
        )


if __name__ == "__main__":
    unittest.main()
