"""TESTE AUTOMÁTICO (D14): dados SINTÉTICOS, sem rede nem PCAPs reais.

Verifica a lógica da semana 5 (D16): latência e perda por janela de tempo e
por faixa de distância, cruzadas com posições, rotas e sinal; janelas vazias;
config `analysis`. Os números reais (exp-08/exp-09) só valem conferidos na
execução REAL no host.
"""

import csv
import tempfile
import unittest
from pathlib import Path

from scripts.testbed.config import ConfigError, load_config
from scripts.testbed.windows import (
    Positions,
    analyze_windows,
    describe_first_loss,
    distance_bands,
    first_loss,
    time_windows,
)
from tests import test_config

CONFIG = test_config.CONFIG

T0 = 1000.0


def row(t, status="delivered", latency_ms=1.0):
    sent_at = T0 + t
    delivered = status == "delivered"
    return {
        "flow": "udp 4000->5000",
        "sent_at": sent_at,
        "received_at": sent_at + latency_ms / 1000 if delivered else None,
        "latency_ms": latency_ms if delivered else None,
        "status": status,
        "duplicates": 0,
    }


def moving_away():
    """gcs parked at 0; drone1 at x = 10 * t (10 m/s), sampled every 0.1 s."""
    samples = {"gcs": [], "drone1": []}
    for step in range(0, 101):
        t = step / 10
        samples["gcs"].append((T0 + t, 0.0, 0.0, 0.0))
        samples["drone1"].append((T0 + t, 10.0 * t, 0.0, 0.0))
    return Positions(samples)


