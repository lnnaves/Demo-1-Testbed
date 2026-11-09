"""Network latency and loss between participants, from their PCAPs (D13).

The same IP datagram is recognized in the capture of the node that sent it
and in the capture of each node that should receive it by: source and
destination address, protocol, ports, IP payload size and IP identification
(set by the kernel, never by the testbed or the protocol). The payload is
never read. Every IPv4 datagram exchanged between participants is analysed,
in both directions, grouped by flow (protocol + ports).

Latency here is network latency: from the sender's capture interface to the
receiver's capture interface, across every hop. Containers share the host
clock, so the timestamps of different PCAPs are comparable.
"""

from __future__ import annotations

import csv
import ipaddress
import math
from pathlib import Path
from statistics import mean, median
from typing import Any

from .pcap import CaptureContents, Datagram, PcapError, read_pcap

# A received datagram only counts as the sent one if it arrives at most this
# long after it was sent. Linux adds a random step to the IP identification
# of every packet, so an ID comes back after a few thousand packets; without a
# window a lost packet could be matched to a later packet with the same ID,
# hiding the loss behind a huge, false latency.
MATCH_WINDOW_SECONDS = 5.0
# Percentiles are only reported with enough delivered packets to mean
# something (with 20 packets, p95 is already close to the second largest).
MIN_SAMPLES_P95 = 20
MIN_SAMPLES_P99 = 100
# Warn when the largest latency reaches this fraction of drain_seconds: the
# receivers are stopped drain_seconds after the sender ends, so packets sent
# at the very end may be counted as lost because of the testbed.
DRAIN_WARNING_FRACTION = 0.5

CSV_NAME = "latency-{src}-{dst}.csv"
CSV_FIELDS = [
    "flow", "ip_id", "ip_payload_bytes", "sent_at", "received_at",
    "latency_ms", "status", "duplicates",
]


def _is_group_address(address: str, broadcast_ip: str | None) -> bool:
    ip = ipaddress.IPv4Address(address)
    return address == broadcast_ip or ip == ipaddress.IPv4Address("255.255.255.255") or ip.is_multicast


def _flow(datagram: Datagram) -> str:
    if datagram.sport is None or datagram.dport is None:
        return datagram.protocol_name
    return f"{datagram.protocol_name} {datagram.sport}->{datagram.dport}"


def _key(datagram: Datagram) -> tuple:
    return (datagram.src, datagram.dst, datagram.protocol, datagram.sport, datagram.dport, datagram.size, datagram.ip_id)


def match_direction(
    sent: list[Datagram],
    received: list[Datagram],
    window: float = MATCH_WINDOW_SECONDS,
) -> list[dict[str, Any]]:
    """Pair each sent datagram with its copy in the receiver's capture.

    Sent datagrams are taken in send order; each one takes the earliest
    received datagram with the same key that arrived within the window and
    was not taken yet. Later copies within the window are duplicates.
    """
    candidates: dict[tuple, list[Datagram]] = {}
    for datagram in sorted(received, key=lambda item: item.last_seen):
        if datagram.complete:
            candidates.setdefault(_key(datagram), []).append(datagram)
    used: set[int] = set()
    rows = []
    for datagram in sorted(sent, key=lambda item: item.first_seen):
        sent_at = datagram.first_seen
        match = None
        duplicates = 0
        for candidate in candidates.get(_key(datagram), []):
            if id(candidate) in used or candidate.last_seen < sent_at:
                continue
            if candidate.last_seen - sent_at > window:
                break
            if match is None:
                match = candidate
            else:
                duplicates += 1
            used.add(id(candidate))
        rows.append({
            "flow": _flow(datagram),
            "ip_id": datagram.ip_id,
            "ip_payload_bytes": datagram.size,
            "sent_at": sent_at,
            "received_at": match.last_seen if match else None,
            "latency_ms": (match.last_seen - sent_at) * 1000 if match else None,
            "status": "delivered" if match else "lost",
            "duplicates": duplicates,
        })
    return rows


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile."""
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


def _ms(value: float | None) -> float | None:
    # PCAP timestamps have microsecond resolution: 0.001 ms.
    return None if value is None else round(value, 3)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts, loss pattern and latency statistics of matched rows (send order)."""
    latencies = [row["latency_ms"] for row in rows if row["status"] == "delivered"]
    ordered = sorted(latencies)
    lost = len(rows) - len(latencies)
    burst = longest_burst = 0
    out_of_order = 0
    latest_arrival = None
    for row in rows:
        if row["status"] == "lost":
            burst += 1
            longest_burst = max(longest_burst, burst)
            continue
        burst = 0
        if latest_arrival is not None and row["received_at"] < latest_arrival:
            out_of_order += 1
        latest_arrival = row["received_at"] if latest_arrival is None else max(latest_arrival, row["received_at"])
    jitter = [abs(later - earlier) for earlier, later in zip(latencies, latencies[1:])]
    return {
        "sent": len(rows),
        "delivered": len(latencies),
        "lost": lost,
        "loss_rate": lost / len(rows) if rows else None,
        "max_loss_burst": longest_burst,
        "out_of_order": out_of_order,
        "duplicates": sum(row["duplicates"] for row in rows),
        "latency_min_ms": _ms(ordered[0] if ordered else None),
        "latency_median_ms": _ms(median(ordered) if ordered else None),
        "latency_mean_ms": _ms(mean(ordered) if ordered else None),
        "latency_p95_ms": _ms(_percentile(ordered, 0.95) if len(ordered) >= MIN_SAMPLES_P95 else None),
        "latency_p99_ms": _ms(_percentile(ordered, 0.99) if len(ordered) >= MIN_SAMPLES_P99 else None),
        "latency_max_ms": _ms(ordered[-1] if ordered else None),
        "jitter_mean_ms": _ms(mean(jitter) if jitter else None),
    }


