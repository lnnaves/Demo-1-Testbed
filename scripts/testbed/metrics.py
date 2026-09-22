from __future__ import annotations

import csv
import json
import signal
import subprocess
import time
from pathlib import Path
from statistics import mean, median
from typing import Any


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def analyze_pcap(pcap_path: str, port: int) -> dict[str, Any]:
    """Read only the configured UDP traffic observed in a tcpdump capture."""
    path = Path(pcap_path)
    metrics = {
        "packet_count": 0,
        "first_timestamp_seconds": None,
        "last_timestamp_seconds": None,
        "observed_duration_seconds": None,
        "interval_mean_seconds": None,
        "interval_median_seconds": None,
        "interval_max_seconds": None,
    }
    if not path.exists():
        return {**metrics, "valid": False, "error": f"PCAP does not exist: {path}"}
    if path.stat().st_size == 0:
        return {**metrics, "valid": False, "error": f"PCAP is empty: {path}"}
    try:
        result = subprocess.run(
            ["tcpdump", "-tt", "-nn", "-r", str(path), "udp", "port", str(port)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return {**metrics, "valid": False, "error": "tcpdump is not available to read PCAP"}
    if result.returncode != 0:
        return {
            **metrics,
            "valid": False,
            "error": f"tcpdump could not read PCAP (exit {result.returncode}): {_decode(result.stderr).strip()}",
        }
    timestamps: list[float] = []
    try:
        for line in result.stdout.splitlines():
            if line.strip():
                timestamps.append(float(line.split(maxsplit=1)[0]))
    except (IndexError, ValueError):
        return {**metrics, "valid": False, "error": "tcpdump output did not contain numeric timestamps"}
    if not timestamps:
        return {**metrics, "valid": True, "error": None}
    intervals = [later - earlier for earlier, later in zip(timestamps, timestamps[1:])]
    metrics.update(
        packet_count=len(timestamps),
        first_timestamp_seconds=timestamps[0],
        last_timestamp_seconds=timestamps[-1],
        observed_duration_seconds=timestamps[-1] - timestamps[0],
    )
    if intervals:
        metrics.update(
            interval_mean_seconds=mean(intervals),
            interval_median_seconds=median(intervals),
            interval_max_seconds=max(intervals),
        )
    return {**metrics, "valid": True, "error": None}


class Capture:
    def __init__(self, node: Any, node_name: str, interface: str, port: int, pcap_path: str):
        self.node = node
        self.node_name = node_name
        self.interface = interface
        self.port = port
        self.pcap_path = str(pcap_path)
        self.process: Any | None = None
        self._pcap_file: Any | None = None
        self.started_at: float | None = None
        self._result: dict[str, Any] | None = None

    def start(self) -> "Capture":
        Path(self.pcap_path).parent.mkdir(parents=True, exist_ok=True)
        self._pcap_file = open(self.pcap_path, "wb")
        try:
            self.process = self.node.popen(
                ["tcpdump", "-i", self.interface, "-w", "-", "udp", "port", str(self.port)],
                stdout=self._pcap_file,
                stderr=subprocess.PIPE,
            )
        except Exception:
            self._pcap_file.close()
            self._pcap_file = None
            raise
        self.started_at = time.monotonic()
        return self

    def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        if self._result is not None:
            return self._result
        result = {
            "pcap": self.pcap_path,
            "node": self.node_name,
            "interface": self.interface,
            "port": self.port,
            "started": self.process is not None,
            "exit_code": None,
            "stderr": "",
            "duration_seconds": 0.0,
            "packet_count": 0,
            "valid": False,
        }
        if self.process is None:
            result["error"] = "capture was not started"
            self._result = result
            return result
        unexpected_exit = self.process.poll() is not None
        stderr: bytes | str | None = None
        try:
            if not unexpected_exit:
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
        finally:
            if self._pcap_file is not None:
                self._pcap_file.close()
                self._pcap_file = None
        result["exit_code"] = self.process.poll()
        result["stderr"] = _decode(stderr)
        result["duration_seconds"] = time.monotonic() - (self.started_at or time.monotonic())
        analysis = analyze_pcap(self.pcap_path, self.port)
        result["packet_count"] = analysis["packet_count"]
        result["metrics"] = analysis
        if unexpected_exit:
            result["error"] = f"tcpdump exited unexpectedly with code {result['exit_code']}"
        elif not analysis["valid"]:
            result["error"] = analysis["error"]
        else:
            result["valid"] = True
            result["error"] = None
        self._result = result
        return result


def start_capture(config: dict[str, Any], nodes: dict[str, Any]) -> Capture:
    sender = config["protocol"]["sender"]
    return Capture(
        nodes[sender],
        sender,
        config["wireless"]["interface"],
        config["protocol"]["port"],
        config["output"]["pcap"],
    ).start()


def _csv_value(value: float | int | None) -> str:
    return "" if value is None else f"{value:.6f}" if isinstance(value, float) else str(value)


def write_csv(path: str, metrics: dict[str, Any]) -> None:
    fields = [
        "packet_count", "first_timestamp_seconds", "last_timestamp_seconds",
        "observed_duration_seconds", "interval_mean_seconds",
        "interval_median_seconds", "interval_max_seconds",
    ]
    csv_path = Path(path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        writer.writerow({field: _csv_value(metrics.get(field)) for field in fields})


def write_summary(path: str, summary: dict[str, Any]) -> None:
    summary_path = Path(path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as summary_file:
        json.dump(summary, summary_file, indent=2, ensure_ascii=False)
        summary_file.write("\n")


def write_outputs(config: dict[str, Any], protocol_result: dict[str, Any], capture_result: dict[str, Any]) -> dict[str, Any]:
    metrics = capture_result.get("metrics", {})
    write_csv(config["output"]["csv"], metrics)
    process_status = protocol_result.get("status", "failed")
    capture_status = "valid" if capture_result.get("valid") else "failed"
    if capture_status == "valid":
        traffic_status = "observed" if metrics.get("packet_count", 0) else "not_observed"
    else:
        traffic_status = "unknown"
    failures = list(protocol_result.get("failures", []))
    warnings: list[str] = []
    if process_status != "success":
        status = "failed"
    elif capture_status != "valid":
        status = "failed"
        failures.append(capture_result.get("error") or "capture is invalid")
    elif traffic_status == "not_observed":
        status = "inconclusive"
        warnings.append("processes completed, but no configured UDP traffic was observed on the sender")
    else:
        status = "success"
    summary = {
        "experiment": config["experiment"]["id"],
        "mode": config["protocol"]["mode"],
        "status": status,
        "process_status": process_status,
        "capture_status": capture_status,
        "traffic_status": traffic_status,
        "sender": protocol_result.get("sender"),
        "receivers": protocol_result.get("receivers", []),
        "capture": capture_result,
        "metrics": metrics,
        "failures": failures,
        "warnings": warnings,
        "outputs": config["output"],
    }
    write_summary(config["output"]["summary"], summary)
    return summary
