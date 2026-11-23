"""TESTE AUTOMÁTICO (D14): repetições com estatística (D20), sem rede.

Confere a estatística (média, desvio, IC 95% com t de Student contra
valores calculados à parte), as seeds por repetição, a leitura das medidas
do summary.json, a exclusão das repetições que falharam, os arquivos da
campanha e o laço de `run.py --repeat` com execuções falsas. Só a execução
REAL no host confirma uma campanha de verdade.
"""

import contextlib
import csv
import io
import json
import math
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts.testbed.campaign import (
    campaign_directory,
    describe,
    format_table,
    repetition_measures,
    repetition_seeds,
    summarize,
    t_critical,
    write_statistics,
)
from scripts.testbed.config import load_config
from scripts.testbed.rundir import prepare_run_directory
from tests import test_random_waypoint

SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
sys.path.insert(0, SCRIPTS_DIR)
try:
    import run  # noqa: E402
finally:
    sys.path.remove(SCRIPTS_DIR)


def summary(delivered, sent=200, median=1.0, first_loss=None, status="success"):
    return {
        "status": status,
        "failures": [] if status != "failed" else ["sender exited with code 1"],
        "latency": {
            "directions": {"drone1->gcs": {"total": {
                "sent": sent, "delivered": delivered, "latency_min_ms": 0.1, "latency_median_ms": median,
                "latency_mean_ms": median, "latency_p95_ms": 2 * median, "latency_max_ms": 3 * median,
                "jitter_mean_ms": 0.2, "max_loss_burst": sent - delivered,
            }}},
            "windows": {"first_loss": {"drone1->gcs": first_loss}},
        },
    }


