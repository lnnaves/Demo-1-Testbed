"""TESTE AUTOMÁTICO com PCAPs SINTÉTICOS (D14).

Os PCAPs daqui são montados à mão, em memória/arquivo temporário, para
provocar casos difíceis de obter na rede real (perda em rajada, duplicata,
ID do IP repetido, fragmentos). Não confirmam nada sobre a rede: a
confirmação é sempre a execução REAL no host.
"""

import struct
import tempfile
import unittest
from pathlib import Path

from scripts.testbed.latency import (
    MATCH_WINDOW_SECONDS,
    analyze,
    analyze_run,
    match_direction,
    summarize,
)
from scripts.testbed.pcap import PcapError, read_pcap

SENDER = "192.168.123.2"
GCS = "192.168.123.1"
DRONE2 = "192.168.123.3"
BROADCAST = "192.168.123.255"
T0 = 1_800_000_000.0


def ip_packet(src, dst, ip_id, sport=40000, dport=5000, payload=b"\x00", protocol=17,
              fragment_offset=0, more_fragments=False, df=True, l4=None):
    if l4 is None:
        l4 = struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload if protocol == 17 else payload
    flags = (0x4000 if df else 0) | (0x2000 if more_fragments else 0) | (fragment_offset // 8)
    header = struct.pack(
        ">BBHHHBBH4s4s", 0x45, 0, 20 + len(l4), ip_id, flags, 64, protocol, 0,
        bytes(int(x) for x in src.split(".")), bytes(int(x) for x in dst.split(".")),
    )
    return header + l4


def ethernet(ip=None, ethertype=0x0800, payload=b""):
    return b"\xff" * 6 + b"\x02" * 6 + struct.pack(">H", ethertype) + (ip if ip is not None else payload)


def write_pcap(path, packets, linktype=1):
    """packets: list of (timestamp, frame)."""
    data = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype)
    for timestamp, frame in packets:
        seconds = int(timestamp)
        micro = int(round((timestamp - seconds) * 1e6))
        data += struct.pack("<IIII", seconds, micro, len(frame), len(frame)) + frame
    Path(path).write_bytes(data)


class PcapReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return Path(self.tmp.name) / name

    def test_reads_fields_and_counts_non_ip(self):
        write_pcap(self.path("a.pcap"), [
            (T0, ethernet(ethertype=0x0806, payload=b"\x00" * 28)),
            (T0 + 0.001, ethernet(ip_packet(SENDER, GCS, 1234, sport=37347))),
        ])
        contents = read_pcap(self.path("a.pcap"))

        self.assertEqual(contents.non_ipv4, 1)
        self.assertEqual(len(contents.datagrams), 1)
        datagram = contents.datagrams[0]
        self.assertEqual((datagram.src, datagram.dst, datagram.protocol), (SENDER, GCS, 17))
        self.assertEqual((datagram.sport, datagram.dport, datagram.ip_id, datagram.size), (37347, 5000, 1234, 9))
        self.assertAlmostEqual(datagram.first_seen, T0 + 0.001, places=5)

    def test_groups_fragments_by_ip_id(self):
        l4 = struct.pack(">HHHH", 40000, 5000, 24, 0) + b"x" * 16  # 24 bytes of UDP
        write_pcap(self.path("f.pcap"), [
            (T0, ethernet(ip_packet(SENDER, GCS, 77, l4=l4[:16], more_fragments=True, df=False))),
            (T0 + 0.002, ethernet(ip_packet(SENDER, GCS, 77, l4=l4[16:], fragment_offset=16, df=False))),
        ])
        [datagram] = read_pcap(self.path("f.pcap")).datagrams

        self.assertTrue(datagram.complete)
        self.assertEqual((datagram.fragments, datagram.size, datagram.dport), (2, 24, 5000))
        self.assertAlmostEqual(datagram.last_seen - datagram.first_seen, 0.002, places=5)

    def test_missing_fragment_leaves_datagram_incomplete(self):
        write_pcap(self.path("f.pcap"), [
            (T0, ethernet(ip_packet(SENDER, GCS, 77, l4=b"y" * 16, more_fragments=True, df=False))),
        ])
        [datagram] = read_pcap(self.path("f.pcap")).datagrams

        self.assertFalse(datagram.complete)

    def test_rejects_files_that_are_not_pcap(self):
        self.path("x.pcap").write_bytes(b"not a pcap at all, sorry")
        with self.assertRaises(PcapError):
            read_pcap(self.path("x.pcap"))


