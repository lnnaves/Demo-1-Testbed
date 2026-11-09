"""TESTE AUTOMÁTICO (D14): nós FALSOS, sem rádio nem containers.

Verifica a lógica de mobilidade e de observação (D15): interpolação da
trajetória, chamadas a setPosition, positions.csv, leitura de `batctl o` e
`iw station dump`, validação do config e fim por duração. Não confirma
nada sobre o rádio emulado: isso só a execução REAL no host confirma.
"""

import csv
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.testbed.config import ConfigError, load_config
from scripts.testbed.mobility import MobilityController, position_at
from scripts.testbed.observer import NetworkObserver, parse_originators, parse_station_dump
from scripts.testbed.runner import execute_protocol
from tests import test_config
from tests.test_runner import FakeNode, FakeProcess, base_config

CONFIG = test_config.CONFIG

NAMES = {"02:00:00:00:00:01": "gcs", "02:00:00:00:00:02": "drone1", "02:00:00:00:00:03": "drone2"}

# Shape of real outputs (batman-adv 2025.5, iw), including the bracketed-paste
# escape that bash on ubuntu:24.04 adds (CLAUDE.md).
BATCTL_O = """\x1b[?2004h[B.A.T.M.A.N. adv 2025.5, MainIF/MAC: dr1-wlan0/02:00:00:00:00:02 (bat0/aa:bb:cc:dd:ee:ff BATMAN_IV)]
   Originator        last-seen (#/255) Nexthop           [outgoingIF]
 * 02:00:00:00:00:01    0.584s   (251) 02:00:00:00:00:01 [ dr1-wlan0]
   02:00:00:00:00:01    0.584s   (198) 02:00:00:00:00:03 [ dr1-wlan0]
 * 02:00:00:00:00:03    1.020s   (255) 02:00:00:00:00:03 [ dr1-wlan0]
"""
STATION_DUMP = """Station 02:00:00:00:00:01 (on dr1-wlan0)
\tinactive time:\t120 ms
\trx bytes:\t1234
\ttx retries:\t7
\ttx failed:\t2
\tsignal:  \t-82 [-82] dBm
\tsignal avg:\t-80 [-80] dBm
\ttx bitrate:\t6.0 MBit/s
Station 02:00:00:00:00:03 (on dr1-wlan0)
\tinactive time:\t40 ms
\tsignal:  \t-55 [-55] dBm
\ttx bitrate:\t54.0 MBit/s
"""


class FakeStation:
    def __init__(self):
        self.positions = []
        self.commands = []

    def setPosition(self, value):
        self.positions.append(value)

    def cmd(self, command):
        self.commands.append(command)
        if command.startswith("batctl o"):
            return BATCTL_O
        return STATION_DUMP


def scenario_config(trajectory=True):
    nodes = {
        "gcs": {"position": [0.0, 0.0, 0.0], "container_name": "gcs0", "mobility": {"type": "static"}},
        "drone1": {"position": [20.0, 0.0, 0.0], "container_name": "dr1", "mobility": {"type": "static"}},
    }
    if trajectory:
        nodes["drone1"]["mobility"] = {"type": "trajectory", "waypoints": [[0, 20, 0, 0], [1, 120, 0, 0]]}
    return {"nodes": nodes}


class PositionTests(unittest.TestCase):
    def test_linear_interpolation_and_parking(self):
        waypoints = [[0, 20, 0, 0], [10, 120, 0, 0], [20, 120, 50, 0]]
        self.assertEqual(position_at(waypoints, -1), [20, 0, 0])
        self.assertEqual(position_at(waypoints, 5), [70, 0, 0])
        self.assertEqual(position_at(waypoints, 15), [120, 25, 0])
        self.assertEqual(position_at(waypoints, 99), [120, 50, 0])


