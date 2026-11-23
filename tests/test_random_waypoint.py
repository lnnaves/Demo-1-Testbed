"""TESTE AUTOMÁTICO (D14): Random Waypoint (D19), sem rede nem containers.

Confere a definição do modelo (destinos na área, velocidades e pausas nas
faixas, trechos retos, começo na posição, mesma seed = mesmo caminho), a
distribuição dos sorteios (teste estatístico), o config e os arquivos da
execução. Que o nó realmente percorre o caminho no rádio emulado só a
execução REAL no host confirma.
"""

import csv
import json
import math
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.testbed.config import ConfigError, load_config
from scripts.testbed.metadata import collect_metadata
from scripts.testbed.mobility import position_at
from scripts.testbed.random_waypoint import generate
from scripts.testbed.rundir import prepare_run_directory
from tests import test_config

AREA_MIN, AREA_MAX = [0.0, 0.0, 10.0], [150.0, 100.0, 30.0]
START = [20.0, 0.0, 10.0]


def legs(waypoints):
    """Split into moving legs and pauses: (t0, p0, t1, p1, is_pause)."""
    for (t0, *p0), (t1, *p1) in zip(waypoints, waypoints[1:]):
        yield t0, p0, t1, p1, p0 == p1


class DefinitionTests(unittest.TestCase):
    def path(self, seed=7, duration=600.0, speed=(2.0, 6.0), pause=(0.5, 3.0)):
        return generate(START, AREA_MIN, AREA_MAX, speed, pause, seed, duration)

    def test_starts_at_position_at_t0_and_ends_at_duration(self):
        waypoints = self.path()
        self.assertEqual(waypoints[0], [0.0, *START])
        self.assertAlmostEqual(waypoints[-1][0], 600.0)
        self.assertTrue(all(b[0] > a[0] for a, b in zip(waypoints, waypoints[1:])))

    def test_every_point_is_inside_the_area(self):
        for _, *point in self.path():
            for value, low, high in zip(point, AREA_MIN, AREA_MAX):
                self.assertGreaterEqual(value, low - 1e-9)
                self.assertLessEqual(value, high + 1e-9)

    def test_speeds_and_pauses_are_within_their_ranges(self):
        moves = pauses = 0
        waypoints = self.path()
        for index, (t0, p0, t1, p1, is_pause) in enumerate(legs(waypoints)):
            if is_pause:
                pauses += 1
                if index + 2 < len(waypoints):  # the last pause may be cut at duration
                    self.assertGreaterEqual(t1 - t0, 0.5 - 1e-9)
                self.assertLessEqual(t1 - t0, 3.0 + 1e-9)
            else:
                moves += 1
                self.assertGreaterEqual(math.dist(p0, p1) / (t1 - t0), 2.0 - 1e-6)
                self.assertLessEqual(math.dist(p0, p1) / (t1 - t0), 6.0 + 1e-6)
        self.assertGreater(moves, 10)
        self.assertGreater(pauses, 10)

    def test_movement_between_waypoints_is_a_straight_line(self):
        waypoints = self.path(duration=60)
        t0, p0, t1, p1, _ = next(legs(waypoints))
        middle = position_at(waypoints, (t0 + t1) / 2)
        for value, a, b in zip(middle, p0, p1):
            self.assertAlmostEqual(value, (a + b) / 2)

    def test_same_seed_same_path_other_seed_other_path(self):
        self.assertEqual(self.path(seed=20), self.path(seed=20))
        self.assertNotEqual(self.path(seed=20), self.path(seed=21))

    def test_without_pause_the_node_never_stops(self):
        waypoints = self.path(pause=(0.0, 0.0))
        self.assertFalse(any(is_pause for *_, is_pause in legs(waypoints)))

    def test_flat_area_keeps_the_altitude(self):
        waypoints = generate([5, 5, 15], [0, 0, 15], [50, 50, 15], (1, 3), (0, 0), 3, 100)
        self.assertTrue(all(point[3] == 15 for point in waypoints))


