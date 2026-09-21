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

    def test_load_config_defaults_and_paths(self):
        tmp, root, config = self.make_root()
        with tmp:
            loaded = load_config(config, root=root)

        self.assertEqual(loaded["protocol"]["mode"], "unicast")
        self.assertEqual(loaded["nodes"]["gcs"]["address"], "192.168.123.1")
        self.assertEqual(loaded["wireless"]["ssid"], "meshNet")
        self.assertEqual(loaded["binaries"]["sender"]["container"], "/opt/protocol/bin/sender")
        self.assertTrue(loaded["output"]["summary"].endswith(os.path.join("logs", "summary.json")))

    def test_rejects_missing_executable_binary(self):
        tmp, root, config = self.make_root()
        with tmp:
            (root / "bin" / "sender").chmod(0o644)
            with self.assertRaises(ConfigError):
                load_config(config, root=root)

    def test_container_name_defaults_to_id_and_preserves_logical_id(self):
        tmp, root, config = self.make_root()
        with tmp:
            loaded = load_config(config, root=root)

        # The logical id ("gcs") remains the key used by protocol.sender,
        # protocol.receivers and results, while container_name ("gcs0") is
        # only used as the Containernet/Mininet-WiFi station name.
        self.assertEqual(loaded["nodes"]["gcs"]["id"], "gcs")
        self.assertEqual(loaded["nodes"]["gcs"]["container_name"], "gcs0")
        self.assertEqual(loaded["nodes"]["drone1"]["container_name"], "dr1")
        self.assertEqual(loaded["protocol"]["sender"], "drone1")
        self.assertEqual(loaded["protocol"]["receivers"], ["gcs"])

    def test_container_name_without_digit_is_rejected(self):
        # Mininet-WiFi/BATMAN internally runs
        # findall(r'\d+', intf.node.name)[0], which raises IndexError for a
        # station name without any digit (e.g. "gcs").
        config_text = CONFIG.replace("container_name: gcs0", "container_name: gcs")
        tmp, root, config = self.make_root(config_text=config_text)
        with tmp:
            with self.assertRaises(ConfigError):
                load_config(config, root=root)

    def test_duplicate_container_name_is_rejected(self):
        config_text = CONFIG.replace("container_name: dr1", "container_name: gcs0")
        tmp, root, config = self.make_root(config_text=config_text)
        with tmp:
            with self.assertRaises(ConfigError):
                load_config(config, root=root)

    def test_container_name_exceeding_interface_limit_is_rejected(self):
        # "<name>-wlan0" must fit Linux's 15 character interface name limit.
        config_text = CONFIG.replace("container_name: gcs0", "container_name: gcs0123456")
        tmp, root, config = self.make_root(config_text=config_text)
        with tmp:
            with self.assertRaises(ConfigError):
                load_config(config, root=root)

    def test_direct_bin_file_preserves_container_path(self):
        config_text = CONFIG.replace("bin/sender", "bin/sender.py")
        tmp, root, config = self.make_root(
            config_text=config_text,
            extra_bin_files=["sender.py"],
        )
        with tmp:
            loaded = load_config(config, root=root)

        self.assertEqual(loaded["binaries"]["sender"]["container"], "/opt/protocol/bin/sender.py")

    def test_binary_in_bin_subdirectory_preserves_relative_path(self):
        config_text = CONFIG.replace("bin/receiver", "bin/receiver/receiver.py")
        tmp, root, config = self.make_root(
            config_text=config_text,
            extra_bin_files=[os.path.join("receiver", "receiver.py")],
        )
        with tmp:
            loaded = load_config(config, root=root)

        # The whole bin/ directory is mounted at /opt/protocol/bin, so the
        # relative path under bin/ must be preserved in the container path.
        self.assertEqual(
            loaded["binaries"]["receiver"]["container"],
            "/opt/protocol/bin/receiver/receiver.py",
        )


if __name__ == "__main__":
    unittest.main()