class WindowTests(unittest.TestCase):
    def test_packet_goes_to_the_window_of_its_send_instant_and_empty_windows_are_kept(self):
        rows = [row(0.1), row(0.5, "lost"), row(2.2, latency_ms=3.0)]
        table = time_windows(rows, "drone1", "gcs", T0, T0 + 3.0, 1.0, moving_away(), [], [])
        self.assertEqual([w["t_start"] for w in table], [0.0, 1.0, 2.0])
        self.assertEqual((table[0]["sent"], table[0]["delivered"], table[0]["lost"]), (2, 1, 1))
        # Nothing sent is not everything lost: sent=0, no loss rate, no latency.
        self.assertEqual(table[1]["sent"], 0)
        self.assertIsNone(table[1]["loss_rate"])
        self.assertIsNone(table[1]["latency_median_ms"])
        # Distance still filled (middle of the window: t = 1.5 s -> 15 m).
        self.assertAlmostEqual(table[1]["distance_m"], 15.0)
        self.assertEqual(table[2]["latency_max_ms"], 3.0)

    def test_route_and_signal_come_from_the_readings_inside_the_window(self):
        routes = [
            {"time": T0 + 0.02, "next_hop": "gcs", "tq": 230},
            {"time": T0 + 1.02, "next_hop": "gcs", "tq": 200},
            {"time": T0 + 1.60, "next_hop": "drone2", "tq": 190},
        ]
        signal = [
            {"time": T0 + 1.02, "neighbor": "gcs", "signal_dbm": -88.0, "tx_bitrate_mbps": 13.0},
            {"time": T0 + 1.60, "neighbor": "drone2", "signal_dbm": -70.0, "tx_bitrate_mbps": 13.0},
        ]
        table = time_windows([row(0.1)], "drone1", "gcs", T0, T0 + 3.0, 1.0, None, routes, signal)
        self.assertEqual((table[0]["next_hop"], table[0]["tq"]), ("gcs", 230))
        # A change inside the window shows both hops; TQ and signal are from the last reading.
        self.assertEqual((table[1]["next_hop"], table[1]["tq"], table[1]["signal_dbm"]), ("gcs|drone2", 190, -70.0))
        self.assertAlmostEqual(table[1]["route_read_t"], 1.6)
        # No reading in the window: left empty, never guessed.
        self.assertIsNone(table[2]["next_hop"])
        self.assertIsNone(table[2]["signal_dbm"])
        self.assertIsNone(table[0]["distance_m"])

    def test_distance_bands_use_the_distance_at_each_send_instant(self):
        rows = [row(0.5), row(0.95), row(1.05, "lost"), row(3.0)]
        routes = [{"time": T0, "next_hop": "gcs", "tq": 230}, {"time": T0 + 2.5, "next_hop": "drone2", "tq": 190}]
        table = distance_bands(rows, "drone1", "gcs", T0, 10.0, moving_away(), routes)
        self.assertEqual([(b["distance_from_m"], b["sent"], b["lost"]) for b in table], [(0.0, 2, 0), (10.0, 1, 1), (30.0, 1, 0)])
        self.assertEqual(table[2]["next_hops"], "drone2")
        self.assertAlmostEqual(table[0]["t_first"], 0.5)

    def test_first_loss_reports_time_and_distance(self):
        rows = [row(0.5), row(4.2, "lost"), row(5.0, "lost")]
        loss = first_loss(rows, "drone1", "gcs", T0, moving_away())
        self.assertEqual(loss, {"t": 4.2, "distance_m": 42.0})
        self.assertEqual(describe_first_loss("drone1->gcs", loss), "drone1->gcs: first loss at t=4.2s, at 42 m")
        self.assertIsNone(first_loss([row(0.5)], "drone1", "gcs", T0, None))
        self.assertEqual(describe_first_loss("drone1->gcs", None), "drone1->gcs: no loss")

    def test_analyze_windows_writes_both_tables_from_run_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with (run / "latency-drone1-gcs.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["flow", "ip_id", "ip_payload_bytes", "sent_at", "received_at", "latency_ms", "status", "duplicates"])
                writer.writerow(["udp 4000->5000", 1, 9, f"{T0 + 0.2:.6f}", f"{T0 + 0.201:.6f}", "1.000", "delivered", 0])
                writer.writerow(["udp 4000->5000", 2, 9, f"{T0 + 1.2:.6f}", "", "", "lost", 0])
            with (run / "positions.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["time", "t", "node", "x", "y", "z"])
                for t in (0.0, 2.0):
                    writer.writerow([T0 + t, t, "gcs", 0, 0, 0])
                    writer.writerow([T0 + t, t, "drone1", 20 + 10 * t, 0, 0])
            (run / "routes.csv").write_text(
                "time,t,node,destination,best,next_hop,tq,last_seen_s,interface\n"
                f"{T0 + 0.1},0.1,drone1,gcs,1,gcs,200,0.1,dr1-wlan0\n"
                f"{T0 + 0.1},0.1,drone1,gcs,0,drone2,90,0.1,dr1-wlan0\n"
                f"{T0 + 0.1},0.1,gcs,drone1,1,drone1,199,0.1,gcs0-wlan0\n"
            )
            (run / "signal.csv").write_text(
                "time,t,node,neighbor,signal_dbm,tx_bitrate_mbps,tx_retries,tx_failed,inactive_ms\n"
                f"{T0 + 0.1},0.1,drone1,gcs,-70,13.0,0,0,10\n"
            )
            result = analyze_windows(run, {"drone1->gcs": str(run / "latency-drone1-gcs.csv")}, T0, T0 + 2.0)
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["first_loss"]["drone1->gcs"], {"t": 1.2, "distance_m": 32.0})
            with open(result["files"]["drone1->gcs"]["windows"], newline="") as handle:
                windows = list(csv.DictReader(handle))
            self.assertEqual(len(windows), 2)
            self.assertEqual(windows[0]["next_hop"], "gcs")  # only the best route
            self.assertEqual((windows[0]["tq"], windows[0]["signal_dbm"], windows[0]["distance_m"]), ("200", "-70", "25.0"))
            self.assertEqual((windows[1]["sent"], windows[1]["lost"], windows[1]["latency_median_ms"]), ("1", "1", ""))
            with open(result["files"]["drone1->gcs"]["distance"], newline="") as handle:
                bands = list(csv.DictReader(handle))
            self.assertEqual([(b["distance_from_m"], b["sent"]) for b in bands], [("20.0", "1"), ("30.0", "1")])

    def test_missing_positions_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "latency-a-b.csv").write_text(
                "flow,ip_id,ip_payload_bytes,sent_at,received_at,latency_ms,status,duplicates\n"
                f"icmp,1,64,{T0 + 0.2},{T0 + 0.21},10.000,delivered,0\n"
            )
            result = analyze_windows(run, {"a->b": str(run / "latency-a-b.csv")}, T0, T0 + 1.0)
            self.assertEqual(len(result["errors"]), 1)
            self.assertIn("positions.csv", result["errors"][0])
            self.assertIn("a->b", result["files"])


class AnalysisConfigTests(unittest.TestCase):
    # Reuses the helpers without re-running every config test.
    make_root = test_config.ConfigTests.make_root
    assert_rejected = test_config.ConfigTests.assert_rejected

    def test_analysis_defaults_and_values(self):
        tmp, root, config = self.make_root()
        with tmp:
            self.assertEqual(load_config(config, root=root)["analysis"], {"window_seconds": 1.0, "distance_bin_meters": 10.0})
        tmp, root, config = self.make_root(CONFIG + "analysis:\n  window_seconds: 0.5\n  distance_bin_meters: 5\n")
        with tmp:
            self.assertEqual(load_config(config, root=root)["analysis"], {"window_seconds": 0.5, "distance_bin_meters": 5.0})

    def test_analysis_section_is_strict_and_positive(self):
        self.assert_rejected(CONFIG + "analysis:\n  window: 1\n", "analysis: unknown field 'window'")
        with self.assertRaises(ConfigError):
            tmp, root, config = self.make_root(CONFIG + "analysis:\n  window_seconds: 0\n")
            with tmp:
                load_config(config, root=root)


if __name__ == "__main__":
    unittest.main()
