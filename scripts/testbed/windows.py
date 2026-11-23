"""Latency and loss per time window and per distance band (D16).

Joins, after the run, files the testbed already wrote:
- latency-<src>-<dst>.csv: one row per sent packet (D13);
- positions.csv: every node every 0.1 s (D15);
- routes.csv and signal.csv: BATMAN routes and radio links every 1 s (D15).

Two files per direction:
- windows-<src>-<dst>.csv: one row per time window (default 1 s), from the
  sender start (t=0, the same t=0 as mobility) to the sender end. Windows
  without packets are kept with sent=0 and empty latency: "nothing sent" is
  not "everything lost".
- distance-<src>-<dst>.csv: one row per distance band (default 10 m) that
  has packets.

Each packet belongs to the window/band of its *send* instant (a lost packet
has no arrival). Distance is the straight line between source and
destination at that instant (both may move); with relays it is not the path
length, the route columns say which way the packet went. Route and signal
are observations (read every 1 s), not causes: a change between two
readings shows up in the window of the next reading.

Never reads payloads. Can also be run on an existing run directory:
    cd scripts && python3 -m testbed.windows <run_dir> [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

from .latency import summarize

DEFAULT_WINDOW_SECONDS = 1.0
DEFAULT_DISTANCE_BIN_METERS = 10.0

WINDOWS_NAME = "windows-{src}-{dst}.csv"
DISTANCE_NAME = "distance-{src}-{dst}.csv"

# p95/p99 are left out: a window has too few packets for them (D16).
METRIC_FIELDS = [
    "sent", "delivered", "lost", "loss_rate", "max_loss_burst", "out_of_order", "duplicates",
    "latency_min_ms", "latency_median_ms", "latency_mean_ms", "latency_max_ms", "jitter_mean_ms",
]
WINDOWS_FIELDS = (
    ["t_start", "t_end", "distance_m"] + METRIC_FIELDS
    + ["next_hop", "tq", "route_read_t", "signal_dbm", "tx_bitrate_mbps"]
)
DISTANCE_FIELDS = ["distance_from_m", "distance_to_m", "t_first", "t_last"] + METRIC_FIELDS + ["next_hops"]


# --------------------------------------------------------------------------
# Reading the run files


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def read_latency_rows(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append({
                "flow": row["flow"],
                "sent_at": float(row["sent_at"]),
                "received_at": _float(row["received_at"]),
                "latency_ms": _float(row["latency_ms"]),
                "status": row["status"],
                "duplicates": int(row["duplicates"] or 0),
            })
    rows.sort(key=lambda row: row["sent_at"])
    return rows


class Positions:
    """Position of each node over time, linearly interpolated between samples."""

    def __init__(self, samples: dict[str, list[tuple[float, float, float, float]]]):
        self.samples = {name: sorted(points) for name, points in samples.items()}
        self.times = {name: [point[0] for point in points] for name, points in self.samples.items()}

    @classmethod
    def read(cls, path: str | Path) -> "Positions":
        samples: dict[str, list[tuple[float, float, float, float]]] = {}
        with Path(path).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                samples.setdefault(row["node"], []).append(
                    (float(row["time"]), float(row["x"]), float(row["y"]), float(row["z"]))
                )
        return cls(samples)

    def at(self, node: str, when: float) -> tuple[float, float, float] | None:
        points = self.samples.get(node)
        if not points:
            return None
        index = bisect.bisect_right(self.times[node], when)
        if index == 0:
            return points[0][1:]
        if index == len(points):
            return points[-1][1:]
        (t0, *p0), (t1, *p1) = points[index - 1], points[index]
        fraction = 0.0 if t1 == t0 else (when - t0) / (t1 - t0)
        return tuple(a + (b - a) * fraction for a, b in zip(p0, p1))

    def distance(self, src: str, dst: str, when: float) -> float | None:
        a, b = self.at(src, when), self.at(dst, when)
        if a is None or b is None:
            return None
        return math.dist(a, b)


def read_routes(path: str | Path, src: str, dst: str) -> list[dict[str, Any]]:
    """Best route of `src` towards `dst`, one entry per reading, in time order."""
    readings = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["node"] == src and row["destination"] == dst and row["best"] == "1":
                readings.append({"time": float(row["time"]), "next_hop": row["next_hop"], "tq": int(row["tq"])})
    readings.sort(key=lambda reading: reading["time"])
    return readings


def read_signal(path: str | Path, src: str) -> list[dict[str, Any]]:
    readings = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["node"] == src:
                readings.append({
                    "time": float(row["time"]),
                    "neighbor": row["neighbor"],
                    "signal_dbm": _float(row["signal_dbm"]),
                    "tx_bitrate_mbps": _float(row["tx_bitrate_mbps"]),
                })
    readings.sort(key=lambda reading: reading["time"])
    return readings


# --------------------------------------------------------------------------
# Building the tables


def _window_bounds(t0: float, t_end: float, window: float, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """First and last window index: from t=0 to the sender end, widened to any packet outside."""
    first, last = 0, max(0, math.ceil((t_end - t0) / window) - 1)
    if rows:
        first = min(first, math.floor((rows[0]["sent_at"] - t0) / window))
        last = max(last, math.floor((rows[-1]["sent_at"] - t0) / window))
    return first, last


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = summarize(rows)
    return {field: total[field] for field in METRIC_FIELDS}


def _in_window(readings: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    return [reading for reading in readings if start <= reading["time"] < end]


def _joined(values: list[str]) -> str:
    """Distinct values in order of appearance, e.g. "gcs|drone2"."""
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return "|".join(seen)


def time_windows(
    rows: list[dict[str, Any]],
    src: str,
    dst: str,
    t0: float,
    t_end: float,
    window: float,
    positions: Positions | None,
    routes: list[dict[str, Any]],
    signal: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    first, last = _window_bounds(t0, t_end, window, rows)
    buckets: dict[int, list[dict[str, Any]]] = {index: [] for index in range(first, last + 1)}
    for row in rows:
        buckets[math.floor((row["sent_at"] - t0) / window)].append(row)
    table = []
    for index in range(first, last + 1):
        start, end = t0 + index * window, t0 + (index + 1) * window
        route_readings = _in_window(routes, start, end)
        last_route = route_readings[-1] if route_readings else None
        signal_value = bitrate = None
        if last_route is not None:
            towards_hop = [r for r in _in_window(signal, start, end) if r["neighbor"] == last_route["next_hop"]]
            if towards_hop:
                signal_value, bitrate = towards_hop[-1]["signal_dbm"], towards_hop[-1]["tx_bitrate_mbps"]
        table.append({
            "t_start": index * window,
            "t_end": (index + 1) * window,
            "distance_m": positions.distance(src, dst, (start + end) / 2) if positions else None,
            **_metrics(buckets[index]),
            "next_hop": _joined([r["next_hop"] for r in route_readings]) or None,
            "tq": last_route["tq"] if last_route else None,
            "route_read_t": last_route["time"] - t0 if last_route else None,
            "signal_dbm": signal_value,
            "tx_bitrate_mbps": bitrate,
        })
    return table


def _route_at(routes: list[dict[str, Any]], when: float) -> str | None:
    """Next hop of the last reading at or before `when`."""
    times = [reading["time"] for reading in routes]
    index = bisect.bisect_right(times, when)
    return routes[index - 1]["next_hop"] if index else None


def distance_bands(
    rows: list[dict[str, Any]],
    src: str,
    dst: str,
    t0: float,
    band: float,
    positions: Positions | None,
    routes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if positions is None:
        return []
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        distance = positions.distance(src, dst, row["sent_at"])
        if distance is None:
            continue
        buckets.setdefault(math.floor(distance / band), []).append(row)
    table = []
    for index in sorted(buckets):
        members = buckets[index]
        table.append({
            "distance_from_m": index * band,
            "distance_to_m": (index + 1) * band,
            "t_first": members[0]["sent_at"] - t0,
            "t_last": members[-1]["sent_at"] - t0,
            **_metrics(members),
            "next_hops": _joined([hop for hop in (_route_at(routes, r["sent_at"]) for r in members) if hop]) or None,
        })
    return table


def first_loss(rows: list[dict[str, Any]], src: str, dst: str, t0: float, positions: Positions | None) -> dict[str, Any] | None:
    for row in rows:
        if row["status"] == "lost":
            distance = positions.distance(src, dst, row["sent_at"]) if positions else None
            return {"t": round(row["sent_at"] - t0, 3), "distance_m": None if distance is None else round(distance, 1)}
    return None


# --------------------------------------------------------------------------
# Writing


def _cell(value: Any, field: str) -> str:
    if value is None:
        return ""
    if field == "loss_rate":
        return f"{value:.4f}"
    if isinstance(value, float):
        if field.startswith(("t_", "route_read_t")):
            return f"{value:.3f}"
        if field.startswith("distance"):
            return f"{value:.1f}"
        return f"{value:.3f}" if field.endswith("_ms") else f"{value:g}"
    return str(value)


def write_table(path: str | Path, fields: list[str], table: list[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for row in table:
            writer.writerow([_cell(row[field], field) for field in fields])


def analyze_windows(
    run_dir: str | Path,
    latency_files: dict[str, str],
    t0: float,
    t_end: float,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    distance_bin_meters: float = DEFAULT_DISTANCE_BIN_METERS,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Write windows-*.csv and distance-*.csv for every direction.

    latency_files: "src->dst" -> path of its latency CSV. t0/t_end: sender
    start and end (epoch seconds). Never raises: problems go to "errors".
    """
    run_dir = Path(run_dir)
    output_dir = Path(output_dir) if output_dir is not None else run_dir
    errors: list[str] = []
    positions = routes_path = signal_path = None
    try:
        positions = Positions.read(run_dir / "positions.csv")
    except (OSError, KeyError, ValueError) as exc:
        errors.append(f"positions.csv not usable, distances left empty: {exc}")
    if (run_dir / "routes.csv").is_file():
        routes_path = run_dir / "routes.csv"
    if (run_dir / "signal.csv").is_file():
        signal_path = run_dir / "signal.csv"

    files: dict[str, dict[str, str]] = {}
    losses: dict[str, Any] = {}
    for direction, latency_path in sorted(latency_files.items()):
        src, dst = direction.split("->")
        try:
            rows = read_latency_rows(latency_path)
            routes = read_routes(routes_path, src, dst) if routes_path else []
            signal = read_signal(signal_path, src) if signal_path else []
            windows_path = output_dir / WINDOWS_NAME.format(src=src, dst=dst)
            distance_path = output_dir / DISTANCE_NAME.format(src=src, dst=dst)
            write_table(windows_path, WINDOWS_FIELDS,
                        time_windows(rows, src, dst, t0, t_end, window_seconds, positions, routes, signal))
            write_table(distance_path, DISTANCE_FIELDS,
                        distance_bands(rows, src, dst, t0, distance_bin_meters, positions, routes))
        except (OSError, KeyError, ValueError) as exc:
            errors.append(f"{direction}: {type(exc).__name__}: {exc}")
            continue
        files[direction] = {"windows": str(windows_path), "distance": str(distance_path)}
        losses[direction] = first_loss(rows, src, dst, t0, positions)
    return {
        "window_seconds": window_seconds,
        "distance_bin_meters": distance_bin_meters,
        "files": files,
        "first_loss": losses,
        "errors": errors,
    }


