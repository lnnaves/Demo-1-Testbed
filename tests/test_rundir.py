import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.testbed.rundir import DEFAULT_OUTPUT_DIR, PROJECT_ROOT, prepare_run_directory

NOW = datetime(2026, 11, 5, 15, 30, 12)


class RunDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.config_file = self.base / "my-config.yml"
        self.config_file.write_text("experiment:\n  id: exp-01\n", encoding="utf-8")

    def config(self):
        return {
            "experiment": {"id": "exp-01"},
            "protocol": {"sender": "drone1", "receivers": ["gcs", "drone2"]},
            "config_path": str(self.config_file),
        }

    def test_creates_experiment_and_timestamp_directory_with_fixed_names(self):
        config = self.config()
        output = prepare_run_directory(config, self.base / "logs", now=NOW)

        run_dir = (self.base / "logs" / "exp-01" / "2026-11-05_15-30-12").resolve()
        self.assertTrue(run_dir.is_dir())
        self.assertEqual(output["directory"], str(run_dir))
        self.assertEqual(output["config"], str(run_dir / "config.yml"))
        # One PCAP per participant, named after the node.
        self.assertEqual(output["pcaps"], {
            "drone1": str(run_dir / "capture-drone1.pcap"),
            "gcs": str(run_dir / "capture-gcs.pcap"),
            "drone2": str(run_dir / "capture-drone2.pcap"),
        })
        self.assertEqual(output["csv"], str(run_dir / "metrics.csv"))
        self.assertEqual(output["summary"], str(run_dir / "summary.json"))
        self.assertIs(config["output"], output)

    def test_copies_trajectory_csvs_but_not_inline_waypoints(self):
        csv_file = self.base / "path.csv"
        csv_file.write_text("t,x,y,z\n0,1,0,0\n10,50,0,0\n", encoding="utf-8")
        config = self.config()
        config["nodes"] = {
            "drone1": {"mobility": {"type": "trajectory", "source": str(csv_file)}},
            "drone2": {"mobility": {"type": "trajectory", "source": "config"}},
            "gcs": {"mobility": {"type": "static"}},
        }
        output = prepare_run_directory(config, self.base / "logs", now=NOW)

        self.assertEqual(list(output["trajectories"]), ["drone1"])
        copy = Path(output["trajectories"]["drone1"])
        self.assertEqual(copy.name, "trajectory-drone1.csv")
        self.assertEqual(copy.read_bytes(), csv_file.read_bytes())

    def test_copies_the_exact_config_file_used(self):
        output = prepare_run_directory(self.config(), self.base, now=NOW)

        self.assertEqual(Path(output["config"]).read_bytes(), self.config_file.read_bytes())

    def test_runs_in_the_same_second_never_share_a_directory(self):
        first = prepare_run_directory(self.config(), self.base, now=NOW)["directory"]
        second = prepare_run_directory(self.config(), self.base, now=NOW)["directory"]
        third = prepare_run_directory(self.config(), self.base, now=NOW)["directory"]

        self.assertEqual(Path(first).name, "2026-11-05_15-30-12")
        self.assertEqual(Path(second).name, "2026-11-05_15-30-12-2")
        self.assertEqual(Path(third).name, "2026-11-05_15-30-12-3")

    def test_works_without_a_config_file(self):
        config = self.config()
        del config["config_path"]
        output = prepare_run_directory(config, self.base, now=NOW)

        self.assertTrue(Path(output["directory"]).is_dir())
        self.assertFalse(Path(output["config"]).exists())

    def test_default_base_directory_is_logs(self):
        self.assertEqual(DEFAULT_OUTPUT_DIR, PROJECT_ROOT / "logs")


if __name__ == "__main__":
    unittest.main()
