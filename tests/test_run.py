import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import run  # noqa: E402  (scripts/run.py imports modules relative to scripts/)


class RunMainTests(unittest.TestCase):
    def test_reports_phase_and_traceback_on_unexpected_error(self):
        network = MagicMock()
        network.nodes = {}

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value={"experiment": {"shutdown_timeout_seconds": 1.0}}), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", side_effect=RuntimeError("boom")), \
             patch("run.cleanup_mininet"), \
             patch("sys.stderr") as mock_stderr:
            exit_code = run.main()

        self.assertEqual(exit_code, 1)
        printed = "".join(call.args[0] for call in mock_stderr.write.call_args_list if call.args)
        self.assertIn("execution error during start_capture", printed)
        self.assertIn("RuntimeError", printed)
        self.assertIn("Traceback", printed)
        network.stop.assert_called_once()

    def test_lists_failures_when_write_outputs_reports_failure(self):
        network = MagicMock()
        network.nodes = {}
        capture = MagicMock()
        capture.stop.return_value = {"started": True, "packet_count": 0}

        summary = {"status": "failed", "failures": ["sender exited with code 1", "no packets captured"]}

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value={"experiment": {"shutdown_timeout_seconds": 1.0}}), \
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

    def test_returns_zero_on_success(self):
        network = MagicMock()
        network.nodes = {}
        capture = MagicMock()
        capture.stop.return_value = {"started": True, "packet_count": 5}

        with patch("sys.argv", ["run.py"]), \
             patch("run.load_config", return_value={"experiment": {"shutdown_timeout_seconds": 1.0}}), \
             patch("run.build_network", return_value=network), \
             patch("run.start_capture", return_value=capture), \
             patch("run.execute_protocol", return_value={"status": "success", "failures": []}), \
             patch("run.write_outputs", return_value={"status": "success", "failures": []}), \
             patch("run.cleanup_mininet"):
            exit_code = run.main()

        self.assertEqual(exit_code, 0)
        network.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