class DistributionTests(unittest.TestCase):
    """Teste estatístico: destinos uniformes na área, velocidades uniformes na faixa."""

    def test_destinations_and_speeds_are_uniform(self):
        waypoints = generate([50, 50, 0], [0, 0, 0], [100, 100, 0], (1.0, 5.0), (0, 0), 12345, 200000)
        destinations = [point[1:] for point in waypoints[1:-1]]
        speeds = [math.dist(p0, p1) / (t1 - t0) for t0, p0, t1, p1, _ in legs(waypoints[:-1])]
        self.assertGreater(len(destinations), 3000)

        # 4 x 4 grid: each cell should get 1/16 of the destinations.
        counts = [0] * 16
        for x, y, _ in destinations:
            counts[min(int(x // 25), 3) * 4 + min(int(y // 25), 3)] += 1
        expected = len(destinations) / 16
        chi2 = sum((count - expected) ** 2 / expected for count in counts)
        self.assertLess(chi2, 37.7)  # chi-square, 15 degrees of freedom, p = 0.001

        # Speeds uniform in [1, 5]: mean 3, quartiles 2 and 4.
        speeds.sort()
        self.assertAlmostEqual(sum(speeds) / len(speeds), 3.0, delta=0.1)
        self.assertAlmostEqual(speeds[len(speeds) // 4], 2.0, delta=0.15)
        self.assertAlmostEqual(speeds[3 * len(speeds) // 4], 4.0, delta=0.15)


RW_CONFIG = test_config.CONFIG.replace(
    "    position: [1, 0, 0]\n",
    "    position: [1, 0, 0]\n    mobility:\n      type: random_waypoint\n"
    "      area: [[0, 0, 0], [150, 150, 0]]\n      speed: [2, 6]\n      pause: [0, 2]\n"
    "      seed: 20\n      duration: 60\n",
)


class ConfigAndOutputTests(unittest.TestCase):
    make_root = test_config.ConfigTests.make_root
    assert_rejected = test_config.ConfigTests.assert_rejected

    def load(self, text=RW_CONFIG):
        tmp, root, config = self.make_root(config_text=text)
        with tmp:
            return load_config(config, root=root)

    def test_config_draws_the_path_from_the_seed(self):
        first, second = self.load(), self.load()
        mobility = first["nodes"]["drone1"]["mobility"]
        self.assertEqual(mobility["type"], "random_waypoint")
        self.assertEqual(mobility["waypoints"][0], [0.0, 1.0, 0.0, 0.0])
        self.assertEqual(mobility["waypoints"], second["nodes"]["drone1"]["mobility"]["waypoints"])
        self.assertEqual(mobility["parameters"]["seed"], 20)
        self.assertTrue(mobility["parameters"]["seed_from_config"])

    def test_without_seed_one_is_drawn_and_recorded(self):
        mobility = self.load(RW_CONFIG.replace("      seed: 20\n", ""))["nodes"]["drone1"]["mobility"]
        self.assertIsInstance(mobility["parameters"]["seed"], int)
        self.assertFalse(mobility["parameters"]["seed_from_config"])
        replay = generate([1, 0, 0], [0, 0, 0], [150, 150, 0], [2, 6], [0, 2], mobility["parameters"]["seed"], 60)
        self.assertEqual(mobility["waypoints"], replay)

    def test_invalid_parameters_are_rejected(self):
        cases = {
            "greater than 0": RW_CONFIG.replace("speed: [2, 6]", "speed: [0, 6]"),
            "at least 0": RW_CONFIG.replace("pause: [0, 2]", "pause: [-1, 2]"),
            "greater than maximum": RW_CONFIG.replace("speed: [2, 6]", "speed: [6, 2]"),
            "inside the area": RW_CONFIG.replace("[[0, 0, 0], [150, 150, 0]]", "[[10, 0, 0], [150, 150, 0]]"),
            "positive size in x and in y": RW_CONFIG.replace("[150, 150, 0]]", "[150, 0, 0]]"),
            "required": RW_CONFIG.replace("      duration: 60\n", ""),
            "non-negative integer": RW_CONFIG.replace("seed: 20", "seed: 1.5"),
            "unknown field 'waypoints'": RW_CONFIG.replace("      seed: 20\n", "      seed: 20\n      waypoints: []\n"),
        }
        for match, text in cases.items():
            with self.subTest(match=match):
                self.assert_rejected(text, match)

    def test_run_directory_and_metadata_record_the_drawn_path(self):
        config = self.load()
        config["config_path"] = None  # the temporary config file is already gone
        with tempfile.TemporaryDirectory() as tmp:
            output = prepare_run_directory(config, tmp, now=datetime(2026, 11, 23, 16, 0, 0))
            path = output["trajectories"]["drone1"]
            with open(path, newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), len(config["nodes"]["drone1"]["mobility"]["waypoints"]))
            self.assertEqual(float(rows[0]["x"]), 1.0)
            # The file is a valid trajectory CSV: it can be replayed as waypoints_csv.
            replay = test_config.CONFIG.replace(
                "    position: [1, 0, 0]\n",
                f"    position: [1, 0, 0]\n    mobility:\n      type: trajectory\n      waypoints_csv: {path}\n",
            )
            tmp2, root, config_file = self.make_root(config_text=replay)
            with tmp2:
                replayed = load_config(config_file, root=root)["nodes"]["drone1"]["mobility"]["waypoints"]
            self.assertEqual(len(replayed), len(rows))
            metadata = collect_metadata(config)
            entry = metadata["trajectories"]["drone1"]
            self.assertEqual(entry["source"], "random_waypoint")
            self.assertEqual(entry["random_waypoint"]["seed"], 20)
            self.assertEqual(len(entry["sha256"]), 64)
            json.dumps(metadata)  # metadata.json must stay serializable


if __name__ == "__main__":
    unittest.main()
