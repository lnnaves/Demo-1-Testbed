import re
import sys
import types
import unittest
from unittest.mock import patch

from scripts.testbed.network import _IP_ASSIGN_OK, _assign_application_ip, build_network


class FakeStation:
    def __init__(self, name, fail_ip_assign=False, fail_batman=False):
        self.name = name
        self.commands = []
        self.fail_ip_assign = fail_ip_assign
        self.fail_batman = fail_batman

    def cmd(self, command):
        self.commands.append(command)
        if self.fail_ip_assign and "ip addr" in command:
            return "RTNETLINK answers: Operation not permitted"
        if self.fail_batman and command.startswith("batctl"):
            return "Error - interface bat0 is not present or not a batman-adv interface"
        sentinel = re.search(r"echo (__\w+__)$", command)
        return f"{sentinel.group(1)}\n" if sentinel else ""


class FakeNet:
    instances = []
    fail_on_start = False
    fail_on_configure_nodes = False
    fail_on_add_link = False
    fail_on_stop = False
    fail_ip_assign_for = frozenset()
    fail_batman_for = frozenset()

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
        station = FakeStation(
            name,
            fail_ip_assign=name in FakeNet.fail_ip_assign_for,
            fail_batman=name in FakeNet.fail_batman_for,
        )
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
        if FakeNet.fail_on_stop:
            raise RuntimeError("stop failed")


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
            "batman_bridge_loop_avoidance": False,
            "batman_orig_interval_ms": 1000,
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
        FakeNet.fail_on_stop = False
        FakeNet.fail_ip_assign_for = frozenset()
        FakeNet.fail_batman_for = frozenset()

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
        self.assertNotIn("batctl if", commands)
        self.assertNotIn("ip route", commands)
        self.assertNotIn("ping", commands)
        self.assertNotIn("iperf", commands)
        # Mininet-WiFi owns the BATMAN-adv lifecycle; after start the testbed
        # only tunes two BATMAN-adv settings and assigns the application IP.
        self.assertEqual(len(context.nodes["gcs"].commands), 2)
        self.assertEqual(len(context.nodes["drone1"].commands), 2)
        self.assertTrue(context.nodes["gcs"].commands[0].startswith("batctl bl 0 && batctl it 1000"))
        self.assertIn("ip addr add 192.168.123.1/24 dev bat0", context.nodes["gcs"].commands[1])
        self.assertIn("ip addr add 192.168.123.2/24 dev bat0", context.nodes["drone1"].commands[1])

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

    def test_build_network_preserves_error_when_cleanup_also_fails(self):
        FakeNet.fail_on_start = True
        FakeNet.fail_on_stop = True
        with patch.dict(sys.modules, _fake_modules()):
            with self.assertRaisesRegex(RuntimeError, "start failed"):
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

    def test_batman_is_configured_on_every_node_before_ip_assignment(self):
        config = _config()
        config["wireless"]["batman_orig_interval_ms"] = 200
        with patch.dict(sys.modules, _fake_modules()):
            build_network(config)

        for station, _kwargs in FakeNet.instances[0].stations:
            with self.subTest(station=station.name):
                self.assertEqual(station.commands[0], "batctl bl 0 && batctl it 200 && echo __BATMAN_OK__")
                self.assertIn("ip addr add", station.commands[1])

    def test_bridge_loop_avoidance_can_be_enabled(self):
        config = _config()
        config["wireless"]["batman_bridge_loop_avoidance"] = True
        with patch.dict(sys.modules, _fake_modules()):
            build_network(config)

        station = FakeNet.instances[0].stations[0][0]
        self.assertTrue(station.commands[0].startswith("batctl bl 1 && "))

    def test_build_network_stops_net_when_batman_configuration_fails(self):
        FakeNet.fail_batman_for = frozenset({"dr1"})
        with patch.dict(sys.modules, _fake_modules()):
            with self.assertRaisesRegex(RuntimeError, r"failed to configure BATMAN-adv on node 'dr1'"):
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

    def test_assign_application_ip_tolerates_terminal_control_sequences(self):
        node = FakeStation("gcs0")
        node.cmd = lambda command: f"\x1b[?2004l\r\r\n{_IP_ASSIGN_OK}\r\n\x1b[?2004h"

        _assign_application_ip(node, "bat0", "192.168.123.1/24")

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

    def test_cleanup_stale_environment_removes_containernet_containers(self):
        from scripts.testbed.network import cleanup_stale_environment

        config = {"nodes": {"gcs": {"container_name": "gcs0"}, "drone1": {"container_name": "dr1"}}}
        with patch("scripts.testbed.network.subprocess.run") as run_mock:
            cleanup_stale_environment(config)

        commands = [c.args[0] for c in run_mock.call_args_list]
        self.assertEqual(commands, [["mn", "-c"], ["docker", "rm", "-f", "mn.gcs0", "mn.dr1"]])

    def test_cleanup_stale_environment_tolerates_missing_docker(self):
        from scripts.testbed.network import cleanup_stale_environment

        config = {"nodes": {"gcs": {"container_name": "gcs0"}}}
        with patch("scripts.testbed.network.subprocess.run", side_effect=[None, FileNotFoundError()]):
            cleanup_stale_environment(config)


