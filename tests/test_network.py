import unittest

from scripts.testbed.network import (
    NetworkValidationError,
    NodeCommandError,
    _EXIT_MARKER,
    _attach_wlan_to_batman,
    _has_flag,
    channel_to_frequency,
    run_node_command,
    validate_network,
)


class FakeNode:
    """Minimal stand-in for a Containernet/Mininet-WiFi node.

    ``responses`` maps the exact command (without the exit-code marker) to a
    ``(exit_code, output)`` tuple, mirroring how ``node.cmd()`` only returns
    combined output without an exit code.
    """

    def __init__(self, name, responses):
        self.name = name
        self.responses = responses
        self.commands = []

    def cmd(self, full_command):
        self.commands.append(full_command)
        marker_suffix = f"; echo {_EXIT_MARKER}:$?"
        assert full_command.endswith(marker_suffix), full_command
        command = full_command[: -len(marker_suffix)]
        if command not in self.responses:
            raise AssertionError(f"unexpected command: {command!r}")
        exit_code, output = self.responses[command]
        return f"{output}{_EXIT_MARKER}:{exit_code}"


class RunNodeCommandTests(unittest.TestCase):
    def test_success_returns_exit_code_and_cleaned_output(self):
        node = FakeNode("gcs0", {"ip link set gcs0-wlan0 up": (0, "")})
        exit_code, output = run_node_command(node, "bring up wlan", "ip link set gcs0-wlan0 up")
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "")

    def test_failure_raises_node_command_error_with_details(self):
        node = FakeNode("gcs0", {"ip link set gcs0-wlan0 up": (1, "RTNETLINK answers: No such device")})
        with self.assertRaises(NodeCommandError) as ctx:
            run_node_command(node, "bring up wlan", "ip link set gcs0-wlan0 up")

        error = ctx.exception
        self.assertEqual(error.node_name, "gcs0")
        self.assertEqual(error.operation, "bring up wlan")
        self.assertEqual(error.command, "ip link set gcs0-wlan0 up")
        self.assertEqual(error.exit_code, 1)
        self.assertIn("No such device", error.output)
        self.assertIn("gcs0", str(error))
        self.assertIn("bring up wlan", str(error))

    def test_check_false_does_not_raise_on_failure(self):
        node = FakeNode("gcs0", {"batctl if add gcs0-wlan0": (1, "command not found")})
        exit_code, output = run_node_command(
            node, "attach wlan", "batctl if add gcs0-wlan0", check=False
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("command not found", output)


class ChannelToFrequencyTests(unittest.TestCase):
    def test_known_channels(self):
        self.assertEqual(channel_to_frequency(1), 2412)
        self.assertEqual(channel_to_frequency(5), 2432)
        self.assertEqual(channel_to_frequency(14), 2484)

    def test_unsupported_channel_raises(self):
        with self.assertRaises(ValueError):
            channel_to_frequency(20)


class AttachWlanToBatmanTests(unittest.TestCase):
    def test_falls_back_to_legacy_batctl_syntax(self):
        node = FakeNode(
            "gcs0",
            {
                "batctl meshif bat0 interface add gcs0-wlan0": (1, "unknown command: meshif"),
                "batctl if add gcs0-wlan0": (0, ""),
            },
        )
        _attach_wlan_to_batman(node, "gcs0-wlan0", "bat0")
        self.assertEqual(
            node.commands,
            [
                f"batctl meshif bat0 interface add gcs0-wlan0; echo {_EXIT_MARKER}:$?",
                f"batctl if add gcs0-wlan0; echo {_EXIT_MARKER}:$?",
            ],
        )

    def test_raises_clear_error_when_both_syntaxes_fail(self):
        node = FakeNode(
            "gcs0",
            {
                "batctl meshif bat0 interface add gcs0-wlan0": (1, "unknown command"),
                "batctl if add gcs0-wlan0": (1, "no such device"),
            },
        )
        with self.assertRaises(NodeCommandError):
            _attach_wlan_to_batman(node, "gcs0-wlan0", "bat0")


class HasFlagTests(unittest.TestCase):
    def test_detects_up_flag(self):
        output = "3: gcs0-wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500"
        self.assertTrue(_has_flag(output, "UP"))

    def test_detects_missing_flag(self):
        output = "3: bat0: <BROADCAST,MULTICAST> mtu 1500"
        self.assertFalse(_has_flag(output, "UP"))


def _validation_config():
    return {
        "wireless": {"interface": "bat0", "subnet": "192.168.123.0/24"},
        "protocol": {"sender": "drone1", "receivers": ["gcs"]},
        "nodes": {
            "gcs": {
                "id": "gcs",
                "container_name": "gcs0",
                "ip": "192.168.123.1/24",
                "address": "192.168.123.1",
            },
            "drone1": {
                "id": "drone1",
                "container_name": "dr1",
                "ip": "192.168.123.2/24",
                "address": "192.168.123.2",
            },
        },
    }


def _healthy_responses(wlan, ip):
    return {
        f"ip link show {wlan}": (0, f"3: {wlan}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500"),
        "ip link show bat0": (0, "4: bat0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500"),
        "ip addr show bat0": (0, f"inet {ip}/24 scope global bat0"),
        "ip route show dev bat0": (0, "192.168.123.0/24 proto kernel scope link src " + ip),
    }


class ValidateNetworkTests(unittest.TestCase):
    def test_passes_when_all_nodes_healthy_and_sender_can_reach_receivers(self):
        gcs_node = FakeNode("gcs0", _healthy_responses("gcs0-wlan0", "192.168.123.1"))
        drone1_responses = _healthy_responses("dr1-wlan0", "192.168.123.2")
        drone1_responses["ip route get 192.168.123.1"] = (0, "192.168.123.1 dev bat0 src 192.168.123.2")
        drone1_responses["ping -c 1 -W 2 192.168.123.1"] = (0, "1 packets transmitted, 1 received")
        drone1_node = FakeNode("dr1", drone1_responses)

        validate_network(_validation_config(), {"gcs": gcs_node, "drone1": drone1_node})

    def test_raises_when_wlan_interface_is_down(self):
        responses = _healthy_responses("gcs0-wlan0", "192.168.123.1")
        responses["ip link show gcs0-wlan0"] = (0, "3: gcs0-wlan0: <BROADCAST,MULTICAST> mtu 1500")
        gcs_node = FakeNode("gcs0", responses)
        drone1_responses = _healthy_responses("dr1-wlan0", "192.168.123.2")
        drone1_node = FakeNode("dr1", drone1_responses)

        with self.assertRaises(NetworkValidationError):
            validate_network(_validation_config(), {"gcs": gcs_node, "drone1": drone1_node})

    def test_raises_when_mesh_route_is_missing(self):
        gcs_responses = _healthy_responses("gcs0-wlan0", "192.168.123.1")
        gcs_responses["ip route show dev bat0"] = (0, "")
        gcs_node = FakeNode("gcs0", gcs_responses)
        drone1_node = FakeNode("dr1", _healthy_responses("dr1-wlan0", "192.168.123.2"))

        with self.assertRaises(NetworkValidationError):
            validate_network(_validation_config(), {"gcs": gcs_node, "drone1": drone1_node})

    def test_raises_clear_error_when_ping_to_receiver_fails(self):
        gcs_node = FakeNode("gcs0", _healthy_responses("gcs0-wlan0", "192.168.123.1"))
        drone1_responses = _healthy_responses("dr1-wlan0", "192.168.123.2")
        drone1_responses["ip route get 192.168.123.1"] = (0, "192.168.123.1 dev bat0 src 192.168.123.2")
        drone1_responses["ping -c 1 -W 2 192.168.123.1"] = (1, "100% packet loss")
        drone1_node = FakeNode("dr1", drone1_responses)

        with self.assertRaises(NodeCommandError) as ctx:
            validate_network(_validation_config(), {"gcs": gcs_node, "drone1": drone1_node})
        self.assertIn("dr1", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
