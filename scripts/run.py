#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from testbed.config import ConfigError, load_config
from testbed.metrics import start_capture, write_outputs
from testbed.network import build_network, cleanup_mininet
from testbed.runner import execute_protocol, stop_receivers


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
    protocol_result = {"status": "failed", "duration_seconds": 0.0, "failures": []}
    capture_result = {"started": False, "packet_count": 0}

    try:
        config = load_config(args.config)
        network = build_network(config)
        capture = start_capture(config, network.nodes)
        protocol_result = execute_protocol(config, network.nodes)
        capture_result = capture.stop(config["experiment"]["shutdown_timeout_seconds"])
        summary = write_outputs(config, protocol_result, capture_result)
        return 0 if summary["status"] == "success" else 1
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        protocol_result.setdefault("failures", []).append(str(exc))
        print(f"execution error: {exc}", file=sys.stderr)
        return 1
    finally:
        if capture is not None and capture_result.get("started") is False:
            try:
                capture.stop()
            except Exception:
                pass
        if network is not None:
            try:
                stop_receivers([], 0)
            finally:
                network.stop()
        cleanup_mininet()


if __name__ == "__main__":
    raise SystemExit(main())
