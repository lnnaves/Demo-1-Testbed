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
    import validate_scenario  # noqa: E402
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


class ValidateScenarioTests(unittest.TestCase):
    def test_repository_config_loads_the_mode_it_declares(self):
        root = Path(__file__).resolve().parents[1]
        config = validate_scenario.load_config(root / "scripts" / "config.yml", root=root)

        self.assertIn(config["protocol"]["mode"], ("unicast", "broadcast"))

    def test_destination_for_unicast_is_the_single_receiver_address(self):
        config = _config(mode="unicast", receivers=["gcs"])

        self.assertEqual(validate_scenario.select_destination(config), "192.168.123.1")

    def test_destination_for_broadcast_is_wireless_broadcast_ip(self):
        config = _config(mode="broadcast", receivers=["gcs", "drone2"])

        self.assertEqual(validate_scenario.select_destination(config), "192.168.123.255")

    def test_destination_rejects_unicast_with_multiple_receivers(self):
        config = _config(mode="unicast", receivers=["gcs", "drone2"])

        with self.assertRaises(ValueError):
            validate_scenario.select_destination(config)

    def test_validate_summary_accepts_unicast_matching_the_single_configured_receiver(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_scenario.validate_summary(_config(mode="unicast"), _summary(tmp, mode="unicast"))

        self.assertEqual(errors, [])

    def test_validate_summary_accepts_broadcast_matching_every_configured_receiver(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_scenario.validate_summary(
                _config(mode="broadcast", receivers=["gcs", "drone2"]),
                _summary(tmp, mode="broadcast", receivers=["gcs", "drone2"]),
            )

        self.assertEqual(errors, [])

    def test_validate_summary_rejects_wrong_count_extra_receiver_and_mode_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = _summary(tmp, receivers=["gcs", "drone2"])
            summary["sender"]["command"].append("--mode")
            summary["receivers"][0]["stdout"] = "Receiver stopped after 4 packets"
            errors = validate_scenario.validate_summary(_config(), summary)

        self.assertTrue(any("receivers are" in error for error in errors))
        self.assertTrue(any("counted 4" in error for error in errors))
        self.assertTrue(any("--mode" in error for error in errors))

    def test_validate_summary_rejects_broadcast_missing_a_configured_receiver(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(mode="broadcast", receivers=["gcs", "drone2"])
            summary = _summary(tmp, mode="broadcast", receivers=["gcs"])
            errors = validate_scenario.validate_summary(config, summary)

        self.assertTrue(any("receivers are" in error for error in errors))

    def test_check_prerequisites_never_toggles_mode_or_reads_a_second_config(self):
        with patch("validate_scenario._command_exists", return_value=True), \
             patch("validate_scenario._docker_is_functional", return_value=True), \
             patch("validate_scenario._imports_are_available", return_value=True), \
             patch("validate_scenario._batman_adv_is_available", return_value=True), \
             patch("validate_scenario._docker_image_exists", return_value=True):
            missing = validate_scenario.check_prerequisites()

        self.assertEqual(missing, [])

    def test_run_scenario_invokes_run_py_exactly_once_with_the_selected_config_path(self):
        config = _config(mode="unicast")
        config["output"] = {
            "pcap": str(Path("/tmp/logs/exp-unicast.pcap")),
            "csv": str(Path("/tmp/logs/exp-unicast.csv")),
            "summary": str(Path("/tmp/logs/exp-unicast-summary.json")),
        }
        completed = MagicMock(returncode=1, stdout="out", stderr="err")
        config_path = Path("/tmp/config.yml")

        with patch("validate_scenario.load_config", return_value=config), \
             patch("validate_scenario._run", return_value=completed) as run_mock:
            ok, message = validate_scenario.run_scenario(config_path)

        self.assertFalse(ok)
        self.assertIn("retornou 1", message)
        self.assertIn("stderr", message)
        run_mock.assert_called_once()
        command = run_mock.call_args[0][0]
        self.assertEqual(command[-1], str(config_path))

    def test_validation_reuses_the_runner_destination_rule(self):
        from testbed.runner import select_destination

        self.assertIs(validate_scenario.select_destination, select_destination)

    def test_run_scenario_never_iterates_a_list_of_both_modes(self):
        # There is a single scenario per invocation: no SCENARIOS/list-of-modes
        # attribute exists on the module, and run_scenario takes one config path.
        self.assertFalse(hasattr(validate_scenario, "SCENARIOS"))
        self.assertFalse(hasattr(validate_scenario, "Scenario"))

    def test_main_reports_missing_prerequisites_as_not_executed(self):
        with patch("validate_scenario.os.geteuid", return_value=1000):
            with patch.object(sys, "argv", ["validate_scenario.py"]):
                exit_code = validate_scenario.main()

        self.assertEqual(exit_code, 3)


if __name__ == "__main__":
    unittest.main()
