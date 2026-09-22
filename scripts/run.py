#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from testbed.config import ConfigError, load_config
from testbed.metrics import start_capture, write_outputs
from testbed.network import build_network, cleanup_mininet
from testbed.runner import execute_protocol

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MVP Demo-1 testbed experiment.")
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help="Path to the YAML configuration. Defaults to scripts/config.yml.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    network = None
    capture = None
    config = None
    phase = "load_config"
    network_attempted = False

    protocol_result = {"status": "failed", "duration_seconds": 0.0, "failures": []}
    capture_result = {"started": False, "packet_count": 0}

    print(f"Executing: {Path(__file__).resolve()}", file=sys.stderr)

    try:
        config = load_config(args.config)

        phase = "build_network"
        network_attempted = True
        network = build_network(config)

        phase = "start_capture"
        capture = start_capture(config, network.nodes)

        phase = "execute_protocol"
        protocol_result = execute_protocol(config, network.nodes)

        phase = "stop_capture"
        capture_result = capture.stop(config["experiment"]["shutdown_timeout_seconds"])
        capture = None

        phase = "write_outputs"
        summary = write_outputs(config, protocol_result, capture_result)

        if summary["status"] == "success":
            print("Experiment completed successfully.")
            return 0

        print(f"Experiment completed with {summary['status']} status:", file=sys.stderr)
        for failure in summary.get("failures", []):
            print(f"  - {failure}", file=sys.stderr)
        for warning in summary.get("warnings", []):
            print(f"  - warning: {warning}", file=sys.stderr)
        return 1

    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    except Exception as exc:
        protocol_result.setdefault("failures", []).append(str(exc))
        print(
            f"execution error during {phase}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        return 1

    finally:
        if capture is not None:
            try:
                timeout = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
                if config is not None:
                    timeout = config["experiment"].get(
                        "shutdown_timeout_seconds", DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
                    )
                capture.stop(timeout)
            except Exception as cleanup_error:
                print(f"warning: could not stop capture: {cleanup_error}", file=sys.stderr)

        if network is not None:
            try:
                network.stop()
            except Exception as cleanup_error:
                print(f"warning: could not stop network: {cleanup_error}", file=sys.stderr)

        if network_attempted:
            try:
                cleanup_mininet()
            except Exception as cleanup_error:
                print(f"warning: Mininet cleanup failed: {cleanup_error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
