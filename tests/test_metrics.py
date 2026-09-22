import csv
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.testbed.metrics import Capture, analyze_pcap, start_capture, write_csv, write_outputs


class MetricsTests(unittest.TestCase):
    def test_start_capture_uses_sender_interface_and_udp_port_filter(self):
        node = MagicMock()
        config = {"protocol": {"sender": "sender", "port": 5000}, "wireless": {"interface": "bat0"},
                  "output": {"pcap": "/tmp/capture.pcap"}}
        with patch("scripts.testbed.metrics.open", unittest.mock.mock_open()):
            capture = start_capture(config, {"sender": node})
        self.assertEqual(node.popen.call_args.args[0],
                         ["tcpdump", "-i", "bat0", "-w", "-", "udp", "port", "5000"])
        capture.stop = MagicMock()

    def test_start_failure_closes_pcap(self):
        node = MagicMock()
        node.popen.side_effect = OSError("no tcpdump")
        mocked_open = unittest.mock.mock_open()
        with patch("scripts.testbed.metrics.open", mocked_open):
            with self.assertRaises(OSError):
                Capture(node, "sender", "bat0", 5000, "/tmp/capture.pcap").start()
        mocked_open.return_value.close.assert_called_once()

    def test_stop_sends_sigint_and_is_idempotent(self):
        process = MagicMock()
        process.poll.side_effect = [None, 0]
        process.communicate.return_value = (b"", b"done")
        capture = Capture(MagicMock(), "sender", "bat0", 5000, "/tmp/none.pcap")
        capture.process, capture.started_at = process, 1.0
        capture._pcap_file = MagicMock()
        with patch("scripts.testbed.metrics.analyze_pcap", return_value={"valid": True, "packet_count": 0}):
            first = capture.stop()
            second = capture.stop()
        process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertIs(first, second)
        self.assertEqual(first["stderr"], "done")

    def test_stop_terminates_then_kills_on_timeouts(self):
        process = MagicMock()
        process.poll.side_effect = [None, None, None, 0]
        process.communicate.side_effect = [subprocess.TimeoutExpired("tcpdump", 1),
                                           subprocess.TimeoutExpired("tcpdump", 1), (b"", b"")]
        capture = Capture(MagicMock(), "sender", "bat0", 5000, "/tmp/none.pcap")
        capture.process, capture.started_at, capture._pcap_file = process, 1.0, MagicMock()
        with patch("scripts.testbed.metrics.analyze_pcap", return_value={"valid": True, "packet_count": 0}):
            capture.stop()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_analyze_pcap_zero_packets_and_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.pcap"
            path.write_bytes(b"pcap")
            run = MagicMock(returncode=0, stdout="", stderr="")
            with patch("scripts.testbed.metrics.subprocess.run", return_value=run) as mocked_run:
                result = analyze_pcap(str(path), 5000)
        self.assertTrue(result["valid"])
        self.assertEqual(result["packet_count"], 0)
        self.assertIsNone(result["interval_mean_seconds"])
        self.assertEqual(mocked_run.call_args.args[0][-3:], ["udp", "port", "5000"])

    def test_analyze_pcap_one_and_multiple_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.pcap"
            path.write_bytes(b"pcap")
            with patch("scripts.testbed.metrics.subprocess.run",
                       return_value=MagicMock(returncode=0, stdout="10.0 udp\n", stderr="")):
                one = analyze_pcap(str(path), 5000)
            with patch("scripts.testbed.metrics.subprocess.run",
                       return_value=MagicMock(returncode=0, stdout="1.0 x\n2.0 x\n4.0 x\n", stderr="")):
                many = analyze_pcap(str(path), 5000)
        self.assertEqual(one["observed_duration_seconds"], 0)
        self.assertIsNone(one["interval_max_seconds"])
        self.assertEqual(many["interval_mean_seconds"], 1.5)
        self.assertEqual(many["interval_median_seconds"], 1.5)
        self.assertEqual(many["interval_max_seconds"], 2.0)

    def test_analyze_pcap_reports_missing_empty_and_reader_errors(self):
        self.assertFalse(analyze_pcap("/not/a/pcap", 5000)["valid"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.pcap"
            path.touch()
            self.assertFalse(analyze_pcap(str(path), 5000)["valid"])
            path.write_bytes(b"pcap")
            with patch("scripts.testbed.metrics.subprocess.run", side_effect=FileNotFoundError):
                self.assertIn("not available", analyze_pcap(str(path), 5000)["error"])
            with patch("scripts.testbed.metrics.subprocess.run",
                       return_value=MagicMock(returncode=1, stdout="", stderr="bad pcap")):
                self.assertIn("bad pcap", analyze_pcap(str(path), 5000)["error"])

    def test_csv_uses_observed_interval_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.csv"
            write_csv(str(path), {"packet_count": 1, "observed_duration_seconds": 0.0})
            with path.open(encoding="utf-8") as csv_file:
                row = next(csv.DictReader(csv_file))
        self.assertIn("interval_mean_seconds", row)
        self.assertNotIn("latency_mean_seconds", row)
        self.assertEqual(row["interval_mean_seconds"], "")

    def test_summary_statuses_do_not_claim_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"experiment": {"id": "exp"}, "protocol": {"mode": "unicast"},
                      "output": {"summary": str(Path(tmp) / "summary.json"), "csv": str(Path(tmp) / "metrics.csv"),
                                 "pcap": str(Path(tmp) / "capture.pcap")}}
            process = {"status": "success", "sender": {}, "receivers": [{"node": "receiver"}], "failures": []}
            not_started = write_outputs(config, process, {"started": False, "valid": False, "error": "not started"})
            invalid = write_outputs(config, process, {"started": True, "valid": False, "error": "bad pcap"})
            empty = write_outputs(config, process, {"started": True, "valid": True,
                                  "metrics": {"packet_count": 0}})
            observed = write_outputs(config, process, {"started": True, "valid": True,
                                     "metrics": {"packet_count": 1}})
            failed_process = write_outputs(config, {**process, "status": "failed", "failures": ["sender failed"]},
                                           {"started": True, "valid": True, "metrics": {"packet_count": 1}})
        self.assertEqual(not_started["status"], "failed")
        self.assertEqual(invalid["traffic_status"], "unknown")
        self.assertEqual(empty["status"], "inconclusive")
        self.assertTrue(empty["warnings"])
        self.assertEqual(observed["status"], "success")
        self.assertNotIn("delivery", str(observed).lower())
        self.assertEqual(failed_process["status"], "failed")
