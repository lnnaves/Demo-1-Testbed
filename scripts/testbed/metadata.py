from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable

_BATMAN_VERSION_FILE = Path("/sys/module/batman_adv/version")
_PACKAGES = ("mininet-wifi", "mininet", "containernet", "PyYAML")
RANDOMNESS_NOTE = (
    "wmediumd (interference mode, fading_coefficient > 0) draws random fading that "
    "the testbed cannot seed: the same config and versions give the same scenario, "
    "not identical results. Use repetitions and statistics."
)


def _run(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _safe(collector: Callable[[], Any]) -> Any:
    # Metadata must never make an experiment fail: a missing tool is recorded
    # as an error string instead.
    try:
        return collector()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: str) -> dict[str, Any]:
    # run.py runs under sudo while the repository belongs to the user; without
    # safe.directory, git refuses to read it ("dubious ownership").
    base = ["git", "-c", f"safe.directory={root}", "-C", root]
    changes = _run([*base, "status", "--porcelain", "--untracked-files=no"])
    return {
        "commit": _run([*base, "rev-parse", "HEAD"]),
        "branch": _run([*base, "rev-parse", "--abbrev-ref", "HEAD"]),
        # True when tracked files differ from the commit: the code that ran is
        # not exactly the recorded commit.
        "uncommitted_changes": bool(changes),
        "changed_files": [line[3:] for line in changes.splitlines()],
    }


def _image(name: str) -> dict[str, Any]:
    raw = _run(["docker", "image", "inspect", "--format", "{{json .}}", name])
    info = json.loads(raw)
    return {"id": info.get("Id"), "repo_digests": info.get("RepoDigests") or [], "created": info.get("Created")}


def _packages() -> dict[str, Any]:
    versions: dict[str, Any] = {}
    for package in _PACKAGES:
        try:
            versions[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _batman_version() -> str:
    return _BATMAN_VERSION_FILE.read_text(encoding="utf-8").strip()


def collect_metadata(config: dict[str, Any], argv: list[str] | None = None) -> dict[str, Any]:
    uname = platform.uname()
    images = sorted({node["image"] for node in config["nodes"].values()})
    return {
        "created_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": list(argv if argv is not None else sys.argv),
        "user": os.environ.get("SUDO_USER") or os.environ.get("USER"),
        "testbed": _safe(lambda: _git(config["root"])),
        "binaries": {
            role: {
                "path": config["binaries"][role]["host"],
                "sha256": _safe(lambda role=role: sha256_file(config["binaries"][role]["host"])),
            }
            for role in ("sender", "receiver")
        },
        "images": {image: _safe(lambda image=image: _image(image)) for image in images},
        "host": {
            "hostname": uname.node,
            "kernel": uname.release,
            "kernel_version": uname.version,
            "machine": uname.machine,
            "batman_adv": _safe(_batman_version),
            "python": sys.version.split()[0],
            "packages": _safe(_packages),
        },
        "randomness": RANDOMNESS_NOTE,
    }


def write_metadata(path: str, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as metadata_file:
        json.dump(data, metadata_file, indent=2, ensure_ascii=False)
        metadata_file.write("\n")
