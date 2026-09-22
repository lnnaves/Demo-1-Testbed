import os
import tempfile
import unittest
from pathlib import Path

from scripts.testbed.config import ConfigError, load_config


CONFIG = """
experiment:
  id: exp-test
  duration_seconds: 2
protocol:
  mode: unicast
  port: 5000
  count: 3
  sender: drone1
  receivers: [gcs]
wireless:
  subnet: 192.168.123.0/24
  broadcast_ip: 192.168.123.255
nodes:
  - id: gcs
    container_name: gcs0
    ip: 192.168.123.1/24
    position: [0, 0, 0]
  - id: drone1
    container_name: dr1
    ip: 192.168.123.2/24
    position: [1, 0, 0]
  - id: drone2
    container_name: dr2
    ip: 192.168.123.3/24
    position: [2, 0, 0]
binaries:
  sender: bin/sender
  receiver: bin/receiver
output:
  pcap: logs/test.pcap
  csv: logs/test.csv
  summary: logs/summary.json
"""


class ConfigTests(unittest.TestCase):
    def make_root(self, config_text=CONFIG, extra_bin_files=None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "bin").mkdir()
        for name in ("sender", "receiver"):
            path = root / "bin" / name
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)
        for relative_path in extra_bin_files or []:
            path = root / "bin" / relative_path
            if path.parent.is_file():
                path.parent.unlink()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)
        config = root / "config.yml"
        config.write_text(config_text, encoding="utf-8")
        return tmp, root, config

    def assert_rejected(self, config_text, match=None):
        tmp, root, config = self.make_root(config_text=config_text)
        with tmp:
            context = self.assertRaisesRegex(ConfigError, match) if match else self.assertRaises(ConfigError)
            with context:
                load_config(config, root=root)

    def test_valid_unicast_config_defaults_and_paths(self):
        tmp, root, config = self.make_root()
        with tmp:
            loaded = load_config(config, root=root)

        self.assertEqual(loaded["protocol"]["mode"], "unicast")
        self.assertEqual(loaded["protocol"]["receivers"], ["gcs"])
        self.assertEqual(loaded["nodes"]["gcs"]["address"], "192.168.123.1")
        self.assertEqual(loaded["wireless"]["ssid"], "meshNet")
        self.assertEqual(loaded["wireless"]["broadcast_ip"], "192.168.123.255")
        self.assertEqual(loaded["binaries"]["sender"]["container"], "/opt/protocol/bin/sender")
        self.assertTrue(loaded["output"]["summary"].endswith(os.path.join("logs", "summary.json")))

    def test_valid_broadcast_config_with_multiple_receivers(self):
        config_text = CONFIG.replace("mode: unicast", "mode: broadcast", 1).replace(
            "receivers: [gcs]", "receivers: [gcs, drone2]", 1
        )
        tmp, root, config = self.make_root(config_text=config_text)
        with tmp:
            loaded = load_config(config, root=root)

        self.assertEqual(loaded["protocol"]["mode"], "broadcast")
        self.assertEqual(loaded["protocol"]["receivers"], ["gcs", "drone2"])

    def test_unicast_requires_exactly_one_receiver(self):
        self.assert_rejected(CONFIG.replace("receivers: [gcs]", "receivers: []", 1))
        self.assert_rejected(CONFIG.replace("receivers: [gcs]", "receivers: [gcs, drone2]", 1))

    def test_broadcast_requires_at_least_one_receiver(self):
        config_text = CONFIG.replace("mode: unicast", "mode: broadcast", 1).replace(
            "receivers: [gcs]", "receivers: []", 1
        )
        self.assert_rejected(config_text)

    def test_unknown_protocol_mode_is_rejected(self):
        self.assert_rejected(CONFIG.replace("mode: unicast", "mode: multicast", 1), "protocol.mode")

    def test_duplicate_receivers_are_rejected(self):
        self.assert_rejected(CONFIG.replace("receivers: [gcs]", "receivers: [gcs, gcs]", 1))

    def test_sender_cannot_also_be_receiver(self):
        self.assert_rejected(CONFIG.replace("receivers: [gcs]", "receivers: [drone1]", 1))

    def test_unknown_sender_or_receiver_is_rejected(self):
        self.assert_rejected(CONFIG.replace("sender: drone1", "sender: missing", 1))
        self.assert_rejected(CONFIG.replace("receivers: [gcs]", "receivers: [missing]", 1))

    def test_duplicate_node_ids_are_rejected(self):
        self.assert_rejected(CONFIG.replace("  - id: drone2", "  - id: gcs", 1), "duplicate node id")

    def test_duplicate_container_names_are_rejected(self):
        self.assert_rejected(CONFIG.replace("container_name: dr1", "container_name: gcs0", 1))

    def test_container_name_without_digit_is_rejected(self):
        # Mininet-WiFi derives node numbering with findall(r"\d+", name)[0],
        # so station names without digits fail internally with IndexError.
        self.assert_rejected(CONFIG.replace("container_name: gcs0", "container_name: gcs", 1))

    def test_container_name_long_interface_is_rejected(self):
        # Linux limits the generated "<container_name>-wlan0" to 15 chars.
        self.assert_rejected(CONFIG.replace("container_name: gcs0", "container_name: gcs0123456", 1))

    def test_duplicate_node_ip_is_rejected(self):
        self.assert_rejected(CONFIG.replace("ip: 192.168.123.2/24", "ip: 192.168.123.1/24", 1))

    def test_node_ip_outside_subnet_is_rejected(self):
        self.assert_rejected(CONFIG.replace("ip: 192.168.123.2/24", "ip: 192.168.124.2/24", 1))

    def test_node_cannot_use_network_or_broadcast_address(self):
        self.assert_rejected(CONFIG.replace("ip: 192.168.123.1/24", "ip: 192.168.123.0/24", 1))
        self.assert_rejected(CONFIG.replace("ip: 192.168.123.1/24", "ip: 192.168.123.255/24", 1))

    def test_inconsistent_broadcast_ip_is_rejected(self):
        self.assert_rejected(CONFIG.replace("broadcast_ip: 192.168.123.255", "broadcast_ip: 192.168.123.254", 1))

    def test_missing_broadcast_ip_is_derived_from_subnet(self):
        config_text = CONFIG.replace("  broadcast_ip: 192.168.123.255\n", "", 1)
        tmp, root, config = self.make_root(config_text=config_text)
        with tmp:
            loaded = load_config(config, root=root)

        self.assertEqual(loaded["wireless"]["broadcast_ip"], "192.168.123.255")

    def test_position_must_have_three_numbers(self):
        self.assert_rejected(CONFIG.replace("position: [0, 0, 0]", "position: [0, 0]", 1))
        self.assert_rejected(CONFIG.replace("position: [0, 0, 0]", "position: [0, nope, 0]", 1))

    def test_port_and_count_limits_are_enforced(self):
        self.assert_rejected(CONFIG.replace("port: 5000", "port: 0", 1))
        self.assert_rejected(CONFIG.replace("port: 5000", "port: 65536", 1))
        self.assert_rejected(CONFIG.replace("count: 3", "count: 0", 1))

    def test_invalid_ip_or_yaml_errors_are_config_errors(self):
        self.assert_rejected(
            CONFIG.replace("subnet: 192.168.123.0/24", "subnet: not-a-subnet", 1),
            "wireless.subnet is invalid",
        )
        self.assert_rejected(
            CONFIG.replace("subnet: 192.168.123.0/24", "subnet: 2001:db8::/64", 1),
            "wireless.subnet must be an IPv4 subnet",
        )
        self.assert_rejected(
            CONFIG.replace("ip: 192.168.123.1/24", "ip: not-an-ip", 1),
            r"nodes\[0\]\.ip is invalid",
        )
        tmp, root, config = self.make_root(config_text="experiment: [")
        with tmp:
            with self.assertRaisesRegex(ConfigError, "invalid YAML"):
                load_config(config, root=root)

    def test_rejects_missing_executable_binary(self):
        tmp, root, config = self.make_root()
        with tmp:
            (root / "bin" / "sender").chmod(0o644)
            with self.assertRaises(ConfigError):
                load_config(config, root=root)

    def test_executable_outside_bin_is_rejected(self):
        config_text = CONFIG.replace("sender: bin/sender", "sender: sender", 1)
        tmp, root, config = self.make_root(config_text=config_text)
        with tmp:
            (root / "sender").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "sender").chmod(0o755)
            with self.assertRaisesRegex(ConfigError, "must be located under"):
                load_config(config, root=root)

    def test_container_name_is_separate_from_logical_id(self):
        tmp, root, config = self.make_root()
        with tmp:
            loaded = load_config(config, root=root)

        self.assertEqual(loaded["nodes"]["gcs"]["id"], "gcs")
        self.assertEqual(loaded["nodes"]["gcs"]["container_name"], "gcs0")
        self.assertEqual(loaded["nodes"]["drone1"]["container_name"], "dr1")
        self.assertEqual(loaded["protocol"]["sender"], "drone1")
        self.assertEqual(loaded["protocol"]["receivers"], ["gcs"])

    def test_direct_bin_file_preserves_container_path(self):
        config_text = CONFIG.replace("bin/sender", "bin/sender.py", 1)
        tmp, root, config = self.make_root(
            config_text=config_text,
            extra_bin_files=["sender.py"],
        )
        with tmp:
            loaded = load_config(config, root=root)

        self.assertEqual(loaded["binaries"]["sender"]["container"], "/opt/protocol/bin/sender.py")

    def test_binary_in_bin_subdirectory_preserves_relative_path(self):
        config_text = CONFIG.replace("bin/receiver", "bin/receiver/receiver.py", 1)
        tmp, root, config = self.make_root(
            config_text=config_text,
            extra_bin_files=[os.path.join("receiver", "receiver.py")],
        )
        with tmp:
            loaded = load_config(config, root=root)

        self.assertEqual(
            loaded["binaries"]["receiver"]["container"],
            "/opt/protocol/bin/receiver/receiver.py",
        )


if __name__ == "__main__":
    unittest.main()
