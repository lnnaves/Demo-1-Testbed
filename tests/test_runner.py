import subprocess
import unittest
from unittest.mock import patch

from scripts.testbed.runner import execute_protocol, select_destination, stop_receivers


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
    """Minimal stand-in for subprocess.Popen used by the fake nodes.

    ``poll_sequence`` lets a test script a process that appears alive for a
    number of poll() calls and then reports as exited afterwards (used to
    simulate a receiver that dies spontaneously after the startup check).
    ``communicate_effects`` lets a test force communicate() to raise
    TimeoutExpired a given number of times before succeeding, to exercise the
    sender timeout/terminate/kill path.
    """

    def __init__(self, stdout="", stderr="", returncode=0, poll_sequence=None, communicate_effects=None):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = None
        self.final_returncode = returncode
        self.terminated = False
        self.killed = False
        self._poll_sequence = list(poll_sequence) if poll_sequence is not None else None
        self._communicate_effects = list(communicate_effects) if communicate_effects else []

    def poll(self):
        if self._poll_sequence is not None:
            if len(self._poll_sequence) > 1:
                return self._poll_sequence.pop(0)
            return self._poll_sequence[0]
        return self.returncode

    def communicate(self, timeout=None):
        if self._communicate_effects:
            effect = self._communicate_effects.pop(0)
            if effect == "timeout":
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
        self.returncode = self.final_returncode
        return self.stdout, self.stderr

    def terminate(self):
        self.terminated = True
        if self.returncode is None:
            self.returncode = self.final_returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class FakeNode:
    def __init__(self, name, process_factory=None, raise_on_popen=None, call_order=None):
        self.name = name
        self.commands = []
        self.processes = []
        self._process_factory = process_factory or (lambda command: FakeProcess(stdout=f"{name} ok"))
        self._raise_on_popen = raise_on_popen
        self._call_order = call_order

    def popen(self, command, **kwargs):
        if self._call_order is not None:
            self._call_order.append(self.name)
        if self._raise_on_popen is not None:
            raise self._raise_on_popen
        self.commands.append(command)
        process = self._process_factory(command)
        self.processes.append(process)
        return process


class DestinationTests(unittest.TestCase):
    def test_unicast_uses_single_receiver_address(self):
        self.assertEqual(select_destination(base_config()), "192.168.123.1")

    def test_broadcast_uses_configured_broadcast_ip(self):
        self.assertEqual(
            select_destination(base_config(mode="broadcast", receivers=["gcs", "drone2"])),
            "192.168.123.255",
        )

    def test_unicast_rejects_multiple_receivers(self):
        with self.assertRaises(ValueError):
            select_destination(base_config(receivers=["gcs", "drone2"]))


