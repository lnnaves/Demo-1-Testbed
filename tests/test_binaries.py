import contextlib
import importlib.util
import io
import itertools
import os
import socket
import stat
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_binary(name):
    path = ROOT / "bin" / name
    loader = SourceFileLoader(f"test_{name}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSocket:
    def __init__(self):
        self.bound = None
        self.options = []
        self.sent = []
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def bind(self, address):
        self.bound = address

    def setsockopt(self, *args):
        self.options.append(args)

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendto(self, payload, address):
        self.sent.append((payload, address))
        return len(payload)

    def recvfrom(self, _size):
        raise socket.timeout()


class BinaryTests(unittest.TestCase):
    def setUp(self):
        self.sender = load_binary("sender")
        self.receiver = load_binary("receiver")

    def run_main(self, module, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = module.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def set_receiver_running(self, value):
        original = self.receiver.running
        self.receiver.running = value
        self.addCleanup(setattr, self.receiver, "running", original)

    def prepare_rx_running_state(self):
        self.set_receiver_running(True)
        self.addCleanup(setattr, self.receiver, "running", True)

    def test_sender_rejects_invalid_port(self):
        code, stdout, stderr = self.run_main(
            self.sender,
            ["sender", "--mode", "unicast", "--destination", "127.0.0.1", "--port", "0", "--count", "1"],
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Invalid port", stderr)

    def test_sender_rejects_non_positive_count(self):
        code, stdout, stderr = self.run_main(
            self.sender,
            ["sender", "--mode", "unicast", "--destination", "127.0.0.1", "--port", "5000", "--count", "0"],
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Count must be greater than zero", stderr)

    def test_sender_rejects_invalid_destination_ipv4(self):
        with patch.object(self.sender.socket, "socket") as socket_factory:
            code, stdout, stderr = self.run_main(
                self.sender,
                [
                    "sender",
                    "--mode",
                    "unicast",
                    "--destination",
                    "not-an-ip",
                    "--port",
                    "5000",
                    "--count",
                    "1",
                ],
            )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Invalid destination IPv4 address", stderr)
        socket_factory.assert_not_called()

    def test_sender_enables_broadcast_only_in_broadcast_mode(self):
        for mode, expect_broadcast in (("unicast", False), ("broadcast", True)):
            fake = FakeSocket()
            with self.subTest(mode=mode), patch.object(self.sender.socket, "socket", return_value=fake):
                code, stdout, stderr = self.run_main(
                    self.sender,
                    [
                        "sender",
                        "--mode",
                        mode,
                        "--destination",
                        "127.0.0.1",
                        "--port",
                        "5000",
                        "--count",
                        "1",
                    ],
                )

                self.assertEqual(code, 0)
                self.assertIn("Transmission completed", stdout)
                self.assertEqual(stderr, "")
                broadcast_options = [
                    option
                    for option in fake.options
                    if option == (self.sender.socket.SOL_SOCKET, self.sender.socket.SO_BROADCAST, 1)
                ]
                self.assertEqual(bool(broadcast_options), expect_broadcast)

    def test_tx_sends_exactly_count_messages_with_sequence_and_timestamp(self):
        fake = FakeSocket()

        with patch.object(self.sender.time, "time_ns", side_effect=itertools.count(100)), patch.object(
            self.sender.time, "sleep", return_value=None
        ), contextlib.redirect_stdout(io.StringIO()):
            sent = self.sender.tx(fake, "127.0.0.1", 5000, 3)

        self.assertEqual(sent, 3)
        self.assertEqual(len(fake.sent), 3)
        for index, (payload, address) in enumerate(fake.sent, start=1):
            self.assertEqual(address, ("127.0.0.1", 5000))
            text = payload.decode("utf-8")
            self.assertIn(f"sequence={index}", text)
            timestamp = text.split("timestamp_ns=", 1)[1]
            self.assertEqual(timestamp, str(99 + index))

    def test_sender_sendto_failure_returns_operational_error(self):
        fake = FakeSocket()

        def fail_sendto(_payload, _address):
            raise OSError("send failed")

        fake.sendto = fail_sendto
        with patch.object(self.sender.socket, "socket", return_value=fake):
            code, stdout, stderr = self.run_main(
                self.sender,
                ["sender", "--mode", "unicast", "--destination", "127.0.0.1", "--port", "5000", "--count", "1"],
            )

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Sender error: send failed", stderr)

    def test_receiver_rejects_invalid_port(self):
        code, stdout, stderr = self.run_main(self.receiver, ["receiver", "--address", "127.0.0.1", "--port", "65536"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Invalid port", stderr)

    def test_receiver_rejects_invalid_ipv4_address(self):
        with patch.object(self.receiver.socket, "socket") as socket_factory:
            code, stdout, stderr = self.run_main(self.receiver, ["receiver", "--address", "2001:db8::1", "--port", "5000"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Invalid local IPv4 address", stderr)
        socket_factory.assert_not_called()

    def test_receiver_binds_to_requested_address_and_port(self):
        fake = FakeSocket()
        with patch.object(self.receiver.socket, "socket", return_value=fake), patch.object(self.receiver, "rx", return_value=0):
            code, stdout, stderr = self.run_main(self.receiver, ["receiver", "--address", "127.0.0.1", "--port", "5001"])

        self.assertEqual(code, 0)
        self.assertEqual(fake.bound, ("127.0.0.1", 5001))
        self.assertIn("Receiver listening on 127.0.0.1:5001", stdout)
        self.assertIn("Receiver stopped after 0 packets", stdout)
        self.assertEqual(stderr, "")

    def test_receiver_main_reinitializes_stop_flag_before_rx(self):
        fake = FakeSocket()
        self.set_receiver_running(False)

        def assert_running(_sock):
            self.assertTrue(self.receiver.running)
            return 0

        with patch.object(self.receiver.socket, "socket", return_value=fake), patch.object(
            self.receiver, "rx", side_effect=assert_running
        ):
            code, _stdout, stderr = self.run_main(
                self.receiver,
                ["receiver", "--address", "127.0.0.1", "--port", "5001"],
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")

    def test_rx_counts_only_datagrams_received(self):
        self.prepare_rx_running_state()
        fake = FakeSocket()
        messages = [(b"one", ("127.0.0.1", 1000)), (b"two", ("127.0.0.1", 1000))]

        def recvfrom(_size):
            if messages:
                return messages.pop(0)
            self.receiver.stop(None, None)
            raise socket.timeout()

        fake.recvfrom = recvfrom
        with contextlib.redirect_stdout(io.StringIO()):
            received = self.receiver.rx(fake)

        self.assertEqual(received, 2)
        self.assertEqual(fake.timeout, 0.5)

    def test_rx_timeout_does_not_increment_count(self):
        self.prepare_rx_running_state()
        fake = FakeSocket()

        def recvfrom(_size):
            self.receiver.stop(None, None)
            raise socket.timeout()

        fake.recvfrom = recvfrom
        with contextlib.redirect_stdout(io.StringIO()):
            received = self.receiver.rx(fake)

        self.assertEqual(received, 0)

    def test_receiver_stop_flag_allows_controlled_exit_without_traffic(self):
        self.prepare_rx_running_state()
        self.receiver.stop(None, None)
        fake = FakeSocket()
        fake.recvfrom = Mock()

        with contextlib.redirect_stdout(io.StringIO()):
            received = self.receiver.rx(fake)

        self.assertEqual(received, 0)
        fake.recvfrom.assert_not_called()

    def test_receiver_bind_failure_returns_operational_error(self):
        fake = FakeSocket()

        def fail_bind(_address):
            raise OSError("bind failed")

        fake.bind = fail_bind
        with patch.object(self.receiver.socket, "socket", return_value=fake):
            code, stdout, stderr = self.run_main(self.receiver, ["receiver", "--address", "127.0.0.1", "--port", "5000"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Receiver error: bind failed", stderr)

    def test_receiver_recv_failure_returns_operational_error(self):
        fake = FakeSocket()
        with patch.object(self.receiver.socket, "socket", return_value=fake), patch.object(
            self.receiver, "rx", side_effect=OSError("recv failed")
        ):
            code, stdout, stderr = self.run_main(self.receiver, ["receiver", "--address", "127.0.0.1", "--port", "5000"])

        self.assertEqual(code, 1)
        self.assertIn("Receiver listening on 127.0.0.1:5000", stdout)
        self.assertIn("Receiver error: recv failed", stderr)

    def test_binaries_remain_executable(self):
        for name in ("sender", "receiver"):
            with self.subTest(name=name):
                mode = os.stat(ROOT / "bin" / name).st_mode
                self.assertTrue(mode & stat.S_IXUSR)
                self.assertTrue(os.access(ROOT / "bin" / name, os.X_OK))


if __name__ == "__main__":
    unittest.main()
