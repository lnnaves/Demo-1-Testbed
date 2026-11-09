from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .network import container_popen


@dataclass
class ProcessRecord:
    role: str
    node: str
    command: list[str]
    process: Any
    started_at: float
    ended_at: float | None = None
    # Wall-clock instants (Unix epoch seconds, same base as PCAP timestamps).
    started_wall: float = field(default_factory=time.time)
    ended_wall: float | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    # "self": the process exited on its own; "testbed": the testbed stopped it
    # (SIGTERM/kill). Being stopped by the testbed is normal (CLAUDE.md, D15).
    ended_by: str | None = None

    def finish(self, timeout: float | None = None) -> None:
        stdout, stderr = self.process.communicate(timeout=timeout)
        self.ended_at = time.monotonic()
        self.ended_wall = time.time()
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
    return container_popen(node, command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def execute_protocol(
    config: dict[str, Any],
    nodes: dict[str, Any],
    on_sender_start: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Run receivers, then the sender, then stop the receivers.

    experiment.duration_seconds counts from the sender start. With
    experiment.end == "duration" the testbed stops the sender at that limit
    (normal end); with "sender" (default) reaching the limit is a failure.
    on_sender_start(wall_time) is called right after the sender is spawned.
    """
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
            try:
                process = _spawn(receiver_node, command)
            except OSError as exc:
                # Abort starting further receivers; already-spawned ones are
                # cleaned up in the `finally` block below.
                failures.append(f"failed to start receiver {receiver_name}: {exc}")
                break
            receiver_records.append(ProcessRecord("receiver", receiver_name, command, process, time.monotonic()))

        if not failures:
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
                "--destination",
                destination,
                "--port",
                str(protocol["port"]),
            ]
            try:
                sender_process = _spawn(sender_node, command)
            except OSError as exc:
                failures.append(f"failed to start sender {sender_name}: {exc}")
            else:
                sender_record = ProcessRecord("sender", sender_name, command, sender_process, time.monotonic())
                if on_sender_start is not None:
                    on_sender_start(sender_record.started_wall)

                remaining = max(0.1, experiment["duration_seconds"] - (time.monotonic() - sender_record.started_at))
                timed_out = False
                try:
                    sender_record.finish(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    sender_record.process.terminate()
                    try:
                        sender_record.finish(timeout=experiment["shutdown_timeout_seconds"])
                    except subprocess.TimeoutExpired:
                        sender_record.process.kill()
                        sender_record.finish(timeout=None)
                    sender_record.ended_by = "testbed"
                    if experiment.get("end", "sender") != "duration":
                        failures.append("sender timed out")
                else:
                    sender_record.ended_by = "self"

                if not timed_out and sender_record.exit_code != 0:
                    failures.append(f"sender exited with code {sender_record.exit_code}")

                # Let the last messages in flight reach the receivers before
                # they are stopped.
                if experiment["drain_seconds"] > 0:
                    time.sleep(experiment["drain_seconds"])
    finally:
        failures.extend(stop_receivers(receiver_records, experiment["shutdown_timeout_seconds"]))

    return _build_result(start, sender_record, receiver_records, failures)


def stop_receivers(records: list[ProcessRecord], timeout: float) -> list[str]:
    """Terminate receivers, reporting only spontaneous (unrequested) exits.

    Three cases per receiver:
    - already flagged with an exit_code (e.g. a startup failure): skipped by
      the failure/termination check, since it was already accounted for; its
      stdout/stderr/duration are still collected in the final pass below.
    - found already dead, but not previously flagged: it exited on its own
      and is reported as a failure.
    - still running: stopped with terminate()/kill(); this controlled
      shutdown is normal lifecycle and never produces a failure by itself.
    """
    failures: list[str] = []
    for record in records:
        if record.exit_code is not None:
            continue
        exit_code = record.process.poll()
        if exit_code is not None:
            record.ended_by = "self"
            failures.append(f"receiver {record.node} exited unexpectedly with code {exit_code}")
        else:
            record.ended_by = "testbed"
            record.process.terminate()

    for record in records:
        if record.ended_at is not None:
            continue
        try:
            record.finish(timeout=timeout)
        except subprocess.TimeoutExpired:
            record.process.kill()
            record.finish(timeout=None)

    return failures


def _record_summary(record: ProcessRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "node": record.node,
        "command": record.command,
        "exit_code": record.exit_code,
        "ended_by": record.ended_by,
        "stdout": record.stdout,
        "stderr": record.stderr,
        "duration_seconds": None if record.ended_at is None else record.ended_at - record.started_at,
        "started_at": record.started_wall,
        "ended_at": record.ended_wall,
    }


def _build_result(
    start: float,
    sender: ProcessRecord | None,
    receivers: list[ProcessRecord],
    failures: list[str],
) -> dict[str, Any]:
    # "status" reflects only the process lifecycle (spawn/startup/exit/timeout
    # outcomes). It does not confirm that any packet was actually delivered;
    # delivery evidence belongs to metrics/summary, not to the runner.
    return {
        "status": "success" if not failures else "failed",
        "duration_seconds": time.monotonic() - start,
        "sender": _record_summary(sender),
        "receivers": [_record_summary(receiver) for receiver in receivers],
        "failures": failures,
    }
