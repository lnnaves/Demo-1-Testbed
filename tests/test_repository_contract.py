import re
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OWN_FILES = [
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "bin" / "BIN_INTERFACE.md",
    PROJECT_ROOT / "bin" / "sender",
    PROJECT_ROOT / "bin" / "receiver",
    PROJECT_ROOT / "scripts" / "config.yml",
    PROJECT_ROOT / "scripts" / "run.py",
    PROJECT_ROOT / "scripts" / "validate_scenario.py",
    PROJECT_ROOT / "scripts" / "build-docker.sh",
    *sorted((PROJECT_ROOT / "scripts" / "testbed").glob("*.py")),
    # This module itself is skipped: it necessarily spells out the removed
    # names it guards against.
    *sorted(p for p in (PROJECT_ROOT / "tests").glob("*.py") if p.name != Path(__file__).name),
]


def _sources():
    return {path: path.read_text(encoding="utf-8") for path in OWN_FILES}


class RepositoryContractTests(unittest.TestCase):
    """Regression guards for contracts kept after the MVP cleanup.

    Only project-owned files are inspected; third-party/containernet/ is
    vendored code and is never scanned or modified here.
    """

    def test_no_reference_to_removed_per_mode_yaml_files(self):
        for path, text in _sources().items():
            with self.subTest(path=path.name):
                self.assertNotIn("config.unicast.yml", text)
                self.assertNotIn("config.broadcast.yml", text)

    def test_no_reference_to_the_removed_plural_validator(self):
        for path, text in _sources().items():
            with self.subTest(path=path.name):
                self.assertNotIn("validate_scenarios", text)

    def test_only_scripts_config_yml_is_versioned_as_configuration(self):
        found = sorted(p.name for p in (PROJECT_ROOT / "scripts").glob("*.yml"))

        self.assertEqual(found, ["config.yml"])

    def test_sender_binary_accepts_only_destination_and_port(self):
        text = (PROJECT_ROOT / "bin" / "sender").read_text(encoding="utf-8")

        self.assertNotIn('"--mode"', text)
        self.assertNotIn('"--count"', text)
        self.assertIn('"--destination"', text)
        self.assertIn('"--port"', text)

    def test_receiver_binary_accepts_address_and_port(self):
        text = (PROJECT_ROOT / "bin" / "receiver").read_text(encoding="utf-8")

        self.assertIn('"--address"', text)
        self.assertIn('"--port"', text)

    def test_no_reference_protocol_semantics_is_required_by_the_platform(self):
        for path, text in _sources().items():
            with self.subTest(path=path.name):
                self.assertNotIn("protocol.count", text)
                self.assertNotIn("timestamp_ns=", text)
                self.assertNotIn("Receiver stopped after", text)

    def test_container_image_is_neutral_and_has_python_and_network_runtime(self):
        dockerfile = (PROJECT_ROOT / "dockerfiles" / "Dockerfile.drone").read_text(encoding="utf-8")

        self.assertNotIn("ardupilot", dockerfile.lower())
        self.assertNotIn("perf", dockerfile)
        self.assertNotIn("linux-tools", dockerfile)
        for package in ("python3", "iproute2", "batctl", "tcpdump", "iw"):
            self.assertIn(package, dockerfile)
        self.assertIn("WORKDIR /workspace", dockerfile)
        self.assertIn('CMD ["/bin/bash"]', dockerfile)

    def test_metrics_report_intervals_and_never_end_to_end_latency(self):
        text = (PROJECT_ROOT / "scripts" / "testbed" / "metrics.py").read_text(encoding="utf-8")

        self.assertIn("interval_mean_seconds", text)
        self.assertNotIn("latency_", text)

    def test_generated_artifacts_are_not_versioned(self):
        """Local results under logs/ are legitimate; only .gitkeep is tracked."""
        ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("logs/*", ignored)
        self.assertIn("!logs/.gitkeep", ignored)

        result = subprocess.run(
            ["git", "ls-files", "logs"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("git is not available to inspect tracked files")

        tracked = sorted(line.strip() for line in result.stdout.splitlines() if line.strip())
        self.assertEqual(tracked, ["logs/.gitkeep"])

    def test_build_script_matches_the_documented_dockerfile_and_image(self):
        script = (PROJECT_ROOT / "scripts" / "build-docker.sh").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertTrue((PROJECT_ROOT / "dockerfiles" / "Dockerfile.drone").is_file())
        self.assertIn("dockerfiles/Dockerfile.drone", script)
        self.assertIn("drone:latest", script)
        self.assertIn("scripts/build-docker.sh", readme)

    def test_readme_documents_the_single_mode_per_run_contract(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("scripts/config.yml", readme)
        self.assertIn("protocol.mode", readme)
        self.assertTrue(re.search(r"inconclusive", readme))
        self.assertNotIn("sender --mode", readme)


if __name__ == "__main__":
    unittest.main()