class MobilityControllerTests(unittest.TestCase):
    def test_moves_only_after_sender_start_and_logs_every_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            nodes = {"gcs": FakeStation(), "drone1": FakeStation()}
            locks = {name: threading.Lock() for name in nodes}
            path = str(Path(tmp) / "positions.csv")
            controller = MobilityController(scenario_config(), nodes, path, locks).start()
            time.sleep(0.25)
            self.assertEqual(nodes["drone1"].positions, [])  # nothing moves before the sender
            controller.sender_started()
            time.sleep(0.45)
            result = controller.stop()
            rows = list(csv.DictReader(open(path, encoding="utf-8")))

        self.assertEqual(result["moving_nodes"], ["drone1"])
        self.assertEqual(result["errors"], [])
        self.assertGreaterEqual(len(nodes["drone1"].positions), 3)
        self.assertEqual(nodes["gcs"].positions, [])  # static nodes are never moved
        first_x = float(nodes["drone1"].positions[0].split(",")[0])
        last_x = float(nodes["drone1"].positions[-1].split(",")[0])
        self.assertLess(first_x, 35)
        self.assertGreater(last_x, first_x)
        self.assertEqual({row["node"] for row in rows}, {"gcs", "drone1"})
        self.assertTrue(all(row["x"] == "0.000" for row in rows if row["node"] == "gcs"))

    def test_setposition_errors_are_reported_not_raised(self):
        class Broken(FakeStation):
            def setPosition(self, value):
                raise RuntimeError("wmediumd gone")

        with tempfile.TemporaryDirectory() as tmp:
            nodes = {"gcs": FakeStation(), "drone1": Broken()}
            controller = MobilityController(
                scenario_config(), nodes, str(Path(tmp) / "p.csv"), {n: threading.Lock() for n in nodes}
            ).start()
            controller.sender_started()
            time.sleep(0.15)
            result = controller.stop()

        self.assertTrue(result["errors"])
        self.assertIn("wmediumd gone", result["errors"][0])


class ObserverTests(unittest.TestCase):
    def test_parses_originator_table_with_node_names(self):
        rows = parse_originators(BATCTL_O, NAMES)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {"destination": "gcs", "best": 1, "next_hop": "gcs", "tq": 251,
                                   "last_seen_s": 0.584, "interface": "dr1-wlan0"})
        self.assertEqual((rows[1]["best"], rows[1]["next_hop"], rows[1]["tq"]), (0, "drone2", 198))

    def test_parses_station_dump(self):
        rows = parse_station_dump(STATION_DUMP, NAMES)

        self.assertEqual(rows[0], {"neighbor": "gcs", "signal_dbm": -82, "tx_bitrate_mbps": 6.0,
                                   "tx_retries": 7, "tx_failed": 2, "inactive_ms": 120})
        self.assertEqual((rows[1]["neighbor"], rows[1]["signal_dbm"], rows[1]["tx_retries"]), ("drone2", -55, None))

    def test_samples_every_node_and_writes_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            nodes = {"gcs": FakeStation(), "drone1": FakeStation()}
            observer = NetworkObserver(
                scenario_config(), nodes, NAMES, str(Path(tmp) / "routes.csv"), str(Path(tmp) / "signal.csv"),
                {n: threading.Lock() for n in nodes}, clock=lambda: 1.5,
            ).start()
            time.sleep(0.1)
            result = observer.stop()
            routes = list(csv.DictReader(open(Path(tmp) / "routes.csv", encoding="utf-8")))
            signal = list(csv.DictReader(open(Path(tmp) / "signal.csv", encoding="utf-8")))

        self.assertGreaterEqual(result["samples"], 2)  # first sample + last picture at stop
        self.assertIn("iw dev dr1-wlan0 station dump 2>&1", nodes["drone1"].commands)
        self.assertEqual({row["node"] for row in routes}, {"gcs", "drone1"})
        self.assertEqual(routes[0]["t"], "1.500")
        self.assertEqual(signal[0]["signal_dbm"], "-82")


MOBILE_CONFIG = CONFIG.replace(
    "    position: [1, 0, 0]\n",
    "    position: [1, 0, 0]\n    mobility:\n      type: trajectory\n      waypoints:\n"
    "        - [0, 1, 0, 0]\n        - [30, 150, 0, 0]\n",
)


