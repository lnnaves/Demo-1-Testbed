from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone().isoformat(timespec="milliseconds")


class Timeline:
    """Start, end and duration of the experiment and of each of its phases.

    Durations come from the monotonic clock (immune to clock adjustments);
    start/end instants come from the wall clock, as Unix epoch seconds, the
    same time base as PCAP timestamps, plus a readable local ISO string.
    """

    def __init__(self, monotonic: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self._monotonic = monotonic
        self._wall = wall
        self._started_mono = monotonic()
        self._started_wall = wall()
        self._ended_mono: float | None = None
        self._ended_wall: float | None = None
        self._current: tuple[str, float, float] | None = None
        self.phases: list[dict[str, Any]] = []

    def start(self, name: str) -> str:
        """Close the running phase as "ok" and start a new one; returns its name."""
        self._close("ok")
        self._current = (name, self._monotonic(), self._wall())
        return name

    def fail(self) -> None:
        """Close the running phase as "failed"."""
        self._close("failed")

    def finish(self) -> None:
        """Close the running phase as "ok" and stop the experiment clock."""
        self._close("ok")
        self._ended_mono = self._monotonic()
        self._ended_wall = self._wall()

    def _close(self, status: str) -> None:
        if self._current is None:
            return
        name, started_mono, started_wall = self._current
        self._current = None
        ended_wall = self._wall()
        self.phases.append({
            "name": name,
            "status": status,
            "started_at": started_wall,
            "ended_at": ended_wall,
            "started_at_local": _iso(started_wall),
            "ended_at_local": _iso(ended_wall),
            "duration_seconds": self._monotonic() - started_mono,
        })

    def to_dict(self) -> dict[str, Any]:
        ended_mono = self._ended_mono if self._ended_mono is not None else self._monotonic()
        ended_wall = self._ended_wall if self._ended_wall is not None else self._wall()
        return {
            "clock": "started_at/ended_at are Unix epoch seconds (same base as PCAP timestamps); "
                     "durations use a monotonic clock",
            "started_at": self._started_wall,
            "ended_at": ended_wall,
            "started_at_local": _iso(self._started_wall),
            "ended_at_local": _iso(ended_wall),
            "duration_seconds": ended_mono - self._started_mono,
            "phases": list(self.phases),
        }