def sent_received(pairs):
    """pairs: (ip_id, sent offset, received offset or None) -> two capture lists."""
    sent = [(T0 + s, ethernet(ip_packet(SENDER, GCS, ip_id))) for ip_id, s, _r in pairs]
    received = [(T0 + r, ethernet(ip_packet(SENDER, GCS, ip_id))) for ip_id, _s, r in pairs if r is not None]
    return sent, sorted(received)


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def analyze_pairs(self, sent, received, extra=None):
        write_pcap(Path(self.tmp.name) / "s.pcap", sent)
        write_pcap(Path(self.tmp.name) / "r.pcap", received)
        contents = {"drone1": read_pcap(Path(self.tmp.name) / "s.pcap"),
                    "gcs": read_pcap(Path(self.tmp.name) / "r.pcap")}
        summary, rows = analyze({"drone1": SENDER, "gcs": GCS}, contents, BROADCAST)
        return summary, rows

    def test_identical_payloads_are_told_apart_by_ip_id(self):
        sent, received = sent_received([(100 + i * 23, i * 0.05, i * 0.05 + 0.001) for i in range(20)])
        summary, rows = self.analyze_pairs(sent, received)

        total = summary["directions"]["drone1->gcs"]["total"]
        self.assertEqual((total["sent"], total["delivered"], total["lost"]), (20, 20, 0))
        self.assertEqual(total["latency_median_ms"], 1.0)
        self.assertIsNotNone(total["latency_p95_ms"])
        self.assertIsNone(total["latency_p99_ms"])
        self.assertEqual(list(summary["directions"]["drone1->gcs"]["flows"]), ["udp 40000->5000"])

    def test_loss_is_attributed_to_the_right_packet_and_burst_is_measured(self):
        pairs = [(10, 0.0, 0.001), (20, 0.1, None), (30, 0.2, None), (40, 0.3, None), (50, 0.4, 0.402),
                 (60, 0.5, None), (70, 0.6, 0.601)]
        summary, rows = self.analyze_pairs(*sent_received(pairs))

        total = summary["directions"]["drone1->gcs"]["total"]
        self.assertEqual([row["status"] for row in rows["drone1->gcs"]],
                         ["delivered", "lost", "lost", "lost", "delivered", "lost", "delivered"])
        self.assertEqual((total["lost"], total["max_loss_burst"]), (4, 3))
        self.assertAlmostEqual(total["loss_rate"], 4 / 7)
        self.assertAlmostEqual(rows["drone1->gcs"][4]["latency_ms"], 2.0, places=2)

    def test_reused_ip_id_outside_window_does_not_hide_a_loss(self):
        # ID 500 is lost; much later another packet with ID 500 arrives.
        late = MATCH_WINDOW_SECONDS + 100
        sent = [(T0, ethernet(ip_packet(SENDER, GCS, 500))), (T0 + late, ethernet(ip_packet(SENDER, GCS, 500)))]
        received = [(T0 + late + 0.001, ethernet(ip_packet(SENDER, GCS, 500)))]
        summary, rows = self.analyze_pairs(sent, received)

        self.assertEqual([row["status"] for row in rows["drone1->gcs"]], ["lost", "delivered"])
        self.assertAlmostEqual(rows["drone1->gcs"][1]["latency_ms"], 1.0, places=2)

    def test_duplicates_and_out_of_order_are_counted(self):
        sent = [(T0, ethernet(ip_packet(SENDER, GCS, 1))), (T0 + 0.01, ethernet(ip_packet(SENDER, GCS, 2)))]
        received = [
            (T0 + 0.012, ethernet(ip_packet(SENDER, GCS, 2))),
            (T0 + 0.015, ethernet(ip_packet(SENDER, GCS, 1))),
            (T0 + 0.016, ethernet(ip_packet(SENDER, GCS, 1))),
        ]
        summary, _rows = self.analyze_pairs(sent, received)

        total = summary["directions"]["drone1->gcs"]["total"]
        self.assertEqual((total["delivered"], total["duplicates"], total["out_of_order"]), (2, 1, 1))

    def test_both_directions_and_non_udp_are_analysed(self):
        sent = [
            (T0, ethernet(ip_packet(SENDER, GCS, 1, sport=5683, dport=5683))),
            (T0 + 0.0105, ethernet(ip_packet(GCS, SENDER, 900, sport=5683, dport=5683))),
            (T0 + 0.02, ethernet(ip_packet(SENDER, GCS, 2, protocol=1, l4=b"\x08\x00" + b"\x00" * 6))),
        ]
        received = [
            (T0 + 0.001, ethernet(ip_packet(SENDER, GCS, 1, sport=5683, dport=5683))),
            (T0 + 0.010, ethernet(ip_packet(GCS, SENDER, 900, sport=5683, dport=5683))),
            (T0 + 0.021, ethernet(ip_packet(SENDER, GCS, 2, protocol=1, l4=b"\x08\x00" + b"\x00" * 6))),
        ]
        summary, _rows = self.analyze_pairs(sent, received)

        directions = summary["directions"]
        self.assertEqual(set(directions), {"drone1->gcs", "gcs->drone1"})
        self.assertEqual(set(directions["drone1->gcs"]["flows"]), {"udp 5683->5683", "icmp"})
        self.assertEqual(directions["gcs->drone1"]["total"]["delivered"], 1)

    def test_broadcast_is_reported_per_receiver(self):
        tmp = Path(self.tmp.name)
        write_pcap(tmp / "s.pcap", [(T0 + i * 0.05, ethernet(ip_packet(SENDER, BROADCAST, i + 1))) for i in range(3)])
        write_pcap(tmp / "g.pcap", [(T0 + i * 0.05 + 0.001, ethernet(ip_packet(SENDER, BROADCAST, i + 1))) for i in range(3)])
        write_pcap(tmp / "d.pcap", [(T0 + 0.002, ethernet(ip_packet(SENDER, BROADCAST, 1)))])
        contents = {"drone1": read_pcap(tmp / "s.pcap"), "gcs": read_pcap(tmp / "g.pcap"),
                    "drone2": read_pcap(tmp / "d.pcap")}
        summary, _rows = analyze({"drone1": SENDER, "gcs": GCS, "drone2": DRONE2}, contents, BROADCAST)

        self.assertEqual(summary["directions"]["drone1->gcs"]["total"]["delivered"], 3)
        self.assertEqual(summary["directions"]["drone1->drone2"]["total"]["delivered"], 1)
        self.assertEqual(summary["directions"]["drone1->drone2"]["total"]["lost"], 2)

    def test_summary_of_nothing_delivered_has_no_latency(self):
        stats = summarize(match_direction([], []))
        self.assertEqual((stats["sent"], stats["loss_rate"], stats["latency_median_ms"]), (0, None, None))


class AnalyzeRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        run_dir = Path(self.tmp.name)
        self.config = {
            "experiment": {"drain_seconds": 1.0},
            "wireless": {"broadcast_ip": BROADCAST},
            "nodes": {"drone1": {"address": SENDER}, "gcs": {"address": GCS}},
            "output": {"directory": str(run_dir), "pcaps": {
                "drone1": str(run_dir / "capture-drone1.pcap"), "gcs": str(run_dir / "capture-gcs.pcap")}},
        }

    def test_writes_one_csv_per_direction_and_warns_near_drain(self):
        sent, received = sent_received([(1, 0.0, 0.0005), (2, 0.05, 0.75)])
        write_pcap(self.config["output"]["pcaps"]["drone1"], sent)
        write_pcap(self.config["output"]["pcaps"]["gcs"], received)

        result = analyze_run(self.config)

        self.assertTrue(result["valid"])
        csv_path = Path(result["files"]["drone1->gcs"])
        self.assertEqual(csv_path.name, "latency-drone1-gcs.csv")
        lines = csv_path.read_text().splitlines()
        self.assertEqual(lines[0], "flow,ip_id,ip_payload_bytes,sent_at,received_at,latency_ms,status,duplicates")
        self.assertEqual(len(lines), 3)
        self.assertTrue(any("drain_seconds" in warning for warning in result["warnings"]))

    def test_unreadable_capture_is_reported_not_raised(self):
        Path(self.config["output"]["pcaps"]["drone1"]).write_bytes(b"garbage")
        result = analyze_run(self.config)

        self.assertFalse(result["valid"])
        self.assertIn("could not read PCAP", result["error"])


if __name__ == "__main__":
    unittest.main()
