import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.testbed import metadata
from scripts.testbed.metadata import collect_metadata, sha256_file, write_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config(root=PROJECT_ROOT):
    return {
        "root": str(root),
        "nodes": {"gcs": {"image": "drone:latest"}, "dr1": {"image": "custom:1"}, "dr2": {"image": "drone:latest"}},
        "binaries": {
            "sender": {"host": str(PROJECT_ROOT / "bin" / "sender")},
            "receiver": {"host": str(PROJECT_ROOT / "bin" / "receiver")},
        },
    }


def _fake_run(command):
    if command[0] == "docker":
        return json.dumps({"Id": f"sha256:id-of-{command[-1]}", "RepoDigests": [], "Created": "2026-11-01"})
    if "status" in command:
        return " M scripts/run.py"
    if "--abbrev-ref" in command:
        return "semana1-execucao-real"
    return "abc123"


class MetadataTests(unittest.TestCase):
    def test_sha256_matches_hashlib(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"protocol")
        self.addCleanup(Path(tmp.name).unlink)

        self.assertEqual(sha256_file(tmp.name), hashlib.sha256(b"protocol").hexdigest())

    def test_collects_code_binaries_images_and_host(self):
        with patch("scripts.testbed.metadata._run", side_effect=_fake_run), \
             patch("scripts.testbed.metadata._batman_version", return_value="2024.0"):
            data = collect_metadata(_config(), argv=["scripts/run.py"])

        self.assertEqual(data["command"], ["scripts/run.py"])
        self.assertEqual(data["testbed"]["commit"], "abc123")
        self.assertTrue(data["testbed"]["uncommitted_changes"])
        self.assertEqual(data["testbed"]["changed_files"], ["scripts/run.py"])
        self.assertEqual(
            data["binaries"]["sender"]["sha256"],
            hashlib.sha256((PROJECT_ROOT / "bin" / "sender").read_bytes()).hexdigest(),
        )
        # Each distinct image once, identified by content id, not only by name.
        self.assertEqual(sorted(data["images"]), ["custom:1", "drone:latest"])
        self.assertEqual(data["images"]["custom:1"]["id"], "sha256:id-of-custom:1")
        self.assertEqual(data["host"]["batman_adv"], "2024.0")
        self.assertTrue(data["host"]["kernel"])
        self.assertIn("cannot", data["randomness"])

    def test_git_reads_repository_owned_by_another_user(self):
        with patch("scripts.testbed.metadata._run", side_effect=_fake_run) as run:
            collect_metadata(_config(), argv=[])

        git_calls = [call.args[0] for call in run.call_args_list if call.args[0][0] == "git"]
        self.assertTrue(git_calls)
        for command in git_calls:
            self.assertIn(f"safe.directory={PROJECT_ROOT}", command)

    def test_missing_tools_are_recorded_and_never_raise(self):
        def broken(command):
            raise FileNotFoundError(command[0])

        with patch("scripts.testbed.metadata._run", side_effect=broken), \
             patch("scripts.testbed.metadata._batman_version", side_effect=FileNotFoundError("batman")):
            data = collect_metadata(_config(), argv=[])

        self.assertIn("error", data["testbed"])
        self.assertIn("error", data["images"]["drone:latest"])
        self.assertIn("error", data["host"]["batman_adv"])

    def test_real_git_repository_is_read(self):
        try:
            subprocess.run(["git", "--version"], check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        data = collect_metadata(_config(), argv=[])

        self.assertRegex(data["testbed"]["commit"], r"^[0-9a-f]{40}$")

    def test_write_metadata_is_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.json"
            write_metadata(str(path), {"a": 1})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})


if __name__ == "__main__":
    unittest.main()