class MobilityConfigTests(unittest.TestCase):
    # Reuse the helpers of test_config without re-running its tests here.
    make_root = test_config.ConfigTests.make_root
    assert_rejected = test_config.ConfigTests.assert_rejected

    def load(self, text, files=None):
        tmp, root, config = self.make_root(config_text=text)
        with tmp:
            for name, content in (files or {}).items():
                (root / name).write_text(content, encoding="utf-8")
            return load_config(config, root=root)

    def test_trajectory_and_static_default(self):
        loaded = self.load(MOBILE_CONFIG)

        self.assertEqual(loaded["nodes"]["drone1"]["mobility"]["waypoints"], [[0, 1, 0, 0], [30, 150, 0, 0]])
        self.assertEqual(loaded["nodes"]["gcs"]["mobility"], {"type": "static"})
        self.assertEqual(loaded["experiment"]["end"], "sender")

    def test_waypoints_from_csv(self):
        text = MOBILE_CONFIG.replace(
            "      waypoints:\n        - [0, 1, 0, 0]\n        - [30, 150, 0, 0]\n",
            "      waypoints_csv: path.csv\n",
        )
        loaded = self.load(text, {"path.csv": "t,x,y,z\n0,1,0,0\n10,50,0,0\n"})

        self.assertEqual(loaded["nodes"]["drone1"]["mobility"]["waypoints"], [[0, 1, 0, 0], [10, 50, 0, 0]])

    def test_invalid_trajectories_are_rejected(self):
        cases = {
            "must equal position": MOBILE_CONFIG.replace("- [0, 1, 0, 0]", "- [0, 5, 0, 0]"),
            "at t=0": MOBILE_CONFIG.replace("- [0, 1, 0, 0]", "- [2, 1, 0, 0]"),
            "must increase": MOBILE_CONFIG.replace("- [30, 150, 0, 0]", "- [0, 150, 0, 0]").replace(
                "- [0, 1, 0, 0]", "- [0, 1, 0, 0]\n        - [5, 2, 0, 0]"),
            "at least two": MOBILE_CONFIG.replace("        - [30, 150, 0, 0]\n", ""),
            r"\[t, x, y, z\]": MOBILE_CONFIG.replace("- [30, 150, 0, 0]", "- [30, 150]"),
            "'static' or 'trajectory'": MOBILE_CONFIG.replace("type: trajectory", "type: random"),
            "unknown field": MOBILE_CONFIG.replace("type: trajectory", "type: trajectory\n      speed: 3"),
        }
        for match, text in cases.items():
            with self.subTest(match=match):
                self.assert_rejected(text, match)

    def test_end_mode(self):
        loaded = self.load(CONFIG.replace("  duration_seconds: 2\n", "  duration_seconds: 2\n  end: duration\n"))
        self.assertEqual(loaded["experiment"]["end"], "duration")
        self.assert_rejected(CONFIG.replace("  duration_seconds: 2\n", "  duration_seconds: 2\n  end: never\n"),
                             "experiment.end")


class EndByDurationTests(unittest.TestCase):
    def setUp(self):
        popen = patch("scripts.testbed.runner.container_popen",
                      side_effect=lambda node, command, **kwargs: node.popen(command, **kwargs))
        popen.start()
        self.addCleanup(popen.stop)

    def run_timing_out(self, end):
        config = base_config()
        config["experiment"]["end"] = end
        started = []
        nodes = {
            "gcs": FakeNode("gcs"),
            "drone1": FakeNode("drone1", process_factory=lambda c: FakeProcess(
                returncode=-15, communicate_effects=["timeout"])),
        }
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes, started.append)
        return result, started

    def test_duration_end_stops_sender_normally(self):
        result, started = self.run_timing_out("duration")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["sender"]["ended_by"], "testbed")
        self.assertEqual(result["receivers"][0]["ended_by"], "testbed")
        self.assertEqual(len(started), 1)

    def test_sender_end_keeps_timeout_as_failure(self):
        result, _started = self.run_timing_out("sender")

        self.assertEqual(result["failures"], ["sender timed out"])
        self.assertEqual(result["sender"]["ended_by"], "testbed")

    def test_sender_that_exits_alone_is_marked_self(self):
        config = base_config()
        nodes = {"gcs": FakeNode("gcs"), "drone1": FakeNode("drone1")}
        with patch("scripts.testbed.runner.time.sleep", return_value=None):
            result = execute_protocol(config, nodes)

        self.assertEqual(result["sender"]["ended_by"], "self")


if __name__ == "__main__":
    unittest.main()
