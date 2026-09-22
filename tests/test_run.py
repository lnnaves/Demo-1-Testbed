import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import run  # noqa: E402  (scripts/run.py imports modules relative to scripts/)
from testbed.config import ConfigError  # noqa: E402


def _config():
    return {"experiment": {"shutdown_timeout_seconds": 1.0}}


class RunMainTests(unittest.TestCase):
    def test_reports_phase_and_traceback_on_unexpected_error(self):
        network = MagicMock()
        network.nodes = {}

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", side_effect=RuntimeError("boom")), \
             patch("run.cleanup_mininet") as mock_cleanup_mininet, \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()

        self.assertEqual(exit_code, 1)
        printed = "".join(call.args[0] for call in mock_stderr.write.call_args_list if call.args)
        self.assertIn("execution error during start_capture", printed)
        self.assertIn("RuntimeError", printed)
        self.assertIn("Traceback", printed)
        network.stop.assert_called_once()
        mock_cleanup_mininet.assert_called_once()

    def test_lists_failures_when_write_outputs_reports_failure(self):
        network = MagicMock()
        network.nodes = {}
        capture = MagicMock()
        capture.stop.return_value = {"started": True, "packet_count": 0}

        summary = {"status": "failed", "failures": ["sender exited with code 1", "no packets captured"]}

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", return_value=capture), \
             patch("run.execute_protocol", return_value={"status": "failed", "failures": []}), \
             patch("run.write_outputs", return_value=summary), \
             patch("run.cleanup_mininet"), \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()

        self.assertEqual(exit_code, 1)
        printed = "".join(call.args[0] for call in mock_stderr.write.call_args_list if call.args)
        self.assertIn("sender exited with code 1", printed)
        self.assertIn("no packets captured", printed)
        # capture.stop() already ran once as part of the "stop_capture" phase and must
        # not be invoked again from the finally cleanup block.
        capture.stop.assert_called_once()

    def test_returns_zero_on_success(self):
        network = MagicMock()
        network.nodes = {}
        capture = MagicMock()
        capture.stop.return_value = {"started": True, "packet_count": 5}

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", return_value=capture), \
             patch("run.execute_protocol", return_value={"status": "success", "failures": []}), \
             patch("run.write_outputs", return_value={"status": "success", "failures": []}), \
             patch("run.cleanup_mininet"):
            exit_code = run.main()

        self.assertEqual(exit_code, 0)
        network.stop.assert_called_once()
        capture.stop.assert_called_once()

    def test_lists_warnings_for_inconclusive_result(self):
        network = MagicMock()
        network.nodes = {}
        capture = MagicMock()
        capture.stop.return_value = {"started": True, "packet_count": 0}
        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", return_value=capture), \
             patch("run.execute_protocol", return_value={"status": "success", "failures": []}), \
             patch("run.write_outputs", return_value={"status": "inconclusive", "failures": [],
                                                       "warnings": ["no traffic observed"]}), \
             patch("run.cleanup_mininet"), \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()
        self.assertEqual(exit_code, 1)
        printed = "".join(call.args[0] for call in mock_stderr.write.call_args_list if call.args)
        self.assertIn("no traffic observed", printed)

    def test_phase_order_on_success_path(self):
        network = MagicMock()
        network.nodes = {}
        capture = MagicMock()
        capture.stop.return_value = {"started": True, "packet_count": 5}

        manager = MagicMock()

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()) as mock_load_config, \
             patch("run.build_network", return_value=network) as mock_build_network, \
             patch("run.start_capture", return_value=capture) as mock_start_capture, \
             patch("run.execute_protocol", return_value={"status": "success", "failures": []}) as mock_execute, \
             patch("run.write_outputs", return_value={"status": "success", "failures": []}) as mock_write_outputs, \
             patch("run.cleanup_mininet"):
            manager.attach_mock(mock_load_config, "load_config")
            manager.attach_mock(mock_build_network, "build_network")
            manager.attach_mock(mock_start_capture, "start_capture")
            manager.attach_mock(mock_execute, "execute_protocol")
            manager.attach_mock(capture.stop, "capture_stop")
            manager.attach_mock(mock_write_outputs, "write_outputs")

            exit_code = run.main()

        self.assertEqual(exit_code, 0)
        expected_order = [
            "load_config",
            "build_network",
            "start_capture",
            "execute_protocol",
            "capture_stop",
            "write_outputs",
        ]
        actual_order = [entry[0] for entry in manager.mock_calls]
        self.assertEqual(actual_order, expected_order)

    def test_config_error_returns_two_without_traceback(self):
        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", side_effect=ConfigError("bad config")), \
             patch("run.build_network") as mock_build_network, \
             patch("run.cleanup_mininet") as mock_cleanup_mininet, \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()

        self.assertEqual(exit_code, 2)
        printed = "".join(c.args[0] for c in mock_stderr.write.call_args_list if c.args)
        self.assertIn("configuration error", printed)
        self.assertIn("bad config", printed)
        self.assertNotIn("Traceback", printed)
        mock_build_network.assert_not_called()
        # No network attempt was ever made, so global Mininet cleanup should be skipped.
        mock_cleanup_mininet.assert_not_called()

    def test_cleanup_mininet_runs_when_build_network_attempted(self):
        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", side_effect=RuntimeError("network boom")), \
             patch("run.cleanup_mininet") as mock_cleanup_mininet, \
             patch("sys.stderr"):
            exit_code = run.main()

        self.assertEqual(exit_code, 1)
        mock_cleanup_mininet.assert_called_once()

    def test_exception_in_build_network_does_not_leak_capture(self):
        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", side_effect=RuntimeError("network boom")), \
             patch("run.start_capture") as mock_start_capture, \
             patch("run.cleanup_mininet"), \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()

        self.assertEqual(exit_code, 1)
        mock_start_capture.assert_not_called()
        printed = "".join(c.args[0] for c in mock_stderr.write.call_args_list if c.args)
        self.assertIn("execution error during build_network", printed)

    def test_exception_in_execute_protocol_stops_capture_and_network(self):
        network = MagicMock()
        network.nodes = {}
        capture = MagicMock()

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", return_value=capture), \
             patch("run.execute_protocol", side_effect=RuntimeError("protocol boom")), \
             patch("run.cleanup_mininet"), \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()

        self.assertEqual(exit_code, 1)
        capture.stop.assert_called_once()
        network.stop.assert_called_once()
        printed = "".join(c.args[0] for c in mock_stderr.write.call_args_list if c.args)
        self.assertIn("execution error during execute_protocol", printed)

    def test_exception_during_capture_stop_still_cleans_up_network(self):
        network = MagicMock()
        network.nodes = {}
        capture = MagicMock()
        capture.stop.side_effect = RuntimeError("stop boom")

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", return_value=capture), \
             patch("run.execute_protocol", return_value={"status": "success", "failures": []}), \
             patch("run.cleanup_mininet"), \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()

        self.assertEqual(exit_code, 1)
        network.stop.assert_called_once()
        printed = "".join(c.args[0] for c in mock_stderr.write.call_args_list if c.args)
        self.assertIn("execution error during stop_capture", printed)
        self.assertIn("warning: could not stop capture", printed)

    def test_exception_in_write_outputs_cleans_network_and_does_not_restop_capture(self):
        network = MagicMock()
        network.nodes = {}
        capture = MagicMock()
        capture.stop.return_value = {"started": True, "packet_count": 5}

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", return_value=capture), \
             patch("run.execute_protocol", return_value={"status": "success", "failures": []}), \
             patch("run.write_outputs", side_effect=RuntimeError("write boom")), \
             patch("run.cleanup_mininet"), \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()

        self.assertEqual(exit_code, 1)
        capture.stop.assert_called_once()
        network.stop.assert_called_once()
        printed = "".join(c.args[0] for c in mock_stderr.write.call_args_list if c.args)
        self.assertIn("execution error during write_outputs", printed)

    def test_cleanup_error_emits_warning_and_preserves_exit_code(self):
        network = MagicMock()
        network.nodes = {}
        network.stop.side_effect = RuntimeError("network stop boom")
        capture = MagicMock()
        capture.stop.return_value = {"started": True, "packet_count": 5}

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value=_config()), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", return_value=capture), \
             patch("run.execute_protocol", return_value={"status": "success", "failures": []}), \
             patch("run.write_outputs", return_value={"status": "success", "failures": []}), \
             patch("run.cleanup_mininet"), \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()

        # A cleanup error must not override the main success exit code.
        self.assertEqual(exit_code, 0)
        printed = "".join(c.args[0] for c in mock_stderr.write.call_args_list if c.args)
        self.assertIn("warning: could not stop network", printed)
        self.assertIn("network stop boom", printed)


if __name__ == "__main__":
    unittest.main()