class LifecycleTests(unittest.TestCase):
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

    def test_receiver_started_before_sender(self):
        config = base_config()
        call_order = []
        nodes = {
            "gcs": FakeNode("gcs", call_order=call_order),
            "drone1": FakeNode("drone1", call_order=call_order),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            execute_protocol(config, nodes)

        self.assertEqual(call_order, ["gcs", "drone1"])

    def test_receiver_command_uses_configured_address_and_port(self):
        config = base_config()
        nodes = {name: FakeNode(name) for name in ("gcs", "drone1")}
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            execute_protocol(config, nodes)

        receiver_command = nodes["gcs"].commands[0]
        self.assertEqual(
            receiver_command,
            ["/opt/protocol/bin/receiver", "--address", "192.168.123.1", "--port", "5000"],
        )

    def test_sender_receives_destination_port_count_and_no_mode(self):
        config = base_config()
        nodes = {name: FakeNode(name) for name in ("gcs", "drone1")}
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            execute_protocol(config, nodes)

        sender_command = nodes["drone1"].commands[0]
        self.assertEqual(
            sender_command,
            ["/opt/protocol/bin/sender", "--destination", "192.168.123.1", "--port", "5000", "--count", "2"],
        )
        self.assertNotIn("--mode", sender_command)

    def test_broadcast_starts_all_receivers(self):
        config = base_config(mode="broadcast", receivers=["gcs", "drone2"])
        nodes = {name: FakeNode(name) for name in ("gcs", "drone2", "drone1")}
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["receivers"]), 2)
        self.assertEqual(len(nodes["gcs"].processes), 1)
        self.assertEqual(len(nodes["drone2"].processes), 1)
        sender_command = nodes["drone1"].commands[0]
        self.assertIn("192.168.123.255", sender_command)

    def test_receiver_exit_during_startup_blocks_sender_and_reports_diagnostics(self):
        config = base_config()

        def dying_receiver(command):
            # Already dead by the time the startup check polls it.
            return FakeProcess(stdout="boom", stderr="crashed", returncode=1, poll_sequence=[1])

        nodes = {
            "gcs": FakeNode("gcs", process_factory=dying_receiver),
            "drone1": FakeNode("drone1"),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("exited during startup" in failure for failure in result["failures"]))
        self.assertIsNone(result["sender"])
        self.assertEqual(len(nodes["drone1"].processes), 0)
        receiver_summary = result["receivers"][0]
        self.assertEqual(receiver_summary["exit_code"], 1)
        self.assertEqual(receiver_summary["stdout"], "boom")
        self.assertEqual(receiver_summary["stderr"], "crashed")
        self.assertIsNotNone(receiver_summary["duration_seconds"])

    def test_receiver_dies_spontaneously_after_startup_reports_failure(self):
        config = base_config()

        def flaky_receiver(command):
            # Alive during the startup check, found dead by the time cleanup runs.
            return FakeProcess(stdout="late crash", stderr="oops", returncode=1, poll_sequence=[None, 1])

        nodes = {
            "gcs": FakeNode("gcs", process_factory=flaky_receiver),
            "drone1": FakeNode("drone1"),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("exited unexpectedly" in failure for failure in result["failures"]))
        # The sender still ran normally; only the receiver failure is reported.
        self.assertEqual(result["sender"]["exit_code"], 0)

    def test_receiver_terminated_by_runner_is_not_a_failure(self):
        config = base_config()
        nodes = {name: FakeNode(name) for name in ("gcs", "drone1")}
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["failures"], [])
        self.assertTrue(nodes["gcs"].processes[0].terminated)

    def test_sender_non_zero_exit_reports_failure(self):
        config = base_config()

        def failing_sender(command):
            return FakeProcess(stdout="", stderr="sender error", returncode=1)

        nodes = {
            "gcs": FakeNode("gcs"),
            "drone1": FakeNode("drone1", process_factory=failing_sender),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failures"], ["sender exited with code 1"])
        self.assertEqual(result["sender"]["exit_code"], 1)
        self.assertEqual(result["sender"]["stderr"], "sender error")

    def test_sender_timeout_terminates_then_kills_and_reports_single_failure(self):
        config = base_config()

        def timing_out_sender(command):
            # First communicate() (before timeout) times out, terminate() is
            # tried, its follow-up communicate() also times out, so kill() is
            # used and the final communicate() succeeds.
            return FakeProcess(
                stdout="partial",
                stderr="",
                returncode=-9,
                communicate_effects=["timeout", "timeout"],
            )

        nodes = {
            "gcs": FakeNode("gcs"),
            "drone1": FakeNode("drone1", process_factory=timing_out_sender),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        sender_process = nodes["drone1"].processes[0]
        self.assertTrue(sender_process.terminated)
        self.assertTrue(sender_process.killed)
        self.assertEqual(result["failures"], ["sender timed out"])
        self.assertEqual(result["sender"]["stdout"], "partial")
        self.assertIsNotNone(result["sender"]["duration_seconds"])

    def test_receivers_are_stopped_when_sender_times_out(self):
        config = base_config()

        def timing_out_sender(command):
            return FakeProcess(returncode=-9, communicate_effects=["timeout"])

        nodes = {
            "gcs": FakeNode("gcs"),
            "drone1": FakeNode("drone1", process_factory=timing_out_sender),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            execute_protocol(config, nodes)

        self.assertTrue(nodes["gcs"].processes[0].terminated)

    def test_receivers_are_stopped_when_sender_fails(self):
        config = base_config()

        def failing_sender(command):
            return FakeProcess(returncode=2)

        nodes = {
            "gcs": FakeNode("gcs"),
            "drone1": FakeNode("drone1", process_factory=failing_sender),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            execute_protocol(config, nodes)

        self.assertTrue(nodes["gcs"].processes[0].terminated)

    def test_result_contains_stdout_stderr_exit_code_and_duration(self):
        config = base_config()
        nodes = {name: FakeNode(name) for name in ("gcs", "drone1")}
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        for summary in [result["sender"], *result["receivers"]]:
            self.assertIn("stdout", summary)
            self.assertIn("stderr", summary)
            self.assertIn("exit_code", summary)
            self.assertIn("duration_seconds", summary)
            self.assertIsNotNone(summary["duration_seconds"])

    def test_receiver_spawn_failure_is_reported_and_earlier_receivers_are_stopped(self):
        config = base_config(mode="broadcast", receivers=["gcs", "drone2"])
        nodes = {
            "gcs": FakeNode("gcs"),
            "drone2": FakeNode("drone2", raise_on_popen=OSError("no such file")),
            "drone1": FakeNode("drone1"),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("failed to start receiver drone2" in failure for failure in result["failures"]))
        self.assertIsNone(result["sender"])
        self.assertEqual(len(nodes["drone1"].processes), 0)
        self.assertTrue(nodes["gcs"].processes[0].terminated)

    def test_sender_spawn_failure_is_reported_and_receivers_are_stopped(self):
        config = base_config()
        nodes = {
            "gcs": FakeNode("gcs"),
            "drone1": FakeNode("drone1", raise_on_popen=OSError("permission denied")),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("failed to start sender drone1" in failure for failure in result["failures"]))
        self.assertIsNone(result["sender"])
        self.assertTrue(nodes["gcs"].processes[0].terminated)


class StopReceiversTests(unittest.TestCase):
    def test_stop_receivers_does_not_flag_controlled_shutdown(self):
        from scripts.testbed.runner import ProcessRecord

        process = FakeProcess(stdout="ok", stderr="", returncode=0)
        record = ProcessRecord("receiver", "gcs", ["receiver"], process, started_at=0.0)

        failures = stop_receivers([record], timeout=1.0)

        self.assertEqual(failures, [])
        self.assertTrue(process.terminated)

    def test_stop_receivers_flags_spontaneous_exit(self):
        from scripts.testbed.runner import ProcessRecord

        # poll_sequence makes the process report as already dead (code 1)
        # before cleanup runs, simulating a spontaneous exit.
        process = FakeProcess(stdout="", stderr="died", returncode=1, poll_sequence=[1])
        record = ProcessRecord("receiver", "gcs", ["receiver"], process, started_at=0.0)

        failures = stop_receivers([record], timeout=1.0)

        self.assertEqual(len(failures), 1)
        self.assertIn("gcs", failures[0])
        self.assertFalse(process.terminated)

    def test_stop_receivers_kills_after_shutdown_timeout(self):
        from scripts.testbed.runner import ProcessRecord

        process = FakeProcess(returncode=0, communicate_effects=["timeout"])
        record = ProcessRecord("receiver", "gcs", ["receiver"], process, started_at=0.0)

        failures = stop_receivers([record], timeout=1.0)

        self.assertEqual(failures, [])
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)


if __name__ == "__main__":
    unittest.main()
