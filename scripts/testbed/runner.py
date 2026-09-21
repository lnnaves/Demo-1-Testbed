from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class ProcessRecord:
    role: str
    node: str
    command: list[str]
    process: Any
    started_at: float
    ended_at: float | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""

    def finish(self, timeout: float | None = None) -> None:
        try:
            stdout, stderr = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise
        self.ended_at = time.monotonic()
        self.exit_code = self.process.poll()
        self.stdout = _decode(stdout)
        self.stderr = _decode(stderr)


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def select_destination(config: dict[str, Any]) -> str:
    protocol = config["protocol"]
    if protocol["mode"] == "unicast":
        if len(protocol["receivers"]) != 1:
            raise ValueError("unicast mode requires exactly one receiver")
        return config["nodes"][protocol["receivers"][0]]["address"]
    if protocol["mode"] == "broadcast":
        if not protocol["receivers"]:
            raise ValueError("broadcast mode requires at least one receiver")
        return config["wireless"]["broadcast_ip"]
    raise ValueError(f"unsupported protocol mode: {protocol['mode']}")


def _spawn(node: Any, command: list[str]) -> Any:
    return node.popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def execute_protocol(config: dict[str, Any], nodes: dict[str, Any]) -> dict[str, Any]:
    experiment = config["experiment"]
    protocol = config["protocol"]
    receiver_records: list[ProcessRecord] = []
    failures: list[str] = []
    start = time.monotonic()
    sender_record: ProcessRecord | None = None

    try:
        for receiver_name in protocol["receivers"]:
            receiver_node = nodes[receiver_name]
            receiver_cfg = config["nodes"][receiver_name]
            command = [
                config["binaries"]["receiver"]["container"],
                "--address",
                receiver_cfg["address"],
                "--port",
                str(protocol["port"]),
            ]
            receiver_records.append(
                ProcessRecord("receiver", receiver_name, command, _spawn(receiver_node, command), time.monotonic())
            )

        time.sleep(experiment["receiver_startup_seconds"])
        for record in receiver_records:
            exit_code = record.process.poll()
            if exit_code is not None:
                record.exit_code = exit_code
                failures.append(f"receiver {record.node} exited during startup with code {exit_code}")

        if not failures:
            destination = select_destination(config)
            sender_name = protocol["sender"]
            sender_node = nodes[sender_name]
            command = [
                config["binaries"]["sender"]["container"],
                "--mode",
                protocol["mode"],
                "--destination",
                destination,
                "--port",
                str(protocol["port"]),
                "--count",
                str(protocol["count"]),
            ]
            sender_record = ProcessRecord("sender", sender_name, command, _spawn(sender_node, command), time.monotonic())

            remaining = max(0.1, experiment["duration_seconds"] - (time.monotonic() - start))
            try:
                sender_record.finish(timeout=remaining)
            except subprocess.TimeoutExpired:
                sender_record.process.terminate()
                try:
                    sender_record.finish(timeout=config["experiment"]["shutdown_timeout_seconds"])
                except subprocess.TimeoutExpired:
                    sender_record.process.kill()
                    sender_record.finish(timeout=None)
                failures.append("sender timed out")

            if sender_record.exit_code != 0:
                failures.append(f"sender exited with code {sender_record.exit_code}")
    finally:
        stop_receivers(receiver_records, experiment["shutdown_timeout_seconds"])

    return _build_result(start, sender_record, receiver_records, failures)


def stop_receivers(records: list[ProcessRecord], timeout: float) -> None:
    for record in records:
        if record.process.poll() is None:
            record.process.terminate()
    for record in records:
        if record.ended_at is not None:
            continue
        try:
            record.finish(timeout=timeout)
        except subprocess.TimeoutExpired:
            record.process.kill()
            record.finish(timeout=None)


def _record_summary(record: ProcessRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "node": record.node,
        "command": record.command,
        "exit_code": record.exit_code,
        "stdout": record.stdout,
        "stderr": record.stderr,
        "duration_seconds": None if record.ended_at is None else record.ended_at - record.started_at,
    }


def _build_result(
    start: float,
    sender: ProcessRecord | None,
    receivers: list[ProcessRecord],
    failures: list[str],
) -> dict[str, Any]:
    return {
        "status": "success" if not failures else "failed",
        "duration_seconds": time.monotonic() - start,
        "sender": _record_summary(sender),
        "receivers": [_record_summary(receiver) for receiver in receivers],
        "failures": failures,
    }
