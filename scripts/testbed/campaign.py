"""Repetitions of the same config and their statistics (D20).

The wmediumd radio draws fading without a seed, so the same config does not
give the same numbers twice. A campaign runs the config N times, each in an
ordinary run directory (rep-01, rep-02...), and summarizes every measure with
mean, sample standard deviation and a 95% confidence interval (Student's t).

A repetition whose status is "failed" (or that did not finish) is kept on
disk and listed, but left out of the statistics.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev
from typing import Any

CAMPAIGN_SUFFIX = "-campanha"
REPETITION_NAME = "rep-{index:02d}"
STATISTICS_JSON = "statistics.json"
STATISTICS_CSV = "statistics.csv"
_TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"

# Measures summarized per direction, read from summary.json.
DIRECTION_METRICS = [
    "delivery_rate",
    "latency_min_ms",
    "latency_median_ms",
    "latency_mean_ms",
    "latency_p95_ms",
    "latency_max_ms",
    "jitter_mean_ms",
    "max_loss_burst",
    "first_loss_t_s",
    "first_loss_distance_m",
]
GLOBAL_METRICS = ["mesh_ready_seconds"]
CSV_FIELDS = ["scope", "metric", "n", "mean", "stdev", "ci95_low", "ci95_high", "min", "max"]

# Two-sided 95% critical values of Student's t, by degrees of freedom.
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
    10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
    18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980,
}


def t_critical(degrees_of_freedom: int) -> float:
    """t for a two-sided 95% interval; between table rows, the lower (more conservative) row."""
    if degrees_of_freedom < 1:
        raise ValueError("at least one degree of freedom is needed")
    if degrees_of_freedom in _T95:
        return _T95[degrees_of_freedom]
    lower = [df for df in _T95 if df < degrees_of_freedom]
    return _T95[max(lower)] if degrees_of_freedom <= 120 else 1.960


def describe(values: list[float]) -> dict[str, Any]:
    """n, mean, sample standard deviation and 95% CI of the mean (no CI with n < 2)."""
    n = len(values)
    result: dict[str, Any] = {
        "n": n, "mean": None, "stdev": None, "ci95_low": None, "ci95_high": None,
        "min": min(values) if values else None, "max": max(values) if values else None, "values": values,
    }
    if n == 0:
        return result
    result["mean"] = mean(values)
    if n >= 2:
        deviation = stdev(values)
        half = t_critical(n - 1) * deviation / math.sqrt(n)
        result.update(stdev=deviation, ci95_low=result["mean"] - half, ci95_high=result["mean"] + half)
    return result


def campaign_directory(experiment_id: str, base_dir: str | Path, now: datetime | None = None) -> Path:
    """logs/<experiment>/<date-time>-campanha/, never shared with another campaign."""
    stamp = (now or datetime.now()).strftime(_TIMESTAMP_FORMAT)
    parent = Path(base_dir).resolve() / experiment_id
    directory = parent / f"{stamp}{CAMPAIGN_SUFFIX}"
    suffix = 2
    while True:
        try:
            directory.mkdir(parents=True, exist_ok=False)
            return directory
        except FileExistsError:
            directory = parent / f"{stamp}{CAMPAIGN_SUFFIX}-{suffix}"
            suffix += 1


def repetition_seeds(base_seeds: dict[str, int], index: int, vary_seed: bool) -> dict[str, int]:
    """Random Waypoint seeds for repetition `index` (1-based): the same, or seed + (index - 1)."""
    return {node: seed + (index - 1) if vary_seed else seed for node, seed in base_seeds.items()}


def repetition_measures(summary: dict[str, Any] | None, mesh_ready_seconds: float | None) -> dict[str, Any]:
    """The measures of one repetition, from its summary.json content."""
    measures: dict[str, Any] = {"global": {"mesh_ready_seconds": mesh_ready_seconds}, "directions": {}}
    latency = (summary or {}).get("latency") or {}
    first_losses = (latency.get("windows") or {}).get("first_loss") or {}
    for direction, result in (latency.get("directions") or {}).items():
        total = result.get("total") or {}
        loss = first_losses.get(direction)
        sent = total.get("sent") or 0
        measures["directions"][direction] = {
            "delivery_rate": (total.get("delivered", 0) / sent) if sent else None,
            **{metric: total.get(metric) for metric in DIRECTION_METRICS if metric.startswith(("latency_", "jitter_", "max_"))},
            "first_loss_t_s": loss.get("t") if loss else None,
            "first_loss_distance_m": loss.get("distance_m") if loss else None,
        }
    return measures


def summarize(repetitions: list[dict[str, Any]]) -> dict[str, Any]:
    """Statistics over the repetitions marked "used"."""
    used = [rep for rep in repetitions if rep["used"]]
    statistics: dict[str, Any] = {"global": {}, "directions": {}}
    for metric in GLOBAL_METRICS:
        values = [rep["measures"]["global"].get(metric) for rep in used]
        statistics["global"][metric] = describe([v for v in values if v is not None])
    directions = sorted({d for rep in used for d in rep["measures"]["directions"]})
    for direction in directions:
        statistics["directions"][direction] = {}
        for metric in DIRECTION_METRICS:
            values = [(rep["measures"]["directions"].get(direction) or {}).get(metric) for rep in used]
            statistics["directions"][direction][metric] = describe([v for v in values if v is not None])
    return statistics


def write_statistics(directory: str | Path, campaign: dict[str, Any], repetitions: list[dict[str, Any]],
                     statistics: dict[str, Any]) -> dict[str, str]:
    directory = Path(directory)
    json_path, csv_path = directory / STATISTICS_JSON, directory / STATISTICS_CSV
    document = {"campaign": campaign, "repetitions": repetitions, "statistics": statistics}
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_FIELDS)
        scopes = [("global", statistics["global"])] + list(statistics["directions"].items())
        for scope, metrics in scopes:
            for metric, stats in metrics.items():
                writer.writerow([scope, metric] + ["" if stats[f] is None else stats[f] for f in CSV_FIELDS[2:]])
    return {"json": str(json_path), "csv": str(csv_path)}


def _number(value: float | None, metric: str) -> str:
    if value is None:
        return "-"
    if metric == "delivery_rate":
        return f"{value * 100:.1f}%"
    return f"{value:.3f}" if abs(value) < 100 else f"{value:.1f}"


def format_table(statistics: dict[str, Any]) -> list[str]:
    """Lines "metric: mean ± half-width (IC95 low..high, n=...)" for the terminal."""
    lines = []
    scopes = [("global", statistics["global"])] + list(statistics["directions"].items())
    for scope, metrics in scopes:
        lines.append(f"{scope}:")
        for metric, stats in metrics.items():
            if stats["n"] == 0:
                lines.append(f"  {metric:<22} - (no value)")
                continue
            if stats["ci95_low"] is None:
                lines.append(f"  {metric:<22} {_number(stats['mean'], metric)} (n={stats['n']}, no CI)")
                continue
            half = (stats["ci95_high"] - stats["ci95_low"]) / 2
            lines.append(
                f"  {metric:<22} {_number(stats['mean'], metric)} ± {_number(half, metric)} "
                f"(IC95 {_number(stats['ci95_low'], metric)}..{_number(stats['ci95_high'], metric)}, n={stats['n']})"
            )
    return lines