class StatisticsTests(unittest.TestCase):
    def test_t_critical_values(self):
        self.assertEqual(t_critical(4), 2.776)
        self.assertEqual(t_critical(9), 2.262)
        self.assertEqual(t_critical(35), 2.042)  # between rows: the lower (more conservative) row
        self.assertEqual(t_critical(500), 1.960)

    def test_mean_stdev_and_confidence_interval(self):
        # values 2, 4, 4, 4, 5, 5, 7, 9: mean 5, sample stdev sqrt(32/7)
        stats = describe([2, 4, 4, 4, 5, 5, 7, 9])
        self.assertAlmostEqual(stats["mean"], 5.0)
        self.assertAlmostEqual(stats["stdev"], math.sqrt(32 / 7))
        half = 2.365 * math.sqrt(32 / 7) / math.sqrt(8)
        self.assertAlmostEqual(stats["ci95_low"], 5 - half)
        self.assertAlmostEqual(stats["ci95_high"], 5 + half)
        self.assertEqual((stats["min"], stats["max"], stats["n"]), (2, 9, 8))

    def test_one_or_no_value_has_no_interval(self):
        self.assertIsNone(describe([3.0])["ci95_low"])
        self.assertEqual(describe([3.0])["mean"], 3.0)
        self.assertIsNone(describe([])["mean"])

    def test_seeds_same_or_varied(self):
        self.assertEqual(repetition_seeds({"drone1": 20}, 3, vary_seed=False), {"drone1": 20})
        self.assertEqual(repetition_seeds({"drone1": 20}, 3, vary_seed=True), {"drone1": 22})
        self.assertEqual(repetition_seeds({}, 3, vary_seed=True), {})

    def test_measures_read_from_the_summary(self):
        measures = repetition_measures(summary(150, first_loss={"t": 8.9, "distance_m": 84.0}), 8.1)
        direction = measures["directions"]["drone1->gcs"]
        self.assertEqual(direction["delivery_rate"], 0.75)
        self.assertEqual((direction["first_loss_t_s"], direction["first_loss_distance_m"]), (8.9, 84.0))
        self.assertEqual(direction["latency_p95_ms"], 2.0)
        self.assertEqual(measures["global"]["mesh_ready_seconds"], 8.1)
        self.assertEqual(repetition_measures(None, None)["directions"], {})

    def test_failed_repetitions_are_left_out_and_missing_values_are_skipped(self):
        reps = [
            {"used": True, "measures": repetition_measures(summary(200), 8.0)},  # no loss: no first_loss
            {"used": True, "measures": repetition_measures(summary(180, first_loss={"t": 9, "distance_m": 80}), 9.0)},
            {"used": False, "measures": repetition_measures(summary(0, status="failed"), 10.0)},
        ]
        stats = summarize(reps)
        delivery = stats["directions"]["drone1->gcs"]["delivery_rate"]
        self.assertEqual(delivery["n"], 2)
        self.assertAlmostEqual(delivery["mean"], 0.95)
        self.assertEqual(stats["directions"]["drone1->gcs"]["first_loss_distance_m"]["n"], 1)
        self.assertEqual(stats["global"]["mesh_ready_seconds"]["values"], [8.0, 9.0])
        lines = format_table(stats)
        self.assertTrue(any(line.strip().startswith("delivery_rate") and "95.0%" in line for line in lines))

    def test_files_of_the_campaign(self):
        reps = [{"used": True, "measures": repetition_measures(summary(d), 8.0)} for d in (190, 195, 200)]
        with tempfile.TemporaryDirectory() as tmp:
            directory = campaign_directory("exp", tmp, now=datetime(2026, 11, 23, 17, 0, 0))
            self.assertEqual(directory.name, "2026-11-23_17-00-00-campanha")
            self.assertEqual(campaign_directory("exp", tmp, now=datetime(2026, 11, 23, 17, 0, 0)).name,
                             "2026-11-23_17-00-00-campanha-2")
            files = write_statistics(directory, {"repetitions_requested": 3}, reps, summarize(reps))
            document = json.loads(Path(files["json"]).read_text())
            with open(files["csv"], newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(document["campaign"]["repetitions_requested"], 3)
        delivery = next(r for r in rows if r["scope"] == "drone1->gcs" and r["metric"] == "delivery_rate")
        self.assertEqual(delivery["n"], "3")
        self.assertAlmostEqual(float(delivery["mean"]), 0.975)


class ConfigAndDirectoryTests(unittest.TestCase):
    make_root = test_random_waypoint.test_config.ConfigTests.make_root

    def test_seed_override_changes_only_the_seed(self):
        tmp, root, config = self.make_root(config_text=test_random_waypoint.RW_CONFIG)
        with tmp:
            same = load_config(config, root=root)
            other = load_config(config, root=root, seeds={"drone1": 21})
        self.assertEqual(same["nodes"]["drone1"]["mobility"]["parameters"]["seed"], 20)
        self.assertEqual(other["nodes"]["drone1"]["mobility"]["parameters"]["seed"], 21)
        self.assertNotEqual(same["nodes"]["drone1"]["mobility"]["waypoints"],
                            other["nodes"]["drone1"]["mobility"]["waypoints"])

    def test_repetition_directory_uses_the_given_name(self):
        config = {"experiment": {"id": "exp"}, "protocol": {"sender": "drone1", "receivers": ["gcs"]}}
        with tempfile.TemporaryDirectory() as tmp:
            output = prepare_run_directory(config, tmp, run_name="rep-03")
            self.assertEqual(Path(output["directory"]), (Path(tmp) / "rep-03").resolve())


class RunCampaignTests(unittest.TestCase):
    def test_repeat_runs_every_repetition_and_writes_statistics(self):
        outcomes = [
            (0, {"directory": "d1", "summary": summary(200), "mesh_ready_seconds": 8.0}),
            (1, {"directory": "d2", "summary": summary(0, status="failed"), "mesh_ready_seconds": 9.0}),
            (0, {"directory": "d3", "summary": summary(190), "mesh_ready_seconds": 8.5}),
        ]
        config = {"experiment": {"id": "exp"}, "nodes": {
            "drone1": {"mobility": {"type": "random_waypoint", "parameters": {"seed": 20}}},
            "gcs": {"mobility": {"type": "static"}},
        }}
        with tempfile.TemporaryDirectory() as tmp, \
                patch("sys.argv", ["run.py", "cfg.yml", "--repeat", "3", "--vary-seed", "--output-dir", tmp]), \
                patch("run.load_config", return_value=config), \
                patch("run.run_once", side_effect=outcomes) as run_once, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            exit_code = run.main()
            directory = next((Path(tmp) / "exp").iterdir())
            document = json.loads((directory / "statistics.json").read_text())

        self.assertEqual(exit_code, 1)  # one repetition failed
        names = [call.kwargs["run_name"] for call in run_once.call_args_list]
        seeds = [call.kwargs["seeds"] for call in run_once.call_args_list]
        self.assertEqual(names, ["rep-01", "rep-02", "rep-03"])
        self.assertEqual(seeds, [{"drone1": 20}, {"drone1": 21}, {"drone1": 22}])
        self.assertEqual(document["campaign"]["repetitions_used"], 2)
        self.assertEqual(document["repetitions"][1]["reason"], "sender exited with code 1")
        delivery = document["statistics"]["directions"]["drone1->gcs"]["delivery_rate"]
        self.assertEqual(delivery["values"], [1.0, 0.95])
        self.assertIn("2/3 repetitions used", err.getvalue())

    def test_without_repeat_a_single_run_is_made(self):
        with patch("sys.argv", ["run.py"]), patch("run.run_once", return_value=(0, {})) as run_once:
            self.assertEqual(run.main(), 0)
        run_once.assert_called_once()
        self.assertNotIn("run_name", run_once.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