def _addressed_to(datagram: Datagram, receiver_ip: str, broadcast_ip: str | None) -> bool:
    return datagram.dst == receiver_ip or _is_group_address(datagram.dst, broadcast_ip)


def analyze(
    participants: dict[str, str],
    contents: dict[str, CaptureContents],
    broadcast_ip: str | None = None,
    window: float = MATCH_WINDOW_SECONDS,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Match every direction between participants.

    participants: node id -> IPv4 address; contents: node id -> its capture.
    Returns the summary and, per direction ("src->dst"), the per-packet rows.
    """
    directions: dict[str, Any] = {}
    rows_by_direction: dict[str, list[dict[str, Any]]] = {}
    for src_name, src_ip in participants.items():
        outgoing = [d for d in contents[src_name].datagrams if d.src == src_ip]
        for dst_name, dst_ip in participants.items():
            if dst_name == src_name:
                continue
            sent = [d for d in outgoing if _addressed_to(d, dst_ip, broadcast_ip)]
            if not sent:
                continue
            received = [
                d for d in contents[dst_name].datagrams
                if d.src == src_ip and _addressed_to(d, dst_ip, broadcast_ip)
            ]
            rows = match_direction(sent, received, window)
            name = f"{src_name}->{dst_name}"
            rows_by_direction[name] = rows
            flows = {}
            for flow in sorted({row["flow"] for row in rows}):
                flows[flow] = summarize([row for row in rows if row["flow"] == flow])
            directions[name] = {"source": src_name, "destination": dst_name, "total": summarize(rows), "flows": flows}

    participant_ips = set(participants.values())
    ignored = {}
    for name, capture in contents.items():
        outside = sum(
            1 for d in capture.datagrams
            if d.src not in participant_ips
            or not (d.dst in participant_ips or _is_group_address(d.dst, broadcast_ip))
        )
        ignored[name] = {"non_ipv4": capture.non_ipv4, "malformed": capture.malformed, "ipv4_not_between_participants": outside}
    summary = {"window_seconds": window, "directions": directions, "ignored": ignored}
    return summary, rows_by_direction


def _csv_cell(value: Any, field: str) -> str:
    if value is None:
        return ""
    if field in ("sent_at", "received_at"):
        return f"{value:.6f}"
    if field == "latency_ms":
        return f"{value:.3f}"
    return str(value)


def write_rows(path: str | Path, rows: list[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row[field], field) for field in CSV_FIELDS})


def analyze_run(config: dict[str, Any]) -> dict[str, Any]:
    """Analyse the PCAPs of a finished run and write one CSV per direction.

    Never raises: a capture that cannot be read is reported in "error" and
    the run status is not changed by this analysis.
    """
    output = config["output"]
    participants = {name: config["nodes"][name]["address"] for name in output["pcaps"]}
    try:
        contents = {name: read_pcap(path) for name, path in output["pcaps"].items()}
    except (OSError, PcapError) as exc:
        return {"valid": False, "error": f"could not read PCAP: {exc}", "warnings": [], "files": {}}
    summary, rows_by_direction = analyze(participants, contents, config["wireless"].get("broadcast_ip"))
    files = {}
    run_dir = Path(output["directory"])
    for direction, rows in rows_by_direction.items():
        src, dst = direction.split("->")
        path = run_dir / CSV_NAME.format(src=src, dst=dst)
        write_rows(path, rows)
        files[direction] = str(path)

    warnings = []
    drain = config["experiment"].get("drain_seconds")
    for direction, result in summary["directions"].items():
        largest = result["total"]["latency_max_ms"]
        if drain and largest is not None and largest / 1000 >= DRAIN_WARNING_FRACTION * drain:
            warnings.append(
                f"{direction}: largest latency ({largest:.1f} ms) is close to drain_seconds ({drain} s); "
                "packets sent at the end may be counted as lost because receivers were stopped"
            )
    return {"valid": True, "error": None, **summary, "warnings": warnings, "files": files}
