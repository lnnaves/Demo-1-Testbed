"""Periodic observation of BATMAN routes and radio links (D15).

Every SAMPLE_SECONDS, on every node, the testbed reads two local tables:
- `batctl o`: BATMAN originator table (destination, next hop, link quality
  TQ, last-seen) -> routes.csv;
- `iw dev <wlan> station dump`: per neighbour signal (dBm), transmit bitrate,
  retries and failures -> signal.csv.

Both commands only read kernel state: no packet is sent, so the protocol's
traffic is not affected. The "t" column counts seconds since the sender
started (empty before it starts).
"""

from __future__ import annotations

import csv
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

SAMPLE_SECONDS = 1.0
ROUTES_FIELDS = ["time", "t", "node", "destination", "best", "next_hop", "tq", "last_seen_s", "interface"]
SIGNAL_FIELDS = [
    "time", "t", "node", "neighbor", "signal_dbm", "tx_bitrate_mbps",
    "tx_retries", "tx_failed", "inactive_ms",
]

_MAC = r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}"
_ROUTE_LINE = re.compile(
    rf"^\s*(\*)?\s*({_MAC})\s+([\d.]+)s\s+\(\s*(\d+)\)\s+({_MAC})\s+\[\s*([^\]]*?)\s*\]", re.I
)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def parse_originators(text: str, names: dict[str, str]) -> list[dict[str, Any]]:
    """Rows of `batctl o`; MACs are shown as node ids when known."""
    rows = []
    for line in _ANSI.sub("", text or "").splitlines():
        match = _ROUTE_LINE.match(line)
        if not match:
            continue
        best, originator, last_seen, tq, next_hop, interface = match.groups()
        rows.append({
            "destination": names.get(originator.lower(), originator.lower()),
            "best": 1 if best else 0,
            "next_hop": names.get(next_hop.lower(), next_hop.lower()),
            "tq": int(tq),
            "last_seen_s": float(last_seen),
            "interface": interface,
        })
    return rows


def _number(pattern: str, block: str, cast: Callable[[str], Any]) -> Any:
    match = re.search(pattern, block)
    return cast(match.group(1)) if match else None


def parse_station_dump(text: str, names: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for block in re.split(r"(?=^Station )", _ANSI.sub("", text or ""), flags=re.M):
        match = re.match(rf"Station ({_MAC})", block, re.I)
        if not match:
            continue
        rows.append({
            "neighbor": names.get(match.group(1).lower(), match.group(1).lower()),
            "signal_dbm": _number(r"\n\s*signal:\s*(-?\d+)", block, int),
            "tx_bitrate_mbps": _number(r"tx bitrate:\s*([\d.]+)", block, float),
            "tx_retries": _number(r"tx retries:\s*(\d+)", block, int),
            "tx_failed": _number(r"tx failed:\s*(\d+)", block, int),
            "inactive_ms": _number(r"inactive time:\s*(\d+)", block, int),
        })
    return rows


class NetworkObserver:
    def __init__(
        self,
        config: dict[str, Any],
        nodes: dict[str, Any],
        mac_names: dict[str, str],
        routes_path: str,
        signal_path: str,
        locks: dict[str, Any],
        clock: Callable[[], float | None],
    ):
        self.config = config
        self.nodes = nodes
        self.names = mac_names
        self.routes_path = routes_path
        self.signal_path = signal_path
        self.locks = locks
        self.clock = clock  # seconds since the sender started, or None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.samples = 0
        self.errors: list[str] = []

    def start(self) -> "NetworkObserver":
        Path(self.routes_path).parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="observer", daemon=True)
        self._thread.start()
        return self

    def _sample(self, routes: Any, signal: Any) -> None:
        for name, node_cfg in self.config["nodes"].items():
            node = self.nodes[name]
            wlan = f"{node_cfg['container_name']}-wlan0"
            try:
                with self.locks[name]:
                    originators = node.cmd("batctl o 2>&1")
                    stations = node.cmd(f"iw dev {wlan} station dump 2>&1")
            except Exception as exc:
                if len(self.errors) < 10:
                    self.errors.append(f"{name}: {exc}")
                continue
            now = time.time()
            t = self.clock()
            stamp = [f"{now:.6f}", "" if t is None else f"{t:.3f}", name]
            for row in parse_originators(originators, self.names):
                routes.writerow(stamp + [row[field] for field in ROUTES_FIELDS[3:]])
            for row in parse_station_dump(stations, self.names):
                signal.writerow(stamp + ["" if row[field] is None else row[field] for field in SIGNAL_FIELDS[3:]])

    def _run(self) -> None:
        with open(self.routes_path, "w", encoding="utf-8", newline="") as routes_file, \
                open(self.signal_path, "w", encoding="utf-8", newline="") as signal_file:
            routes = csv.writer(routes_file)
            signal = csv.writer(signal_file)
            routes.writerow(ROUTES_FIELDS)
            signal.writerow(SIGNAL_FIELDS)
            next_sample = time.monotonic()
            while True:
                self._sample(routes, signal)
                self.samples += 1
                routes_file.flush()
                signal_file.flush()
                next_sample += SAMPLE_SECONDS
                if self._stop.wait(max(0.0, next_sample - time.monotonic())):
                    self._sample(routes, signal)  # last picture, at the end
                    self.samples += 1
                    return

    def stop(self, timeout: float = 10.0) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return {
            "routes": self.routes_path,
            "signal": self.signal_path,
            "sample_seconds": SAMPLE_SECONDS,
            "samples": self.samples,
            "errors": self.errors,
        }
