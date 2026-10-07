#!/usr/bin/env python3
"""Validate the testbed platform for the scenario configured in scripts/config.yml.

The validation is strictly infrastructural: it checks that the platform builds
the scenario, runs the configured executables, captures traffic, produces
PCAP/CSV/JSON and cleans up. It never interprets payloads, stdout messages or
the semantic success of any protocol, and it never edits or generates YAML.
"""
from __future__ import annotations

import argparse
import json
import os
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
CONTAINERNET_LABEL = "com.containernet"
SUMMARY_KEYS = (
    "process_status",
    "capture_status",
    "traffic_status",
    "sender",
    "receivers",
    "capture",
    "metrics",
    "failures",
    "warnings",
    "outputs",
)


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


def _configured_images(config: dict[str, Any] | None) -> list[str]:
    if config is None:
        return ["drone:latest"]
    return sorted({node["image"] for node in config["nodes"].values()})


def _output_problems(config: dict[str, Any] | None) -> list[str]:
    if config is None:
        return []
    problems: list[str] = []
    for key, value in config["output"].items():
        directory = Path(value).parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problems.append(f"output directory for {key} cannot be created: {directory}: {exc}")
            continue
        if not os.access(directory, os.W_OK):
            problems.append(f"output directory for {key} is not writable: {directory}")
    return problems


def check_prerequisites(config: dict[str, Any] | None = None) -> list[str]:
    """Check host prerequisites only; never elevate privileges or change the host."""
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
    for image in _configured_images(config):
        if not _docker_image_exists(image):
            missing.append(f"Docker image is missing: {image}")
    missing.extend(_output_problems(config))
    return missing


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as summary_file:
        data = json.load(summary_file)
    if not isinstance(data, dict):
        raise ValueError(f"summary is not a JSON object: {path}")
    return data


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
    """Check only platform behaviour: processes, capture, artifacts and status."""
    errors: list[str] = []
    protocol = config["protocol"]
    expected_receivers = protocol["receivers"]
    sender = summary.get("sender") or {}
    receivers = summary.get("receivers", [])
    receiver_names = [receiver.get("node") for receiver in receivers]

    for key in SUMMARY_KEYS:
        if key not in summary:
            errors.append(f"summary is missing the {key!r} field")

    if summary.get("mode") != protocol["mode"]:
        errors.append(f"summary mode is {summary.get('mode')!r}, expected {protocol['mode']!r}")
    if summary.get("status") != "success":
        errors.append(f"summary status is {summary.get('status')!r}, expected 'success'")
    if summary.get("process_status") != "success":
        errors.append(f"process_status is {summary.get('process_status')!r}, expected 'success'")
    if summary.get("capture_status") != "valid":
        errors.append(f"capture_status is {summary.get('capture_status')!r}, expected 'valid'")
    if summary.get("traffic_status") != "observed":
        errors.append(f"traffic_status is {summary.get('traffic_status')!r}, expected 'observed'")

    if sender.get("node") != protocol["sender"]:
        errors.append(f"sender node is {sender.get('node')!r}, expected {protocol['sender']!r}")
    if sender.get("exit_code") != 0:
        errors.append(f"sender exit_code is {sender.get('exit_code')!r}, expected 0")

    if receiver_names != expected_receivers:
        errors.append(f"receivers are {receiver_names!r}, expected {expected_receivers!r}")
    for receiver in receivers:
        if receiver.get("exit_code") != 0:
            errors.append(f"receiver {receiver.get('node')} exit_code is {receiver.get('exit_code')!r}, expected 0")
        receiver_command = receiver.get("command") or []
        if "--address" not in receiver_command or "--port" not in receiver_command:
            errors.append(
                f"receiver {receiver.get('node')} command must contain --address and --port: {receiver_command!r}"
            )

    command = sender.get("command") or []
    for forbidden in ("--mode", "--count"):
        if forbidden in command:
            errors.append(f"sender command must not contain {forbidden}")

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

    capture = summary.get("capture") or {}
    if not capture.get("started"):
        errors.append("capture was not started")
    if not capture.get("valid"):
        errors.append(f"capture is not valid: {capture.get('error')!r}")

    metrics = summary.get("metrics") or {}
    if not isinstance(metrics.get("packet_count"), int) or metrics.get("packet_count") <= 0:
        errors.append(f"capture packet_count is {metrics.get('packet_count')!r}, expected a positive integer")

    outputs = summary.get("outputs") or {}
    for key in ("pcap", "csv", "summary"):
        _assert_file_readable(outputs.get(key, ""), errors)

    return errors


def _docker_containers(filters: list[str]) -> list[str]:
    command = ["docker", "ps", "--all", "--format", "{{.Names}}"]
    for value in filters:
        command.extend(["--filter", value])
    result = _run(command)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def check_cleanup(config: dict[str, Any]) -> list[str]:
    """Report Containernet containers of this experiment left behind by cleanup.

    Containernet names its containers ``mn.<container_name>``; both that form
    and the plain configured name are inspected, including stopped containers.
    Unrelated containers are never reported.
    """
    errors: list[str] = []
    if not _command_exists("docker"):
        return errors

    expected_names: set[str] = set()
    for node in config["nodes"].values():
        container_name = node["container_name"]
        expected_names.update({container_name, f"mn.{container_name}"})

    found: set[str] = set()
    for container_name in sorted(expected_names):
        found.update(_docker_containers([f"name=^/{container_name}$"]))
    found.update(_docker_containers([f"label={CONTAINERNET_LABEL}"]))
    residual = found & expected_names

    for container_name in sorted(residual):
        errors.append(f"container still present after cleanup: {container_name}")
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

    capture_count = summary.get("metrics", {}).get("packet_count")
    return True, (
        f"{mode}: PASSOU - a plataforma montou o cenário, executou os executáveis "
        f"configurados, observou {capture_count} pacotes UDP na porta "
        f"{config['protocol']['port']}, gerou PCAP/CSV/JSON e limpou o ambiente.\n"
        "PASSOU refere-se somente à infraestrutura: nenhum protocolo foi validado semanticamente."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the testbed platform for the single scenario (Unicast or Broadcast) "
            "selected in scripts/config.yml."
        )
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

    config: dict[str, Any] | None = None
    if not missing:
        try:
            # load_config also validates nodes, IPs, wireless, binaries and outputs.
            config = load_config(config_path)
        except ConfigError as exc:
            print(f"FALHOU - configuração inválida: {exc}", file=sys.stderr)
            return 1

    missing.extend(check_prerequisites(config))
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
