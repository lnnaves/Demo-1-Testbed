"""Node movement during the experiment (D15).

A thread of the testbed moves every node that has a trajectory, calling
setPosition() every TICK_SECONDS, so wmediumd recomputes the radio links.
Only the emulated world changes; the protocol is never touched.

t=0 is the instant the sender starts. Before that (mesh wait, receivers
starting) every node stays at its configured position. Every tick, the
position of every node is written to positions.csv.
"""

from __future__ import annotations

import csv
import threading
import time
from pathlib import Path
from typing import Any

TICK_SECONDS = 0.1
POSITIONS_FIELDS = ["time", "t", "node", "x", "y", "z"]


def position_at(waypoints: list[list[float]], t: float) -> list[float]:
    """Linear interpolation over [t, x, y, z] waypoints; parked after the last."""
    if t <= waypoints[0][0]:
        return list(waypoints[0][1:])
    for (t0, *p0), (t1, *p1) in zip(waypoints, waypoints[1:]):
        if t <= t1:
            fraction = (t - t0) / (t1 - t0)
            return [a + (b - a) * fraction for a, b in zip(p0, p1)]
    return list(waypoints[-1][1:])


def _fmt(value: float) -> str:
    return f"{value:.3f}"


class MobilityController:
    """Moves nodes along their trajectories and logs every node's position."""

    def __init__(self, config: dict[str, Any], nodes: dict[str, Any], positions_path: str, locks: dict[str, Any]):
        self.config = config
        self.nodes = nodes
        self.locks = locks
        self.positions_path = positions_path
        self.trajectories = {
            name: node_cfg["mobility"]["waypoints"]
            for name, node_cfg in config["nodes"].items()
            if node_cfg["mobility"]["type"] == "trajectory"
        }
        self.started = threading.Event()
        self.t0_wall: float | None = None
        self._t0_monotonic: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors: list[str] = []
        self.ticks = 0

    def start(self) -> "MobilityController":
        Path(self.positions_path).parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="mobility", daemon=True)
        self._thread.start()
        return self

    def sender_started(self, wall: float | None = None) -> None:
        """Called by the runner right after the sender process is spawned."""
        self._t0_monotonic = time.monotonic()
        self.t0_wall = wall if wall is not None else time.time()
        self.started.set()

    def t(self) -> float | None:
        return None if self._t0_monotonic is None else time.monotonic() - self._t0_monotonic

    def _write_positions(self, writer: Any, t: float, positions: dict[str, list[float]]) -> None:
        now = time.time()
        for name, (x, y, z) in positions.items():
            writer.writerow([f"{now:.6f}", _fmt(t), name, _fmt(x), _fmt(y), _fmt(z)])

    def _run(self) -> None:
        with open(self.positions_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(POSITIONS_FIELDS)
            # Nothing moves before the sender starts.
            while not self.started.wait(TICK_SECONDS):
                if self._stop.is_set():
                    return
            next_tick = self._t0_monotonic or time.monotonic()
            while True:
                t = time.monotonic() - (self._t0_monotonic or next_tick)
                positions = {name: list(cfg["position"]) for name, cfg in self.config["nodes"].items()}
                for name, waypoints in self.trajectories.items():
                    position = position_at(waypoints, t)
                    positions[name] = position
                    try:
                        with self.locks[name]:
                            self.nodes[name].setPosition(",".join(f"{value:.3f}" for value in position))
                    except Exception as exc:  # keep moving the others; report at the end
                        if len(self.errors) < 10:
                            self.errors.append(f"setPosition({name}) failed at t={t:.1f}s: {exc}")
                self._write_positions(writer, t, positions)
                self.ticks += 1
                if self._stop.is_set():
                    return
                next_tick += TICK_SECONDS
                self._stop.wait(max(0.0, next_tick - time.monotonic()))

    def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return {
            "positions": self.positions_path,
            "moving_nodes": sorted(self.trajectories),
            "tick_seconds": TICK_SECONDS,
            "t0": self.t0_wall,
            "ticks": self.ticks,
            "errors": self.errors,
        }
