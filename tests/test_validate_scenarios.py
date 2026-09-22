import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
_added_scripts_dir = SCRIPTS_DIR not in sys.path
if _added_scripts_dir:
    sys.path.insert(0, SCRIPTS_DIR)

try:
    import validate_scenarios  # noqa: E402
    from testbed.config import load_config  # noqa: E402
finally:
    if _added_scripts_dir:
        sys.path.remove(SCRIPTS_DIR)


def _config(mode="unicast", receivers=None):
    receivers = receivers or ["gcs"]
    return {
        "protocol": {
            "mode": mode,
            "port": 5101,
            "count": 5,
            "sender": "drone1",
            "receivers": receivers,
        },
        "wireless": {"broadcast_ip": "192.168.123.255"},
        "nodes": {
            "gcs": {"address": "192.168.123.1", "container_name": "gcs0"},
            "drone1": {"address": "192.168.123.2", "container_name": "dr1"},
            "drone2": {"address": "192.168.123.3", "container_name": "dr2"},
        },
        "output": {},
    }


def _summary(tmp, mode="unicast", receivers=None):
    receivers = receivers or ["gcs"]
    outputs = {
        "pcap": str(Path(tmp) / f"{mode}.pcap"),
        "csv": str(Path(tmp) / f"{mode}.csv"),
        "summary": str(Path(tmp) / f"{mode}-summary.json"),
    }
    for path in outputs.values():
        Path(path).write_bytes(b"x")
    destination = "192.168.123.255" if mode == "broadcast" else "192.168.123.1"
    return {
        "status": "success",
        "process_status": "success",
        "capture_status": "valid",
        "traffic_status": "observed",
        "sender": {
            "node": "drone1",
            "exit_code": 0,
            "command": ["/opt/protocol/bin/sender", "--destination", destination, "--port", "5101", "--count", "5"],
            "stdout": "\n".join(f"TX {index}: ok" for index in range(1, 6)),
        },
        "receivers": [
            {"node": receiver, "exit_code": 0, "stdout": "Receiver stopped after 5 packets"}
            for receiver in receivers
        ],
        "metrics": {"packet_count": 5},
        "outputs": outputs,
    }


class ValidateScenariosTests(unittest.TestCase):
    def test_repository_scenario_configs_load(self):
        root = Path(__file__).resolve().parents[1]
        unicast = load_config(root / "scripts" / "config.unicast.yml", root=root)
        broadcast = load_config(root / "scripts" / "config.broadcast.yml", root=root)

        self.assertEqual(unicast["protocol"]["mode"], "unicast")
        self.assertEqual(unicast["protocol"]["receivers"], ["gcs"])
        self.assertEqual(broadcast["protocol"]["mode"], "broadcast")
        self.assertEqual(broadcast["protocol"]["receivers"], ["gcs", "drone2"])

    def test_validate_summary_accepts_unicast_reference_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_scenarios.validate_summary(_config(), _summary(tmp))

        self.assertEqual(errors, [])

    def test_validate_summary_accepts_each_broadcast_receiver(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_scenarios.validate_summary(
                _config(mode="broadcast", receivers=["gcs", "drone2"]),
                _summary(tmp, mode="broadcast", receivers=["gcs", "drone2"]),
            )

        self.assertEqual(errors, [])

    def test_validate_summary_rejects_wrong_count_extra_receiver_and_mode_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = _summary(tmp, receivers=["gcs", "drone2"])
            summary["sender"]["command"].append("--mode")
            summary["receivers"][0]["stdout"] = "Receiver stopped after 4 packets"
            errors = validate_scenarios.validate_summary(_config(), summary)

        self.assertTrue(any("receivers are" in error for error in errors))
        self.assertTrue(any("counted 4" in error for error in errors))
        self.assertTrue(any("--mode" in error for error in errors))

    def test_check_prerequisites_requires_root_without_elevation(self):
        with patch("validate_scenarios.os.geteuid", return_value=1000), \
             patch("validate_scenarios._command_exists", return_value=True), \
             patch("validate_scenarios._docker_is_functional", return_value=True), \
             patch("validate_scenarios._imports_are_available", return_value=True), \
             patch("validate_scenarios._batman_adv_is_available", return_value=True), \
             patch("validate_scenarios._docker_image_exists", return_value=True):
            missing = validate_scenarios.check_prerequisites()

        self.assertTrue(any("root" in item for item in missing))

    def test_run_scenario_stops_when_command_fails(self):
        scenario = validate_scenarios.Scenario("Unicast", Path("/tmp/config.yml"))
        config = _config()
        config["output"] = {
            "pcap": str(Path("/tmp/logs/mvp-unicast.pcap")),
            "csv": str(Path("/tmp/logs/mvp-unicast.csv")),
            "summary": str(Path("/tmp/logs/mvp-unicast-summary.json")),
        }
        completed = MagicMock(returncode=1, stdout="out", stderr="err")

        with patch("validate_scenarios.load_config", return_value=config), \
             patch("validate_scenarios.remove_scenario_outputs"), \
             patch("validate_scenarios._run", return_value=completed):
            ok, message = validate_scenarios.run_scenario(scenario)

        self.assertFalse(ok)
        self.assertIn("retornou 1", message)
        self.assertIn("stderr", message)


if __name__ == "__main__":
    unittest.main()
