"""TESTE AUTOMÁTICO (D14): contrato da semana 6 (D18), sem rede nem containers.

Verifica: bloco `commands` e marcadores no config, montagem do comando por
nó, variáveis TESTBED_*, pasta de trabalho, volumes de dados/saída e pastas
out-<nó>. Que o programa realmente recebe isso dentro do container só a
execução REAL no host confirma.
"""

import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts.testbed.config import ConfigError, expand_command, load_config
from scripts.testbed.network import container_popen, node_volumes
from scripts.testbed.rundir import prepare_run_directory
from scripts.testbed.runner import build_command, command_env, execute_protocol
from tests import test_config
from tests.test_runner import FakeNode, base_config

CONFIG = test_config.CONFIG


class CommandConfigTests(unittest.TestCase):
    make_root = test_config.ConfigTests.make_root
    assert_rejected = test_config.ConfigTests.assert_rejected

    def load(self, extra=""):
        tmp, root, config = self.make_root(CONFIG + extra)
        with tmp:
            return load_config(config, root=root)

    def test_without_commands_the_old_contract_is_kept(self):
        commands = self.load()["commands"]
        self.assertEqual(commands["sender"], "/opt/protocol/bin/sender --destination {destination} --port {port}")
        self.assertEqual(commands["receiver"], "/opt/protocol/bin/receiver --address {address} --port {port}")

    def test_custom_commands_with_every_placeholder(self):
        loaded = self.load(
            "commands:\n"
            "  sender: \"edhoc-initiator coap://{destination}:{port} --id {node_id} --mode {mode}"
            " --keys {data_dir}/key.pem --log '{output_dir}/my log.txt'\"\n"
            "  receiver: edhoc-responder --bind {address}\n"
        )
        self.assertTrue(loaded["commands"]["sender"].startswith("edhoc-initiator"))
        self.assertEqual(loaded["commands"]["receiver"], "edhoc-responder --bind {address}")

    def test_unknown_placeholder_empty_command_and_unknown_role_are_errors(self):
        self.assert_rejected(CONFIG + "commands:\n  sender: prog --to {adress}\n", r"unknown placeholder \{adress\}")
        self.assert_rejected(CONFIG + "commands:\n  sender: '   '\n", "commands.sender")
        self.assert_rejected(CONFIG + "commands:\n  relay: prog\n", "commands: unknown field 'relay'")
        self.assert_rejected(CONFIG + "commands:\n  sender: \"prog 'unclosed\"\n", "cannot split command")
        self.assert_rejected(CONFIG + "commands:\n  sender: prog {port\n", "invalid placeholder")

    def test_literal_braces_and_quotes(self):
        argv = expand_command("prog --json '{{\"port\": {port}}}' --name 'a b'", {"port": 5000})
        self.assertEqual(argv, ["prog", "--json", '{"port": 5000}', "--name", "a b"])


