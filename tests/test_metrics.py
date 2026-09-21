import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.testbed.metrics import write_csv, write_outputs, write_summary


class MetricsTests(unittest.TestCase):
    def test_write_csv_leaves_latency_empty_without_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.csv"
            write_csv(str(path), duration_seconds=1.25, packet_count=7)
            with path.open(encoding="utf-8") as csv_file:
                rows = list(csv.DictReader(csv_file))

        self.assertEqual(rows[0]["packet_count"], "7")
        self.assertEqual(rows[0]["latency_mean_seconds"], "")
        self.assertEqual(rows[0]["latency_median_seconds"], "")
        self.assertEqual(rows[0]["latency_max_seconds"], "")

    def test_write_summary_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.json"
            csv_path = Path(tmp) / "metrics.csv"
            config = {
                "experiment": {"id": "exp"},
                "output": {"summary": str(summary_path), "csv": str(csv_path), "pcap": str(Path(tmp) / "out.pcap")},
            }
            summary = write_outputs(
                config,
                {"status": "success", "duration_seconds": 0.5, "sender": {}, "receivers": [], "failures": []},
                {"started": True, "packet_count": 1},
            )
            write_summary(str(summary_path), summary)
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(loaded["status"], "success")
        self.assertEqual(loaded["capture"]["packet_count"], 1)


if __name__ == "__main__":
    unittest.main()
