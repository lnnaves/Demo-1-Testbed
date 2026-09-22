import sys
import types
import unittest
from unittest.mock import patch

from scripts.testbed.network import _assign_application_ip, build_network


class FakeStation:
    def __init__(self, name, fail_ip_assign=False):
        self.name = name
        self.commands = []
        self.fail_ip_assign = fail_ip_assign

    def cmd(self, command):
        self.commands.append(command)
        if self.fail_ip_assign:
            return "RTNETLINK answers: Operation not permitted"
        if "&& echo " in command:
            return command.rsplit("echo ", 1)[-1] + "\n"
        return ""


class FakeNet:
    instances = []
    fail_on_start = False
    fail_on_configure_nodes = False
    fail_on_add_link = False
    fail_ip_assign_for = frozenset()

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.propagation = None
        self.stations = []
        self.links = []
        self.call_order = []
        self.configure_nodes_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        FakeNet.instances.append(self)

    def setPropagationModel(self, **kwargs):
        self.propagation = kwargs

    def addStation(self, name, **kwargs):
        station = FakeStation(name, fail_ip_assign=name in FakeNet.fail_ip_assign_for)
        self.stations.append((station, kwargs))
        return station

    def configureNodes(self):
        self.call_order.append("configureNodes")
        self.configure_nodes_calls += 1
        if FakeNet.fail_on_configure_nodes:
            raise RuntimeError("configureNodes failed")

    def addLink(self, station, **kwargs):
        self.call_order.append("addLink")
        if FakeNet.fail_on_add_link:
            raise RuntimeError("addLink failed")
        self.links.append((station, kwargs))

    def start(self):
        self.call_order.append("start")
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
        FakeNet.fail_on_configure_nodes = False
        FakeNet.fail_on_add_link = False
        FakeNet.fail_ip_assign_for = frozenset()

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
        self.assertNotIn("iperf", commands)
        # The only post-start operation is the minimal application IP assignment.
        self.assertEqual(len(context.nodes["gcs"].commands), 1)
        self.assertEqual(len(context.nodes["drone1"].commands), 1)
        self.assertIn("ip addr add 192.168.123.1/24 dev bat0", context.nodes["gcs"].commands[0])
        self.assertIn("ip addr add 192.168.123.2/24 dev bat0", context.nodes["drone1"].commands[0])

    def test_add_station_receives_container_name_and_readonly_binaries_volume(self):
        config = _config()
        with patch.dict(sys.modules, _fake_modules()):
            build_network(config)

        net = FakeNet.instances[0]
        station_names = [station.name for station, _ in net.stations]
        self.assertEqual(station_names, ["gcs0", "dr1"])

        expected_volume = (
            f"{config['binaries']['host_directory']}:{config['binaries']['container_directory']}:ro"
        )
        for _, kwargs in net.stations:
            self.assertIs(kwargs["cls"], FakeDockerSta)
            self.assertEqual(kwargs["volumes"], [expected_volume])
        self.assertTrue(expected_volume.endswith(":ro"))

    def test_configure_nodes_runs_before_add_link(self):
        with patch.dict(sys.modules, _fake_modules()):
            build_network(_config())

        net = FakeNet.instances[0]
        self.assertEqual(net.call_order[0], "configureNodes")
        first_add_link_index = net.call_order.index("addLink")
        self.assertGreater(first_add_link_index, net.call_order.index("configureNodes"))

    def test_build_network_stops_net_when_start_fails(self):
        FakeNet.fail_on_start = True
        with patch.dict(sys.modules, _fake_modules()):
            with self.assertRaises(RuntimeError):
                build_network(_config())

        self.assertEqual(FakeNet.instances[0].stop_calls, 1)

    def test_build_network_stops_net_when_configure_nodes_fails(self):
        FakeNet.fail_on_configure_nodes = True
        with patch.dict(sys.modules, _fake_modules()):
            with self.assertRaises(RuntimeError):
                build_network(_config())

        self.assertEqual(FakeNet.instances[0].stop_calls, 1)

    def test_build_network_stops_net_when_add_link_fails(self):
        FakeNet.fail_on_add_link = True
        with patch.dict(sys.modules, _fake_modules()):
            with self.assertRaises(RuntimeError):
                build_network(_config())

        self.assertEqual(FakeNet.instances[0].stop_calls, 1)

    def test_build_network_stops_net_when_ip_assignment_fails(self):
        FakeNet.fail_ip_assign_for = frozenset({"gcs0"})
        with patch.dict(sys.modules, _fake_modules()):
            with self.assertRaisesRegex(RuntimeError, r"gcs0"):
                build_network(_config())

        self.assertEqual(FakeNet.instances[0].stop_calls, 1)


class AssignApplicationIpTests(unittest.TestCase):
    def test_assign_application_ip_only_updates_configured_interface_address(self):
        node = FakeStation("gcs0")
        _assign_application_ip(node, "bat0", "192.168.123.1/24")

        self.assertEqual(len(node.commands), 1)
        command = node.commands[0]
        self.assertIn("ip link set dev bat0 up", command)
        self.assertIn("ip addr flush dev bat0", command)
        self.assertIn("ip addr add 192.168.123.1/24 dev bat0", command)
        self.assertNotIn("ip route", command)
        self.assertNotIn("ping", command)
        self.assertNotIn("batctl", command)

    def test_assign_application_ip_raises_useful_error_on_failure(self):
        node = FakeStation("dr1", fail_ip_assign=True)

        with self.assertRaisesRegex(RuntimeError, r"bat0.*dr1"):
            _assign_application_ip(node, "bat0", "192.168.123.2/24")


class NetworkContextTests(unittest.TestCase):
    def test_stop_delegates_to_net_and_is_idempotent(self):
        net = FakeNet()
        context = build_network.__globals__["NetworkContext"](net, {})

        context.stop()
        context.stop()

        self.assertEqual(net.stop_calls, 2)

    def test_stop_is_safe_when_net_raises(self):
        class RaisingNet:
            def stop(self):
                raise RuntimeError("already stopped")

        context = build_network.__globals__["NetworkContext"](RaisingNet(), {})
        context.stop()  # must not raise

    def test_stop_is_safe_when_net_is_none(self):
        context = build_network.__globals__["NetworkContext"](None, {})
        context.stop()  # must not raise


class CleanupMininetTests(unittest.TestCase):
    def test_cleanup_mininet_invokes_mn_dash_c_without_raising(self):
        from scripts.testbed.network import cleanup_mininet

        with patch("scripts.testbed.network.subprocess.run") as run_mock:
            cleanup_mininet()

        run_mock.assert_called_once()
        args, kwargs = run_mock.call_args
        self.assertEqual(args[0], ["mn", "-c"])
        self.assertFalse(kwargs.get("check", False))


if __name__ == "__main__":
    unittest.main()