class PingStation:
    """Station whose ping to each address succeeds only after N failed tries."""

    def __init__(self, name, failures_before_reply=None):
        self.name = name
        self.commands = []
        self.failures_before_reply = dict(failures_before_reply or {})

    def cmd(self, command):
        self.commands.append(command)
        if command.startswith("ping"):
            address = command.split()[5]
            remaining = self.failures_before_reply.get(address, 0)
            if remaining is None:
                return "\x1b[?2004l\r\n"
            if remaining > 0:
                self.failures_before_reply[address] = remaining - 1
                return "\x1b[?2004l\r\n"
            return "\x1b[?2004l\r\n__PING_OK__\r\n\x1b[?2004h"
        return ""


def _mesh_config(receivers=("gcs",), timeout=30.0):
    return {
        "experiment": {"network_ready_timeout_seconds": timeout},
        "protocol": {"sender": "drone1", "receivers": list(receivers)},
        "wireless": {"interface": "bat0"},
        "nodes": {
            "gcs": {"address": "192.168.123.1"},
            "drone1": {"address": "192.168.123.2"},
            "drone2": {"address": "192.168.123.3"},
        },
    }


class WaitForNetworkTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        clock = patch("scripts.testbed.network.time.monotonic", side_effect=lambda: self.now[0])
        sleep = patch("scripts.testbed.network.time.sleep", side_effect=self._advance)
        self.sleep = sleep.start()
        clock.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleep.stop)

    def _advance(self, seconds):
        self.now[0] += seconds

    def _nodes(self, sender):
        return {"drone1": sender, "gcs": PingStation("gcs0"), "drone2": PingStation("dr2")}

    def test_returns_immediately_when_receiver_already_reachable(self):
        from scripts.testbed.network import wait_for_network

        sender = PingStation("dr1")
        elapsed = wait_for_network(_mesh_config(), self._nodes(sender))

        self.assertEqual(elapsed, 0.0)
        self.sleep.assert_not_called()
        self.assertEqual(sender.commands[0], "ping -c 1 -W 1 192.168.123.1 >/dev/null 2>&1 && echo __PING_OK__")

    def test_retries_until_mesh_converges(self):
        from scripts.testbed.network import wait_for_network

        sender = PingStation("dr1", {"192.168.123.1": 3})
        elapsed = wait_for_network(_mesh_config(), self._nodes(sender))

        self.assertEqual(elapsed, 3.0)
        pings = [c for c in sender.commands if c.startswith("ping")]
        self.assertEqual(len(pings), 4)

    def test_broadcast_waits_for_every_receiver(self):
        from scripts.testbed.network import wait_for_network

        sender = PingStation("dr1", {"192.168.123.3": 2})
        wait_for_network(_mesh_config(receivers=("gcs", "drone2")), self._nodes(sender))

        pinged = [c.split()[5] for c in sender.commands if c.startswith("ping")]
        # gcs answers at once and is not pinged again; drone2 needs three tries.
        self.assertEqual(pinged.count("192.168.123.1"), 1)
        self.assertEqual(pinged.count("192.168.123.3"), 3)

    def test_flushes_neighbour_cache_on_every_node_after_readiness(self):
        from scripts.testbed.network import wait_for_network

        nodes = self._nodes(PingStation("dr1"))
        wait_for_network(_mesh_config(), nodes)

        for node in nodes.values():
            self.assertEqual(node.commands[-1], "ip neigh flush dev bat0")

    def test_timeout_names_unreachable_receivers(self):
        from scripts.testbed.network import wait_for_network

        sender = PingStation("dr1", {"192.168.123.1": None})
        nodes = self._nodes(sender)
        with self.assertRaisesRegex(
            RuntimeError, r"network not ready: drone1 could not reach gcs \(192\.168\.123\.1\) within 5s"
        ):
            wait_for_network(_mesh_config(timeout=5.0), nodes)

        self.assertNotIn("ip neigh flush dev bat0", nodes["gcs"].commands)


class ContainerPopenTests(unittest.TestCase):
    def test_runs_command_in_container_namespaces_with_container_env(self):
        from scripts.testbed.network import container_popen

        node = types.SimpleNamespace(
            pid=4242,
            dcinfo={"Config": {"Env": ["PATH=/opt/bin:/usr/bin", "LD_LIBRARY_PATH=/opt/lib", "BROKEN"]}},
        )
        with patch("scripts.testbed.network.subprocess.Popen") as popen:
            container_popen(node, ["/opt/protocol/bin/receiver", "--port", "5000"], text=True)

        args, kwargs = popen.call_args
        self.assertEqual(
            args[0],
            ["nsenter", "-t", "4242", "-m", "-u", "-i", "-n", "--root", "--wd", "--",
             "/opt/protocol/bin/receiver", "--port", "5000"],
        )
        self.assertEqual(kwargs["env"], {"PATH": "/opt/bin:/usr/bin", "LD_LIBRARY_PATH": "/opt/lib"})
        self.assertTrue(kwargs["text"])

    def test_defaults_path_when_container_env_is_unknown(self):
        from scripts.testbed.network import container_popen

        node = types.SimpleNamespace(pid=7)
        with patch("scripts.testbed.network.subprocess.Popen") as popen:
            container_popen(node, ["true"])

        self.assertIn("/usr/bin", popen.call_args.kwargs["env"]["PATH"])


if __name__ == "__main__":
    unittest.main()
