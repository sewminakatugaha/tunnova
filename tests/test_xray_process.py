import json
import os
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from tunnova.profiles import parse_vless_uri
from tunnova.xray_config import build_xray_config
from tunnova.xray_process import (
    XrayProcessError,
    XrayProcessManager,
    XrayProcessState,
)


SYNTHETIC_UUID = "11111111-2222-4333-8444-555555555555"


def make_config():
    profile = parse_vless_uri(
        f"vless://{SYNTHETIC_UUID}@vpn.example.com:443"
        "?type=ws&security=tls&sni=certificate.example.com&path=%2Fedge"
    )
    return build_xray_config(profile)


class FakeProcess:
    def __init__(self):
        self.stdin = unittest.mock.Mock()
        self.pid = 987654
        self.returncode = None
        self.terminate_called = False
        self.kill_called = False
        self.wait_calls = 0
        self.config_data = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_called = True

    def kill(self):
        self.kill_called = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls == 1 and self.terminate_called and not self.kill_called:
            self.returncode = 0
            return 0
        return self.returncode


class XrayProcessManagerTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.manager = XrayProcessManager("/configured/path/xray")
        self.process = FakeProcess()
        self.port_available_patcher = patch.object(
            XrayProcessManager, "_port_available", return_value=True
        )
        self.port_available_patcher.start()
        self.addCleanup(self.port_available_patcher.stop)
        self.write_configuration_patcher = patch.object(
            XrayProcessManager,
            "_write_configuration",
            autospec=True,
            side_effect=self.capture_configuration,
        )
        self.write_configuration_patcher.start()
        self.addCleanup(self.write_configuration_patcher.stop)
        self.enterContext(
            patch("tunnova.xray_process.os.killpg", side_effect=self.signal_group)
        )
        self.enterContext(
            patch.object(
                XrayProcessManager,
                "_group_exists",
                autospec=True,
                side_effect=self.group_exists,
            )
        )

    def capture_configuration(self, _manager, process, data, _deadline):
        process.config_data = data

    def signal_group(self, _pid, signal_number):
        if signal_number == 15:
            self.process.terminate()
        elif signal_number == 9:
            self.process.kill()

    def group_exists(self, _manager, _pid):
        return self.process.returncode is None

    @patch("tunnova.xray_process.XrayProcessManager._listener_ready", return_value=True)
    @patch("tunnova.xray_process.subprocess.Popen")
    def test_start_uses_argument_list_and_stdin_only(self, popen, _ready):
        popen.return_value = self.process

        self.manager.start(self.config)

        args, kwargs = popen.call_args
        self.assertEqual(
            args[0],
            ["/configured/path/xray", "run", "-format", "json", "-config", "stdin:"],
        )
        self.assertEqual(kwargs["stdin"], subprocess.PIPE)
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["close_fds"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(json.loads(self.process.config_data), self.config)
        self.assertEqual(self.manager.state, XrayProcessState.RUNNING)
        self.assertTrue(self.manager.is_running())

    @patch("tunnova.xray_process.XrayProcessManager._listener_ready", return_value=True)
    @patch("tunnova.xray_process.subprocess.Popen")
    def test_prevents_duplicate_processes(self, popen, _ready):
        popen.return_value = self.process
        self.manager.start(self.config)

        with self.assertRaises(XrayProcessError):
            self.manager.start(self.config)

        popen.assert_called_once()

    @patch("tunnova.xray_process.subprocess.Popen", side_effect=FileNotFoundError("secret output"))
    def test_missing_executable_has_sanitized_error(self, _popen):
        with self.assertRaises(XrayProcessError) as raised:
            self.manager.start(self.config)

        self.assertNotIn(SYNTHETIC_UUID, str(raised.exception))
        self.assertNotIn("secret output", str(raised.exception))
        self.assertEqual(self.manager.state, XrayProcessState.FAILED)

    @patch("tunnova.xray_process.XrayProcessManager._listener_ready", return_value=False)
    @patch("tunnova.xray_process.subprocess.Popen")
    def test_startup_timeout_cleans_up_process(self, popen, _ready):
        self.manager = XrayProcessManager(
            "/configured/path/xray", startup_timeout=0.01, readiness_interval=0.001
        )
        popen.return_value = self.process

        with self.assertRaisesRegex(XrayProcessError, "startup timeout"):
            self.manager.start(self.config)

        self.assertTrue(self.process.terminate_called)
        self.assertEqual(self.manager.state, XrayProcessState.FAILED)
        self.assertFalse(self.manager.is_running())

    @patch("tunnova.xray_process.subprocess.Popen")
    def test_rejects_an_already_occupied_local_port_before_launch(self, popen):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        self.config["inbounds"][0]["port"] = listener.getsockname()[1]
        self.port_available_patcher.stop()
        try:
            with self.assertRaisesRegex(XrayProcessError, "already in use"):
                self.manager.start(self.config)
        finally:
            listener.close()

        popen.assert_not_called()

    @patch("tunnova.xray_process.XrayProcessManager._listener_ready", return_value=False)
    @patch("tunnova.xray_process.subprocess.Popen")
    def test_unexpected_startup_exit_is_reported(self, popen, _ready):
        self.process.returncode = 2
        popen.return_value = self.process

        with self.assertRaisesRegex(XrayProcessError, "exited before"):
            self.manager.start(self.config)

        self.assertEqual(self.manager.state, XrayProcessState.FAILED)

    @patch("tunnova.xray_process.XrayProcessManager._listener_ready")
    @patch("tunnova.xray_process.subprocess.Popen")
    def test_child_exit_during_readiness_is_not_accepted(self, popen, ready):
        popen.return_value = self.process

        def exit_during_probe(_port):
            self.process.returncode = 1
            return True

        ready.side_effect = exit_during_probe
        with self.assertRaisesRegex(XrayProcessError, "during local SOCKS readiness"):
            self.manager.start(self.config)

        self.assertEqual(self.manager.state, XrayProcessState.FAILED)

    @patch("tunnova.xray_process.XrayProcessManager._listener_ready", return_value=True)
    @patch("tunnova.xray_process.subprocess.Popen")
    def test_is_running_detects_unexpected_exit(self, popen, _ready):
        popen.return_value = self.process
        self.manager.start(self.config)
        self.process.returncode = 1

        self.assertFalse(self.manager.is_running())
        self.assertEqual(self.manager.state, XrayProcessState.FAILED)

    @patch("tunnova.xray_process.XrayProcessManager._listener_ready", return_value=True)
    @patch("tunnova.xray_process.subprocess.Popen")
    def test_stop_terminates_gracefully(self, popen, _ready):
        popen.return_value = self.process
        self.manager.start(self.config)

        self.manager.stop()

        self.assertTrue(self.process.terminate_called)
        self.assertFalse(self.process.kill_called)
        self.assertEqual(self.manager.state, XrayProcessState.STOPPED)

    @patch("tunnova.xray_process.XrayProcessManager._listener_ready", return_value=True)
    @patch("tunnova.xray_process.subprocess.Popen")
    def test_stop_kills_only_after_graceful_timeout(self, popen, _ready):
        self.manager = XrayProcessManager(
            "/configured/path/xray", shutdown_timeout=0.01
        )
        self.process.wait = unittest.mock.Mock(
            side_effect=[subprocess.TimeoutExpired("xray", 1), self.finish_after_kill]
        )
        popen.return_value = self.process
        self.manager.start(self.config)

        self.manager.stop()

        self.assertTrue(self.process.terminate_called)
        self.assertTrue(self.process.kill_called)
        self.assertEqual(self.manager.state, XrayProcessState.STOPPED)

    def finish_after_kill(self, *_args, **_kwargs):
        self.process.returncode = -9
        return -9

    @patch("tunnova.xray_process.subprocess.Popen")
    def test_start_during_shutdown_does_not_launch_another_child(self, popen):
        popen.return_value = self.process
        with patch.object(XrayProcessManager, "_listener_ready", return_value=True):
            self.manager.start(self.config)

        cleanup_entered = threading.Event()
        allow_cleanup = threading.Event()

        def delayed_cleanup(_process):
            cleanup_entered.set()
            allow_cleanup.wait(1)
            self.process.returncode = 0
            return True

        errors = []
        with patch.object(self.manager, "_terminate_process", side_effect=delayed_cleanup):
            thread = threading.Thread(target=lambda: self._capture_error(self.manager.stop, errors))
            thread.start()
            self.assertTrue(cleanup_entered.wait(1))
            with self.assertRaisesRegex(XrayProcessError, "starting, running, or stopping"):
                self.manager.start(self.config)
            allow_cleanup.set()
            thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        popen.assert_called_once()
        self.assertEqual(self.manager.state, XrayProcessState.STOPPED)

    @patch("tunnova.xray_process.subprocess.Popen")
    def test_concurrent_startup_and_shutdown_clean_up_once(self, popen):
        popen.return_value = self.process
        writer_entered = threading.Event()
        allow_writer = threading.Event()
        cleanup_entered = threading.Event()
        allow_cleanup = threading.Event()
        start_errors = []

        def stalled_writer(_manager, _process, _data, _deadline):
            writer_entered.set()
            allow_writer.wait(1)

        def delayed_cleanup(_process):
            cleanup_entered.set()
            allow_cleanup.wait(1)
            self.process.returncode = 0
            return True

        self.write_configuration_patcher.stop()
        with patch.object(XrayProcessManager, "_write_configuration", autospec=True, side_effect=stalled_writer), patch.object(self.manager, "_terminate_process", side_effect=delayed_cleanup):
            starting = threading.Thread(
                target=lambda: self._capture_error(lambda: self.manager.start(self.config), start_errors)
            )
            starting.start()
            self.assertTrue(writer_entered.wait(1))
            stopping = threading.Thread(target=self.manager.stop)
            stopping.start()
            self.assertTrue(cleanup_entered.wait(1))
            allow_writer.set()
            starting.join(1)
            allow_cleanup.set()
            stopping.join(1)

        self.assertFalse(starting.is_alive())
        self.assertFalse(stopping.is_alive())
        self.assertEqual(len(start_errors), 1)
        self.assertIsInstance(start_errors[0], XrayProcessError)
        self.assertEqual(self.manager.state, XrayProcessState.STOPPED)

    @staticmethod
    def _capture_error(callback, errors):
        try:
            callback()
        except Exception as error:
            errors.append(error)

    @patch("tunnova.xray_process.subprocess.Popen")
    def test_cleanup_after_configuration_write_exception_is_sanitized(self, popen):
        popen.return_value = self.process
        secret_error = RuntimeError(SYNTHETIC_UUID)
        self.write_configuration_patcher.stop()
        with patch.object(
            XrayProcessManager,
            "_write_configuration",
            autospec=True,
            side_effect=secret_error,
        ), patch.object(XrayProcessManager, "_listener_ready", return_value=True):
            with self.assertRaises(XrayProcessError) as raised:
                self.manager.start(self.config)

        self.assertNotIn(SYNTHETIC_UUID, str(raised.exception))
        self.assertTrue(self.process.terminate_called)
        self.assertEqual(self.manager.state, XrayProcessState.FAILED)

    def test_stalled_configuration_write_is_bounded(self):
        read_descriptor, write_descriptor = os.pipe()
        os.set_blocking(write_descriptor, False)
        while True:
            try:
                os.write(write_descriptor, b"x" * 65536)
            except BlockingIOError:
                break
        pipe = os.fdopen(write_descriptor, "wb", buffering=0)
        process = unittest.mock.Mock(stdin=pipe, poll=unittest.mock.Mock(return_value=None))
        manager = XrayProcessManager("/configured/path/xray", startup_timeout=0.02)
        manager._process = process
        manager._state = XrayProcessState.STARTING
        self.write_configuration_patcher.stop()
        try:
            with self.assertRaisesRegex(XrayProcessError, "input timed out"):
                manager._write_configuration(
                    process, b"x" * (1024 * 1024), time.monotonic() + 0.02
                )
        finally:
            os.close(read_descriptor)

    def test_readiness_probe_uses_only_loopback(self):
        connection = unittest.mock.MagicMock()
        with patch("tunnova.xray_process.socket.create_connection", return_value=connection) as connect:
            self.assertTrue(XrayProcessManager._listener_ready(10890))

        connect.assert_called_once_with(("127.0.0.1", 10890), timeout=0.1)
        connection.__enter__.assert_called_once()
        connection.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()