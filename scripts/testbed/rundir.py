from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "logs"
# Fixed names inside every run directory, so runs are comparable and easy to
# process by script (e.g. logs/<experiment>/*/summary.json).
RUN_FILES = {
    "config": "config.yml",
    "csv": "metrics.csv",
    "summary": "summary.json",
    "timing": "timing.json",
    "metadata": "metadata.json",
    # D15: written during the protocol phase
    "positions": "positions.csv",
    "routes": "routes.csv",
    "signal": "signal.csv",
}
# One PCAP per participant: capture-<node id>.pcap
PCAP_NAME = "capture-{node}.pcap"
# Copy of each trajectory CSV used (nodes[].mobility.waypoints_csv), so the
# run stays reproducible even if the original CSV is edited later (D15).
TRAJECTORY_NAME = "trajectory-{node}.csv"
_TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"


def prepare_run_directory(
    config: dict[str, Any],
    base_dir: str | Path = DEFAULT_OUTPUT_DIR,
    now: datetime | None = None,
) -> dict[str, str]:
    """Create <base_dir>/<experiment.id>/<timestamp>/ and copy the config into it.

    Sets config["output"] to the paths of the run's files and returns them.
    Two runs started in the same second get "-2", "-3"... suffixes instead of
    sharing a directory.
    """
    stamp = (now or datetime.now()).strftime(_TIMESTAMP_FORMAT)
    experiment_dir = Path(base_dir).resolve() / config["experiment"]["id"]
    run_dir = experiment_dir / stamp
    suffix = 2
    while True:
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            run_dir = experiment_dir / f"{stamp}-{suffix}"
            suffix += 1

    output = {"directory": str(run_dir)}
    output.update({key: str(run_dir / name) for key, name in RUN_FILES.items()})
    protocol = config["protocol"]
    output["pcaps"] = {
        node: str(run_dir / PCAP_NAME.format(node=node)) for node in [protocol["sender"], *protocol["receivers"]]
    }
    config_path = config.get("config_path")
    if config_path:
        shutil.copyfile(config_path, output["config"])
    output["trajectories"] = {}
    for name, node in (config.get("nodes") or {}).items():
        source = (node.get("mobility") or {}).get("source")
        if source and source != "config":
            copy = run_dir / TRAJECTORY_NAME.format(node=name)
            shutil.copyfile(source, copy)
            output["trajectories"][name] = str(copy)
    config["output"] = output
    return output
