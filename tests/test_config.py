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
    ip: 192.168.123.1/24
    position: [0, 0, 0]
  - id: drone1
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
    def make_root(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "bin").mkdir()
        for name in ("sender", "receiver"):
            path = root / "bin" / name
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)
        config = root / "config.yml"
        config.write_text(CONFIG, encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
