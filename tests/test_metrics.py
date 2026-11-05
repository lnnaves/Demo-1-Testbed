import csv
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.testbed.metrics import Capture, CaptureSet, analyze_pcap, start_capture, write_csv, write_outputs


def _capture_config(receivers=("gcs",), bpf_filter=None):
    participants = ["drone1", *receivers]
    return {
        "protocol": {"sender": "drone1", "receivers": list(receivers), "port": 5000},
        "wireless": {"interface": "bat0"},
        "capture": {"filter": bpf_filter},
        "output": {"pcaps": {name: f"/tmp/capture-{name}.pcap" for name in participants}},
    }


def _alive_process():
    process = MagicMock()
    process.poll.return_value = None
    process.communicate.return_value = (b"", b"")
    return process


class MetricsTests(unittest.TestCase):
    def _start(self, config, nodes):
        with patch("scripts.testbed.metrics.open", unittest.mock.mock_open()), \
             patch("scripts.testbed.metrics.subprocess.Popen", side_effect=lambda *a, **k: _alive_process()) as popen, \
             patch("scripts.testbed.metrics.time.sleep"):
            captures = start_capture(config, nodes)
        return captures, [call.args[0] for call in popen.call_args_list]

    def test_start_capture_records_every_participant_without_filter_by_default(self):
        nodes = {"drone1": MagicMock(pid=11), "gcs": MagicMock(pid=22), "drone2": MagicMock(pid=33)}
        captures, commands = self._start(_capture_config(receivers=("gcs", "drone2")), nodes)

        self.assertEqual(commands, [
            ["nsenter", "-t", str(pid), "-n", "tcpdump", "-i", "bat0", "-w", "-"] for pid in (11, 22, 33)
        ])
        self.assertEqual(sorted(captures.captures), ["drone1", "drone2", "gcs"])
        self.assertEqual(captures.captures["gcs"].pcap_path, "/tmp/capture-gcs.pcap")
        for node in nodes.values():
            node.popen.assert_not_called()

    def test_start_capture_applies_configured_filter_as_one_expression(self):
        nodes = {"drone1": MagicMock(pid=11), "gcs": MagicMock(pid=22)}
        _, commands = self._start(_capture_config(bpf_filter="udp port 5000 or icmp"), nodes)

        for command in commands:
            self.assertEqual(command[-1], "udp port 5000 or icmp")

    def test_capture_that_dies_at_start_fails_before_the_protocol(self):
        dead = MagicMock()
        dead.poll.return_value = 1
        dead.communicate.return_value = (b"", b"tcpdump: syntax error in filter expression")
        processes = iter([_alive_process(), dead])
        nodes = {"drone1": MagicMock(pid=11), "gcs": MagicMock(pid=22)}
        with patch("scripts.testbed.metrics.open", unittest.mock.mock_open()), \
             patch("scripts.testbed.metrics.subprocess.Popen", side_effect=lambda *a, **k: next(processes)), \
             patch("scripts.testbed.metrics.time.sleep"), \
             patch("scripts.testbed.metrics.analyze_pcap", return_value={"valid": True, "packet_count": 0}):
            with self.assertRaisesRegex(RuntimeError, "gcs: tcpdump exited unexpectedly.*syntax error"):
                start_capture(_capture_config(bpf_filter="udp prt 5000"), nodes)

    def test_capture_set_reports_sender_metrics_and_every_node(self):
        sender, receiver = MagicMock(), MagicMock()
        sender.stop.return_value = {"pcap": "/s.pcap", "interface": "bat0", "port": 5000, "started": True,
                                    "valid": True, "error": None, "packet_count": 3, "metrics": {"packet_count": 3}}
        receiver.stop.return_value = {"started": True, "valid": False, "error": "PCAP is empty", "packet_count": 0}
        result = CaptureSet({"drone1": sender, "gcs": receiver}, "drone1", None).stop()

        self.assertEqual(result["packet_count"], 3)
        self.assertEqual(result["metrics"], {"packet_count": 3})
        self.assertFalse(result["valid"])
        self.assertEqual(result["error"], "gcs: PCAP is empty")
        self.assertEqual(sorted(result["nodes"]), ["drone1", "gcs"])

    def test_start_failure_closes_pcap(self):
        node = MagicMock(pid=4242)
        mocked_open = unittest.mock.mock_open()
        with patch("scripts.testbed.metrics.open", mocked_open), patch(
            "scripts.testbed.metrics.subprocess.Popen", side_effect=OSError("no tcpdump")
        ):
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

    def test_analyze_pcap_rejects_non_numeric_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.pcap"
            path.write_bytes(b"pcap")
            with patch("scripts.testbed.metrics.subprocess.run",
                       return_value=MagicMock(returncode=0, stdout="not-a-timestamp udp\n", stderr="")):
                result = analyze_pcap(str(path), 5000)

        self.assertFalse(result["valid"])
        self.assertIn("numeric timestamps", result["error"])

    def test_stop_reports_capture_not_started_or_unexpected_exit(self):
        not_started = Capture(MagicMock(), "sender", "bat0", 5000, "/tmp/none.pcap").stop()
        process = MagicMock()
        process.poll.return_value = 1
        process.communicate.return_value = (b"", b"tcpdump error")
        capture = Capture(MagicMock(), "sender", "bat0", 5000, "/tmp/none.pcap")
        capture.process, capture.started_at, capture._pcap_file = process, 1.0, MagicMock()
        with patch("scripts.testbed.metrics.analyze_pcap", return_value={"valid": True, "packet_count": 0}):
            unexpected_exit = capture.stop()

        self.assertEqual(not_started["error"], "capture was not started")
        self.assertIn("exited unexpectedly", unexpected_exit["error"])

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
