from __future__ import annotations

import csv
import json
import signal
import subprocess
import time
from pathlib import Path
from statistics import mean, median
from typing import Any


class Capture:
    def __init__(self, node: Any, interface: str, pcap_path: str):
        self.node = node
        self.interface = interface
        self.pcap_path = str(pcap_path)
        self.process: Any | None = None
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.stderr = ""

    def start(self) -> "Capture":
        Path(self.pcap_path).parent.mkdir(parents=True, exist_ok=True)
        pcap_file = open(self.pcap_path, "wb")
        try:
            self.process = self.node.popen(
                ["tcpdump", "-i", self.interface, "-w", "-"],
                stdout=pcap_file,
                stderr=subprocess.PIPE,
            )
        except Exception:
            pcap_file.close()
            raise
        self._pcap_file = pcap_file
        self.started_at = time.monotonic()
        return self

    def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        if self.process is None:
            return {"pcap": self.pcap_path, "started": False, "exit_code": None, "stderr": ""}
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        try:
            _, stderr = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                _, stderr = self.process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                _, stderr = self.process.communicate()
        self._pcap_file.close()
        self.ended_at = time.monotonic()
        self.stderr = _decode(stderr)
        return {
            "pcap": self.pcap_path,
            "started": True,
            "interface": self.interface,
            "exit_code": self.process.poll(),
            "stderr": self.stderr,
            "duration_seconds": self.ended_at - (self.started_at or self.ended_at),
            "packet_count": count_packets(self.pcap_path),
        }


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def start_capture(config: dict[str, Any], nodes: dict[str, Any]) -> Capture:
    sender = config["protocol"]["sender"]
    interface = config["wireless"].get("interface", "bat0")
    return Capture(nodes[sender], interface, config["output"]["pcap"]).start()


def count_packets(pcap_path: str) -> int:
    path = Path(pcap_path)
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        result = subprocess.run(
            ["tcpdump", "-nn", "-r", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


def write_csv(path: str, duration_seconds: float, packet_count: int, latency_samples: list[float] | None = None) -> None:
    samples = latency_samples or []
    row = {
        "duration_seconds": f"{duration_seconds:.6f}",
        "packet_count": str(packet_count),
        "latency_mean_seconds": "" if not samples else f"{mean(samples):.6f}",
        "latency_median_seconds": "" if not samples else f"{median(samples):.6f}",
        "latency_max_seconds": "" if not samples else f"{max(samples):.6f}",
    }
    csv_path = Path(path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


def write_summary(path: str, summary: dict[str, Any]) -> None:
    summary_path = Path(path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as summary_file:
        json.dump(summary, summary_file, indent=2, ensure_ascii=False)
        summary_file.write("\n")


def write_outputs(config: dict[str, Any], protocol_result: dict[str, Any], capture_result: dict[str, Any]) -> dict[str, Any]:
    duration = protocol_result.get("duration_seconds", 0.0)
    packet_count = capture_result.get("packet_count", 0)
    write_csv(config["output"]["csv"], duration, packet_count)
    summary = {
        "experiment": config["experiment"]["id"],
        "status": "success" if protocol_result.get("status") == "success" else "failed",
        "sender": protocol_result.get("sender"),
        "receivers": protocol_result.get("receivers", []),
        "capture": capture_result,
        "failures": protocol_result.get("failures", []),
        "outputs": config["output"],
    }
    write_summary(config["output"]["summary"], summary)
    return summary
