#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from testbed.config import ConfigError, load_config
from testbed.metrics import start_capture, write_outputs
from testbed.network import build_network, cleanup_mininet, cleanup_stale_environment, wait_for_network
from testbed.rundir import DEFAULT_OUTPUT_DIR, prepare_run_directory
from testbed.runner import execute_protocol
from testbed.timing import Timeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MVP Demo-1 testbed experiment.")
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help="Path to the YAML configuration. Defaults to scripts/config.yml.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Base directory for results; each run goes to <dir>/<experiment.id>/<date-time>/. Defaults to logs/.",
    )
    return parser.parse_args()


def _write_timing(path: str, timeline: Timeline) -> None:
    with open(path, "w", encoding="utf-8") as timing_file:
        json.dump(timeline.to_dict(), timing_file, indent=2, ensure_ascii=False)
        timing_file.write("\n")


def main() -> int:
    args = parse_args()

    network = None
    capture = None
    config = None
    output = None
    timeline = Timeline()
    phase = timeline.start("load_config")
    network_attempted = False

    print(f"Executing: {Path(__file__).resolve()}", file=sys.stderr)

    try:
        config = load_config(args.config)

        phase = timeline.start("prepare_output")
        output = prepare_run_directory(config, args.output_dir)
        print(f"Run directory: {output['directory']}", file=sys.stderr)

        phase = timeline.start("prepare_host")
        cleanup_stale_environment(config)

        phase = timeline.start("build_network")
        network_attempted = True
        network = build_network(config)

        phase = timeline.start("wait_network")
        ready_after = wait_for_network(config, network.nodes)
        print(f"Mesh ready after {ready_after:.1f}s", file=sys.stderr)

        phase = timeline.start("start_capture")
        capture = start_capture(config, network.nodes)

        phase = timeline.start("execute_protocol")
        protocol_result = execute_protocol(config, network.nodes)

        phase = timeline.start("stop_capture")
        capture_result = capture.stop(config["experiment"]["shutdown_timeout_seconds"])
        capture = None

        phase = timeline.start("write_outputs")
        summary = write_outputs(config, protocol_result, capture_result)

        if summary["status"] == "success":
            print("Experiment completed successfully.", flush=True)
            return 0

        print(f"Experiment completed with {summary['status']} status:", file=sys.stderr)
        for failure in summary.get("failures", []):
            print(f"  - {failure}", file=sys.stderr)
        for warning in summary.get("warnings", []):
            print(f"  - warning: {warning}", file=sys.stderr)
        return 1

    except ConfigError as exc:
        timeline.fail()
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    except Exception as exc:
        timeline.fail()
        print(
            f"execution error during {phase}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        return 1

    finally:
        timeline.start("cleanup")
        if capture is not None:
            try:
                # A capture only exists when load_config() already succeeded, so
                # the normalized shutdown timeout is always available here.
                capture.stop(config["experiment"]["shutdown_timeout_seconds"])
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

        timeline.finish()
        if output is not None:
            # Written last, so it also covers cleanup and failed runs.
            try:
                _write_timing(output["timing"], timeline)
            except Exception as timing_error:
                print(f"warning: could not write timing: {timing_error}", file=sys.stderr)
        print(f"Total time: {timeline.to_dict()['duration_seconds']:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
