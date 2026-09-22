#!/usr/bin/env python3
"""Validate the single scenario configured in scripts/config.yml.

This helper never reads/writes more than one YAML file, never toggles
`protocol.mode` automatically, and never generates temporary YAML files to
switch scenarios. It executes `scripts/run.py <config>` exactly once and
validates the resulting summary against the mode already selected by the
user in the loaded configuration.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from testbed.config import ConfigError, load_config
from testbed.runner import select_destination

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = PROJECT_ROOT / "scripts" / "run.py"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "scripts" / "config.yml"
RECEIVER_STOPPED = re.compile(r"Receiver stopped after (\d+) packets")
TX_LINE = re.compile(r"^TX \d+:", re.MULTILINE)


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def _command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _docker_image_exists(image: str) -> bool:
    if not _command_exists("docker"):
        return False
    result = _run(["docker", "image", "inspect", image])
    return result.returncode == 0


def _docker_is_functional() -> bool:
    if not _command_exists("docker"):
        return False
    result = _run(["docker", "info"])
    return result.returncode == 0


def _imports_are_available() -> bool:
    script = "import containernet.net; import containernet.node; import mn_wifi.link; import mn_wifi.wmediumdConnector"
    result = _run([sys.executable, "-c", script], cwd=PROJECT_ROOT)
    return result.returncode == 0


def _batman_adv_is_available() -> bool:
    if Path("/sys/module/batman_adv").exists():
        return True
    for module_name in ("batman-adv", "batman_adv"):
        if _command_exists("modinfo") and _run(["modinfo", module_name]).returncode == 0:
            return True
    return False


def check_prerequisites() -> list[str]:
    missing: list[str] = []
    for command in ("docker", "mn", "tcpdump", "wmediumd"):
        if not _command_exists(command):
            missing.append(f"required command not found in PATH: {command}")
    if not _docker_is_functional():
        missing.append("Docker is not functional for this user/environment")
    if not _imports_are_available():
        missing.append("Containernet/Mininet-WiFi Python modules are not importable")
    if not _batman_adv_is_available():
        missing.append("BATMAN-adv is not available on the host")
    if not _docker_image_exists("drone:latest"):
        missing.append("Docker image is missing: drone:latest")
    return missing


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as summary_file:
        data = json.load(summary_file)
    if not isinstance(data, dict):
        raise ValueError(f"summary is not a JSON object: {path}")
    return data


def _receiver_counts(summary: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for receiver in summary.get("receivers", []):
        match = RECEIVER_STOPPED.search(receiver.get("stdout", ""))
        if match:
            counts[receiver.get("node", "")] = int(match.group(1))
    return counts


def _sender_tx_count(summary: dict[str, Any]) -> int:
    sender = summary.get("sender") or {}
    return len(TX_LINE.findall(sender.get("stdout", "")))


def _assert_file_readable(path_value: str, errors: list[str]) -> None:
    path = Path(path_value)
    if not path.is_file():
        errors.append(f"output does not exist: {path}")
        return
    try:
        with path.open("rb") as output_file:
            output_file.read(1)
    except OSError as exc:
        errors.append(f"output is not readable: {path}: {exc}")


def validate_summary(config: dict[str, Any], summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    protocol = config["protocol"]
    expected_receivers = protocol["receivers"]
    expected_count = protocol["count"]
    sender = summary.get("sender") or {}
    receivers = summary.get("receivers", [])
    receiver_names = [receiver.get("node") for receiver in receivers]

    if summary.get("status") != "success":
        errors.append(f"summary status is {summary.get('status')!r}, expected 'success'")
    if summary.get("process_status") != "success":
        errors.append(f"process_status is {summary.get('process_status')!r}, expected 'success'")
    if summary.get("capture_status") != "valid":
        errors.append(f"capture_status is {summary.get('capture_status')!r}, expected 'valid'")
    if summary.get("traffic_status") != "observed":
        errors.append(f"traffic_status is {summary.get('traffic_status')!r}, expected 'observed'")
    if sender.get("exit_code") != 0:
        errors.append(f"sender exit_code is {sender.get('exit_code')!r}, expected 0")

    if receiver_names != expected_receivers:
        errors.append(f"receivers are {receiver_names!r}, expected {expected_receivers!r}")
    for receiver in receivers:
        if receiver.get("exit_code") != 0:
            errors.append(f"receiver {receiver.get('node')} exit_code is {receiver.get('exit_code')!r}, expected 0")

    receiver_counts = _receiver_counts(summary)
    for receiver in expected_receivers:
        received = receiver_counts.get(receiver)
        if received != expected_count:
            errors.append(f"receiver {receiver} counted {received!r} packets, expected {expected_count}")

    sent = _sender_tx_count(summary)
    if sent != expected_count:
        errors.append(f"sender stdout has {sent} TX lines, expected {expected_count}")

    command = sender.get("command") or []
    if "--mode" in command:
        errors.append("sender command must not contain --mode")

    try:
        # The expected destination comes from the same rule the runner applies,
        # so this check never re-implements the unicast/broadcast selection.
        expected_destination = select_destination(config)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if expected_destination not in command:
            errors.append(
                f"sender command does not use the expected {protocol['mode']} destination: {expected_destination}"
            )

    metrics = summary.get("metrics") or {}
    if not isinstance(metrics.get("packet_count"), int) or metrics.get("packet_count") <= 0:
        errors.append(f"capture packet_count is {metrics.get('packet_count')!r}, expected a positive integer")

    outputs = summary.get("outputs") or {}
    for key in ("pcap", "csv", "summary"):
        _assert_file_readable(outputs.get(key, ""), errors)

    return errors


def check_cleanup(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _command_exists("docker"):
        return errors
    for node in config["nodes"].values():
        container_name = node["container_name"]
        result = _run(
            ["docker", "ps", "--filter", f"name=^/{container_name}$", "--format", "{{.Names}}"]
        )
        if result.returncode == 0 and result.stdout.strip():
            errors.append(f"container still running after cleanup: {container_name}")
    return errors


def run_scenario(config_path: Path) -> tuple[bool, str]:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        return False, f"FALHOU - configuração inválida: {exc}"

    mode = config["protocol"]["mode"]
    command = [sys.executable, str(RUN_SCRIPT), str(config_path)]
    result = _run(command, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        return False, (
            f"{mode}: FALHOU - comando retornou {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    summary_path = Path(config["output"]["summary"])
    try:
        summary = _load_json(summary_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"{mode}: FALHOU - summary ilegível: {exc}"

    errors = validate_summary(config, summary)
    errors.extend(check_cleanup(config))
    if errors:
        return False, f"{mode}: FALHOU\n- " + "\n- ".join(errors)

    counts = _receiver_counts(summary)
    capture_count = summary.get("metrics", {}).get("packet_count")
    return True, (
        f"{mode}: PASSOU - enviados={config['protocol']['count']} "
        f"recebidos={counts} captura_udp={capture_count}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the single scenario (Unicast or Broadcast) selected in scripts/config.yml."
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the YAML configuration. Defaults to scripts/config.yml.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)

    missing: list[str] = []
    if os.geteuid() != 0:
        missing.append("script must be executed as root; run: sudo python3 scripts/validate_scenario.py")
    missing.extend(check_prerequisites())
    if missing:
        print("NÃO EXECUTADO: ambiente sem pré-requisitos para validação real.", file=sys.stderr)
        for item in missing:
            print(f"- {item}", file=sys.stderr)
        return 3

    ok, message = run_scenario(config_path)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