def describe_first_loss(direction: str, loss: dict[str, Any] | None) -> str:
    if loss is None:
        return f"{direction}: no loss"
    where = "" if loss["distance_m"] is None else f", at {loss['distance_m']:.0f} m"
    return f"{direction}: first loss at t={loss['t']:.1f}s{where}"


def main(argv: list[str] | None = None) -> int:
    """Recompute the tables of an existing run directory (e.g. older runs)."""
    parser = argparse.ArgumentParser(description="Latency/loss per time window and distance band of a finished run.")
    parser.add_argument("run_dir")
    parser.add_argument("--output-dir", default=None, help="Where to write the CSVs (default: the run directory).")
    parser.add_argument("--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS)
    parser.add_argument("--distance-bin-meters", type=float, default=DEFAULT_DISTANCE_BIN_METERS)
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    sender = summary["sender"]
    latency_files = {
        direction: str(run_dir / Path(path).name)
        for direction, path in (summary.get("latency") or {}).get("files", {}).items()
    }
    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    result = analyze_windows(
        run_dir, latency_files, sender["started_at"], sender["ended_at"],
        args.window_seconds, args.distance_bin_meters, args.output_dir,
    )
    for direction in sorted(result["files"]):
        print(describe_first_loss(direction, result["first_loss"][direction]))
    for error in result["errors"]:
        print(f"warning: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