class CommandBuildTests(unittest.TestCase):
    def config(self, mode="unicast", receivers=None):
        config = base_config(mode, receivers)
        config["commands"] = {
            "sender": "init {destination}:{port} --me {address} --id {node_id} --mode {mode} --out {output_dir}",
            "receiver": "resp --bind {address} --peer {destination} --data {data_dir}",
        }
        return config

    def test_destination_is_the_receiver_in_unicast_and_broadcast_ip_in_broadcast(self):
        self.assertEqual(
            build_command(self.config(), "drone1", "sender", "192.168.123.1"),
            ["init", "192.168.123.1:5000", "--me", "192.168.123.2", "--id", "drone1", "--mode", "unicast",
             "--out", "/opt/protocol/out"],
        )
        self.assertEqual(
            build_command(self.config(), "gcs", "receiver", "192.168.123.1"),
            ["resp", "--bind", "192.168.123.1", "--peer", "192.168.123.1", "--data", "/opt/protocol/data"],
        )

    def test_execute_protocol_uses_the_templates_env_and_output_directory(self):
        config = self.config("broadcast", ["gcs", "drone2"])
        config["output"] = {"node_outputs": {"drone1": "/x/out-drone1", "gcs": "/x/out-gcs"}}
        nodes = {name: FakeNode(name) for name in ("drone1", "gcs", "drone2")}
        calls = []

        def fake_popen(node, command, **kwargs):
            calls.append((node.name, command, kwargs))
            return node.popen(command)

        with patch("scripts.testbed.runner.container_popen", side_effect=fake_popen), \
                patch("scripts.testbed.runner.time.sleep"):
            execute_protocol(config, nodes)

        by_node = {name: (command, kwargs) for name, command, kwargs in calls}
        self.assertEqual(by_node["drone1"][0][1], "192.168.123.255:5000")
        self.assertEqual(by_node["drone2"][0][:3], ["resp", "--bind", "192.168.123.3"])
        self.assertEqual(by_node["drone1"][1]["extra_env"]["TESTBED_ROLE"], "sender")
        self.assertEqual(by_node["gcs"][1]["extra_env"]["TESTBED_NODE_ID"], "gcs")
        self.assertEqual(by_node["gcs"][1]["cwd"], "/opt/protocol/out")
        # drone2 has no output directory in this config: it keeps the container's.
        self.assertIsNone(by_node["drone2"][1]["cwd"])

    def test_environment_variables(self):
        self.assertEqual(command_env(self.config(), "gcs", "receiver"), {
            "TESTBED_NODE_ID": "gcs",
            "TESTBED_ROLE": "receiver",
            "TESTBED_MODE": "unicast",
            "TESTBED_DATA_DIR": "/opt/protocol/data",
            "TESTBED_OUTPUT_DIR": "/opt/protocol/out",
        })


class ContainerSideTests(unittest.TestCase):
    def test_cwd_wraps_the_program_with_cd_and_exec_and_env_is_extended(self):
        node = types.SimpleNamespace(pid=11, dcinfo={"Config": {"Env": ["PATH=/usr/bin"]}})
        with patch("scripts.testbed.network.subprocess.Popen") as popen:
            container_popen(node, ["prog", "a b"], extra_env={"TESTBED_ROLE": "sender"}, cwd="/opt/protocol/out")
        args, kwargs = popen.call_args
        self.assertEqual(args[0][:10], ["nsenter", "-t", "11", "-m", "-u", "-i", "-n", "--root", "--wd", "--"])
        self.assertEqual(args[0][10:], ["/bin/sh", "-c", 'cd -- "$1" && shift && exec "$@"', "sh",
                                        "/opt/protocol/out", "prog", "a b"])
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin", "TESTBED_ROLE": "sender"})

    def test_volumes_data_only_when_present_and_output_only_for_participants(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "protocol-data"
            (data_root / "gcs").mkdir(parents=True)
            config = {"protocol_data_dir": str(data_root)}
            outputs = {"gcs": "/run/out-gcs", "drone1": "/run/out-drone1"}
            self.assertEqual(node_volumes(config, "gcs", "BIN", outputs), [
                "BIN", f"{data_root / 'gcs'}:/opt/protocol/data:ro", "/run/out-gcs:/opt/protocol/out:rw",
            ])
            self.assertEqual(node_volumes(config, "drone1", "BIN", outputs), ["BIN", "/run/out-drone1:/opt/protocol/out:rw"])
            self.assertEqual(node_volumes(config, "drone2", "BIN", outputs), ["BIN"])

    def test_run_directory_has_one_output_directory_per_participant(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {"experiment": {"id": "exp"}, "protocol": {"sender": "drone1", "receivers": ["gcs"]}}
            output = prepare_run_directory(config, tmp, now=datetime(2026, 11, 23, 15, 0, 0))
            self.assertEqual(sorted(output["node_outputs"]), ["drone1", "gcs"])
            for path in output["node_outputs"].values():
                self.assertTrue(Path(path).is_dir())
                self.assertTrue(Path(path).name.startswith("out-"))


if __name__ == "__main__":
    unittest.main()
