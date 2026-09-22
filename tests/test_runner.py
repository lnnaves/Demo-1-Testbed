import unittest
from unittest.mock import patch

from scripts.testbed.runner import execute_protocol, select_destination


def base_config(mode="unicast", receivers=None):
    return {
        "experiment": {
            "duration_seconds": 5,
            "receiver_startup_seconds": 1.0,
            "shutdown_timeout_seconds": 1.0,
        },
        "protocol": {
            "mode": mode,
            "port": 5000,
            "count": 2,
            "sender": "drone1",
            "receivers": receivers or ["gcs"],
        },
        "wireless": {"broadcast_ip": "192.168.123.255"},
        "nodes": {
            "gcs": {"address": "192.168.123.1"},
            "drone1": {"address": "192.168.123.2"},
            "drone2": {"address": "192.168.123.3"},
        },
        "binaries": {
            "sender": {"container": "/opt/protocol/bin/sender"},
            "receiver": {"container": "/opt/protocol/bin/receiver"},
        },
    }


class FakeProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = None
        self.final_returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        self.returncode = self.final_returncode
        return self.stdout, self.stderr

    def terminate(self):
        self.terminated = True
        self.returncode = self.final_returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class FakeNode:
    def __init__(self, name):
        self.name = name
        self.commands = []
        self.processes = []

    def popen(self, command, **kwargs):
        self.commands.append(command)
        process = FakeProcess(stdout=f"{self.name} ok")
        self.processes.append(process)
        return process


class RunnerTests(unittest.TestCase):
    def test_select_destination_unicast_and_broadcast(self):
        self.assertEqual(select_destination(base_config()), "192.168.123.1")
        self.assertEqual(
            select_destination(base_config(mode="broadcast", receivers=["gcs", "drone2"])),
            "192.168.123.255",
        )
        with self.assertRaises(ValueError):
            select_destination(base_config(receivers=["gcs", "drone2"]))

    def test_execute_protocol_lifecycle_with_mocks(self):
        config = base_config()
        nodes = {name: FakeNode(name) for name in ("gcs", "drone1")}
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["sender"]["exit_code"], 0)
        self.assertEqual(result["receivers"][0]["exit_code"], 0)
        sender_command = nodes["drone1"].commands[0]
        self.assertIn("--destination", sender_command)
        self.assertNotIn("--mode", sender_command)
        self.assertTrue(nodes["gcs"].processes[0].terminated)


if __name__ == "__main__":
    unittest.main()
