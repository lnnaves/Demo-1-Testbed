#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from pathlib import Path

from testbed.campaign import (
    REPETITION_NAME,
    campaign_directory,
    format_table,
    repetition_measures,
    repetition_seeds,
    summarize,
    write_statistics,
)
from testbed.config import CONFIG_PATH, ConfigError, load_config
from testbed.latency import analyze_run
from testbed.metadata import collect_metadata, write_metadata
from testbed.metrics import start_capture, write_outputs
from testbed.mobility import MobilityController
from testbed.network import build_network, cleanup_mininet, cleanup_stale_environment, station_macs, wait_for_network
from testbed.observer import NetworkObserver
from testbed.rundir import DEFAULT_OUTPUT_DIR, prepare_run_directory
from testbed.runner import execute_protocol
from testbed.timing import Timeline
from testbed.windows import analyze_windows, describe_first_loss


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MVP Demo-1 testbed experiment.")
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help="Path to the YAML configuration. Defaults to scripts/config.yml.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="Run the same config N times and write statistics (mean, stdev, 95%% CI) of the measures (D20).",
    )
    parser.add_argument(
        "--vary-seed",
        action="store_true",
        help="With --repeat: Random Waypoint seed + (repetition - 1), i.e. a different random path per repetition.",
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


class ScenarioMonitor:
    """Moves nodes (mobility) and samples routes/signal while the protocol runs (D15)."""

    def __init__(self, config: dict, nodes: dict):
        output = config["output"]
        locks = {name: threading.Lock() for name in config["nodes"]}
        self.mobility = MobilityController(config, nodes, output["positions"], locks)
        self.observer = NetworkObserver(
            config, nodes, station_macs(config), output["routes"], output["signal"], locks, self.mobility.t
        )
        self._result: dict | None = None

    def start(self) -> "ScenarioMonitor":
        self.mobility.start()
        self.observer.start()
        return self

    def sender_started(self, wall: float) -> None:
        self.mobility.sender_started(wall)

    def stop(self) -> dict:
        if self._result is None:
            self._result = {"mobility": self.mobility.stop(), "observer": self.observer.stop()}
        return self._result


def _analyze_windows(config: dict, protocol_result: dict, network_analysis: dict | None) -> dict | None:
    """Latency/loss per time window and distance band (D16); attached to the latency block.

    Problems become warnings of the latency analysis; the status never changes.
    """
    if not network_analysis or not network_analysis.get("valid"):
        return None
    sender = protocol_result.get("sender") or {}
    if sender.get("started_at") is None or sender.get("ended_at") is None:
        network_analysis["warnings"].append("windows: sender start/end unknown, tables not written")
        return None
    analysis = config["analysis"]
    windows = analyze_windows(
        config["output"]["directory"], network_analysis["files"], sender["started_at"], sender["ended_at"],
        analysis["window_seconds"], analysis["distance_bin_meters"],
    )
    network_analysis["windows"] = windows
    network_analysis["warnings"].extend(f"windows: {error}" for error in windows["errors"])
    return windows


def run_once(
    config_path: str | None,
    output_dir: str,
    run_name: str | None = None,
    seeds: dict | None = None,
) -> tuple[int, dict]:
    """One complete execution. Returns the exit code and what a campaign needs (D20)."""
    outcome: dict = {"directory": None, "summary": None, "mesh_ready_seconds": None}
    network = None
    capture = None
    monitor = None
    config = None
    output = None
    timeline = Timeline()
    phase = timeline.start("load_config")
    network_attempted = False

    print(f"Executing: {Path(__file__).resolve()}", file=sys.stderr)

    try:
        config = load_config(config_path) if seeds is None else load_config(config_path, seeds=seeds)

        phase = timeline.start("prepare_output")
        if run_name is None:
            output = prepare_run_directory(config, output_dir)
        else:
            output = prepare_run_directory(config, output_dir, run_name=run_name)
        outcome["directory"] = output["directory"]
        print(f"Run directory: {output['directory']}", file=sys.stderr)

        phase = timeline.start("collect_metadata")
        write_metadata(output["metadata"], collect_metadata(config))

        phase = timeline.start("prepare_host")
        cleanup_stale_environment(config)

        phase = timeline.start("build_network")
        network_attempted = True
        network = build_network(config)

        phase = timeline.start("wait_network")
        ready_after = wait_for_network(config, network.nodes)
        print(f"Mesh ready after {ready_after:.1f}s", file=sys.stderr)
        outcome["mesh_ready_seconds"] = ready_after

        phase = timeline.start("start_capture")
        capture = start_capture(config, network.nodes)

        phase = timeline.start("execute_protocol")
        monitor = ScenarioMonitor(config, network.nodes).start()
        protocol_result = execute_protocol(config, network.nodes, monitor.sender_started)
        scenario_result = monitor.stop()
        monitor = None

        phase = timeline.start("stop_capture")
        capture_result = capture.stop(config["experiment"]["shutdown_timeout_seconds"])
        capture = None

        phase = timeline.start("analyze_network")
        network_analysis = analyze_run(config) if capture_result.get("valid") else None
        windows = _analyze_windows(config, protocol_result, network_analysis)

        phase = timeline.start("write_outputs")
        summary = write_outputs(config, protocol_result, capture_result, network_analysis, scenario_result)
        outcome["summary"] = summary

        mobility = scenario_result.get("mobility") or {}
        observer = scenario_result.get("observer") or {}
        if mobility.get("moving_nodes"):
            print(
                f"Mobility: moved {', '.join(mobility['moving_nodes'])} ({mobility.get('ticks', 0)} position updates)",
                file=sys.stderr,
            )
        print(f"Routes/signal samples: {observer.get('samples', 0)}", file=sys.stderr)
        for direction, result in ((summary.get("latency") or {}).get("directions") or {}).items():
            total = result["total"]
            median_ms = total["latency_median_ms"]
            print(
                f"{direction}: {total['delivered']}/{total['sent']} delivered, "
                f"median latency {'-' if median_ms is None else f'{median_ms:.3f} ms'}",
                file=sys.stderr,
            )
        if windows is not None:
            for direction in sorted(windows["files"]):
                print(describe_first_loss(direction, windows["first_loss"][direction]), file=sys.stderr)

        if summary["status"] == "success":
            print("Experiment completed successfully.", flush=True)
            return 0, outcome

        print(f"Experiment completed with {summary['status']} status:", file=sys.stderr)
        for failure in summary.get("failures", []):
            print(f"  - {failure}", file=sys.stderr)
        for warning in summary.get("warnings", []):
            print(f"  - warning: {warning}", file=sys.stderr)
        return 1, outcome

    except ConfigError as exc:
        timeline.fail()
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2, outcome

    except Exception as exc:
        timeline.fail()
        print(
            f"execution error during {phase}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        return 1, outcome

    finally:
        timeline.start("cleanup")
        if monitor is not None:
            try:
                monitor.stop()
            except Exception as cleanup_error:
                print(f"warning: could not stop mobility/observer: {cleanup_error}", file=sys.stderr)

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


def run_campaign(args: argparse.Namespace) -> int:
    """Run the config args.repeat times and write the statistics (D20)."""
    try:
        first = load_config(args.config)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    # Random Waypoint seeds are fixed for the whole campaign (drawn once if
    # the config has none), so every repetition follows the same path unless
    # --vary-seed is given.
    base_seeds = {
        name: node["mobility"]["parameters"]["seed"]
        for name, node in first["nodes"].items()
        if node["mobility"].get("type") == "random_waypoint"
    }
    directory = campaign_directory(first["experiment"]["id"], args.output_dir)
    print(f"Campaign directory: {directory} ({args.repeat} repetitions)", file=sys.stderr)
    repetitions = []
    for index in range(1, args.repeat + 1):
        name = REPETITION_NAME.format(index=index)
        seeds = repetition_seeds(base_seeds, index, args.vary_seed)
        print(f"\n=== Repetition {index}/{args.repeat} ({name}) ===", file=sys.stderr, flush=True)
        exit_code, outcome = run_once(args.config, str(directory), run_name=name, seeds=seeds)
        summary = outcome["summary"]
        status = summary["status"] if summary else "not finished"
        used = summary is not None and status != "failed"
        reason = None
        if not used:
            reason = "; ".join(summary.get("failures", [])) if summary else f"execution stopped (exit code {exit_code})"
        repetitions.append({
            "name": name,
            "directory": outcome["directory"],
            "status": status,
            "used": used,
            "reason": reason,
            "seeds": seeds,
            "measures": repetition_measures(summary, outcome["mesh_ready_seconds"]),
        })
        print(f"{name}: {status}" + ("" if used else f" (left out: {reason})"), file=sys.stderr)

    statistics = summarize(repetitions)
    used = sum(1 for rep in repetitions if rep["used"])
    campaign = {
        "config": str(Path(args.config).resolve()) if args.config else str(CONFIG_PATH),
        "repetitions_requested": args.repeat,
        "repetitions_used": used,
        "vary_seed": args.vary_seed,
        "random_waypoint_base_seeds": base_seeds,
        "confidence_interval": "95%, Student's t, sample standard deviation",
    }
    files = write_statistics(directory, campaign, repetitions, statistics)
    print(f"\nCampaign: {used}/{args.repeat} repetitions used in the statistics", file=sys.stderr)
    for line in format_table(statistics):
        print(line, file=sys.stderr)
    print(f"Statistics: {files['csv']}", file=sys.stderr)
    return 0 if used == args.repeat else 1


def main() -> int:
    args = parse_args()
    if args.repeat < 1:
        print("--repeat must be at least 1", file=sys.stderr)
        return 2
    if args.repeat == 1:
        exit_code, _ = run_once(args.config, args.output_dir)
        return exit_code
    return run_campaign(args)


if __name__ == "__main__":
    raise SystemExit(main())
