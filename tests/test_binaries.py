import contextlib
import importlib.util
import io
import os
import signal
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
    """Contract of the minimal replaceable models shipped under bin/.

    Only the operational contract is checked: accepted arguments, socket
    usage, signal handling and exit codes. The payload and the stdout
    messages are not part of the contract and are never asserted here.
    """

    def setUp(self):
        self.sender = load_binary("sender")
        self.receiver = load_binary("receiver")
        signal_handlers = {
            signal.SIGINT: signal.getsignal(signal.SIGINT),
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        }
        self.addCleanup(
            lambda: [signal.signal(sig, handler) for sig, handler in signal_handlers.items()]
        )

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

    def sender_argv(self, destination="127.0.0.1", port="5000"):
        return ["sender", "--destination", destination, "--port", port]

    def test_sender_rejects_invalid_port(self):
        code, stdout, stderr = self.run_main(self.sender, self.sender_argv(port="0"))

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Invalid port", stderr)

    def test_sender_rejects_invalid_destination_ipv4(self):
        with patch.object(self.sender.socket, "socket") as socket_factory:
            code, stdout, stderr = self.run_main(self.sender, self.sender_argv(destination="not-an-ip"))

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Invalid destination IPv4 address", stderr)
        socket_factory.assert_not_called()

    def test_sender_accepts_only_destination_and_port(self):
        fake = FakeSocket()
        with patch.object(self.sender.socket, "socket", return_value=fake):
            code, _stdout, stderr = self.run_main(self.sender, self.sender_argv(destination="192.168.123.1"))

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(fake.sent[0][1], ("192.168.123.1", 5000))

    def test_sender_allows_broadcast_destination(self):
        fake = FakeSocket()
        with patch.object(self.sender.socket, "socket", return_value=fake):
            code, _stdout, _stderr = self.run_main(self.sender, self.sender_argv(destination="192.168.123.255"))

        self.assertEqual(code, 0)
        self.assertIn(
            (self.sender.socket.SOL_SOCKET, self.sender.socket.SO_BROADCAST, 1),
            fake.options,
        )
        self.assertEqual(fake.sent[0][1], ("192.168.123.255", 5000))

    def test_sender_rejects_mode_argument(self):
        with patch.object(sys, "argv", self.sender_argv() + ["--mode", "broadcast"]), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as context:
                self.sender.main()

        self.assertEqual(context.exception.code, 2)

    def test_sender_rejects_count_argument(self):
        with patch.object(sys, "argv", self.sender_argv() + ["--count", "10"]), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as context:
                self.sender.main()

        self.assertEqual(context.exception.code, 2)

    def test_sender_sendto_failure_returns_operational_error(self):
        fake = FakeSocket()

        def fail_sendto(_payload, _address):
            raise OSError("send failed")

        fake.sendto = fail_sendto
        with patch.object(self.sender.socket, "socket", return_value=fake):
            code, stdout, stderr = self.run_main(self.sender, self.sender_argv())

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
        with patch.object(self.receiver.socket, "socket", return_value=fake), patch.object(self.receiver, "rx"):
            code, _stdout, stderr = self.run_main(self.receiver, ["receiver", "--address", "127.0.0.1", "--port", "5001"])

        self.assertEqual(code, 0)
        self.assertEqual(fake.bound, ("127.0.0.1", 5001))
        self.assertEqual(stderr, "")

    def test_receiver_main_reinitializes_stop_flag_before_rx(self):
        fake = FakeSocket()
        self.set_receiver_running(False)

        def assert_running(_sock):
            self.assertTrue(self.receiver.running)

        with patch.object(self.receiver.socket, "socket", return_value=fake), patch.object(
            self.receiver, "rx", side_effect=assert_running
        ):
            code, _stdout, stderr = self.run_main(
                self.receiver,
                ["receiver", "--address", "127.0.0.1", "--port", "5001"],
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")

    def test_receiver_stops_on_termination_signal(self):
        self.set_receiver_running(True)
        fake = FakeSocket()

        def recvfrom(_size):
            self.receiver.stop(None, None)
            raise socket.timeout()

        fake.recvfrom = recvfrom
        with contextlib.redirect_stdout(io.StringIO()):
            self.receiver.rx(fake)

        self.assertEqual(fake.timeout, 0.5)
        self.assertFalse(self.receiver.running)

    def test_receiver_stop_flag_allows_controlled_exit_without_traffic(self):
        self.set_receiver_running(True)
        self.receiver.stop(None, None)
        fake = FakeSocket()
        fake.recvfrom = Mock()

        with contextlib.redirect_stdout(io.StringIO()):
            self.receiver.rx(fake)

        fake.recvfrom.assert_not_called()

    def test_receiver_registers_sigterm_and_sigint_handlers(self):
        fake = FakeSocket()
        with patch.object(self.receiver.socket, "socket", return_value=fake), patch.object(
            self.receiver, "rx"
        ), patch.object(self.receiver.signal, "signal") as signal_mock:
            code, _stdout, _stderr = self.run_main(
                self.receiver,
                ["receiver", "--address", "127.0.0.1", "--port", "5000"],
            )

        self.assertEqual(code, 0)
        registered = {call.args[0] for call in signal_mock.call_args_list}
        self.assertEqual(registered, {signal.SIGINT, signal.SIGTERM})

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
            code, _stdout, stderr = self.run_main(self.receiver, ["receiver", "--address", "127.0.0.1", "--port", "5000"])

        self.assertEqual(code, 1)
        self.assertIn("Receiver error: recv failed", stderr)

    def test_binaries_remain_executable(self):
        for name in ("sender", "receiver"):
            with self.subTest(name=name):
                mode = os.stat(ROOT / "bin" / name).st_mode
                self.assertTrue(mode & stat.S_IXUSR)
                self.assertTrue(os.access(ROOT / "bin" / name, os.X_OK))


if __name__ == "__main__":
    unittest.main()
