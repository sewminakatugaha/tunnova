import importlib
import sys
import types
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch

from tunnova.connection import ConnectionControllerError, ConnectionState
from tunnova.profiles import ProfileManager


_idle_callbacks = []


class _FakeApplication:
    def __init__(self, **_kwargs):
        pass


def _idle_add(callback, *args):
    _idle_callbacks.append((callback, args))
    return len(_idle_callbacks)


_gi = types.ModuleType("gi")
_gi.require_version = lambda *_args: None
_repository = types.ModuleType("gi.repository")
_repository.Gtk = types.SimpleNamespace(
    Application=_FakeApplication,
    ListBoxRow=object,
)
_repository.GLib = types.SimpleNamespace(idle_add=_idle_add)
_gi.repository = _repository

with patch.dict(sys.modules, {"gi": _gi, "gi.repository": _repository}):
    gui = importlib.import_module("tunnova.gui")


def _run_idle_callbacks():
    while _idle_callbacks:
        callback, args = _idle_callbacks.pop(0)
        callback(*args)


class _Widget:
    def __init__(self, text=""):
        self.text = text
        self.sensitive = True
        self.closed = False
        self.selected_row = object()
        self.close_handler = None

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive

    def get_selected_row(self):
        return self.selected_row

    def get_row_at_index(self, _index):
        return self.selected_row

    def select_row(self, row):
        self.selected_row = row

    def close(self):
        if self.close_handler is None or not self.close_handler(self):
            self.closed = True


class _MockConnection:
    def __init__(self, *_args, **_kwargs):
        self.state = ConnectionState.DISCONNECTED
        self.last_error = None
        self.listeners = []
        self.connect_future = Future()
        self.disconnect_future = Future()
        self.shutdown_future = Future()

    def add_state_listener(self, listener):
        self.listeners.append(listener)

    def remove_state_listener(self, listener):
        self.listeners.remove(listener)

    def notify(self, state):
        self.state = state
        for listener in self.listeners:
            listener(state)

    def connect(self):
        self.notify(ConnectionState.CONNECTING)
        return self.connect_future

    def disconnect(self):
        self.notify(ConnectionState.DISCONNECTING)
        return self.disconnect_future

    def shutdown(self):
        if self.state != ConnectionState.DISCONNECTED:
            self.notify(ConnectionState.DISCONNECTING)
        return self.shutdown_future


class GuiConnectionTests(unittest.TestCase):
    def setUp(self):
        _idle_callbacks.clear()
        manager = ProfileManager()
        manager.import_link(
            "vless://11111111-2222-4333-8444-555555555555"
            "@gateway.example.com:443?type=ws&security=tls"
            "&sni=original-sni.example&host=original-ws.example#Synthetic"
        )
        manager.select(0)

        self.app = gui.VpnApplication.__new__(gui.VpnApplication)
        self.app.profile_manager = manager
        self.app._controller = None
        self.app._controller_state_listener = None
        self.app._closing = False
        self.app._close_allowed = False
        self.app.window = _Widget()
        self.app.window.close_handler = self.app.on_close_request
        self.app.status = _Widget("Status: Disconnected")
        self.app.feedback = _Widget()
        self.app.entry = _Widget()
        self.app.import_button = _Widget()
        self.app.profile_list = _Widget()
        self.app.remove_button = _Widget()
        self.app.xray_path_entry = _Widget("/synthetic/xray")
        self.app.tls_verify_name_entry = _Widget("trusted-certificate.example")
        self.app.connect_button = _Widget()
        self.app.disconnect_button = _Widget()
        self.app._controller_factory = Mock()
        self.controller = _MockConnection()
        self.app._controller_factory.return_value = self.controller

    def test_connect_uses_explicit_tls_name_and_marshals_states(self):
        with (
            patch.object(gui.os.path, "isfile", return_value=True),
            patch.object(gui.os, "access", return_value=True),
        ):
            self.app._update_controls()
            self.assertTrue(self.app.connect_button.sensitive)
            self.app.on_connect(None)

        self.app._controller_factory.assert_called_once_with(
            self.app.profile_manager.selected_profile,
            "/synthetic/xray",
            tls_verify_name="trusted-certificate.example",
        )
        profile = self.app._controller_factory.call_args.args[0]
        self.assertEqual(profile.server_name, "original-sni.example")
        self.assertEqual(profile.ws_host, "original-ws.example")
        self.assertFalse(self.app.profile_list.sensitive)
        self.assertEqual(self.app.status.text, "Status: Disconnected")
        _run_idle_callbacks()
        self.assertEqual(self.app.status.text, "Status: Connecting")

        self.controller.notify(ConnectionState.PROXY_READY)
        _run_idle_callbacks()
        self.assertEqual(
            self.app.status.text,
            "Status: Local SOCKS5 proxy ready at 127.0.0.1:10890",
        )

    def test_blank_tls_verification_name_is_not_inferred(self):
        self.app.tls_verify_name_entry.text = ""
        with (
            patch.object(gui.os.path, "isfile", return_value=True),
            patch.object(gui.os, "access", return_value=True),
        ):
            self.app.on_connect(None)

        self.app._controller_factory.assert_called_once_with(
            self.app.profile_manager.selected_profile,
            "/synthetic/xray",
            tls_verify_name=None,
        )

    def test_connect_is_disabled_without_selection_or_available_executable(self):
        with (
            patch.object(gui.os.path, "isfile", return_value=False),
            patch.object(gui.os, "access", return_value=False),
        ):
            self.app._update_controls()
        self.assertFalse(self.app.connect_button.sensitive)

        self.app.profile_manager.clear_selection()
        with (
            patch.object(gui.os.path, "isfile", return_value=True),
            patch.object(gui.os, "access", return_value=True),
        ):
            self.app._update_controls()
        self.assertFalse(self.app.connect_button.sensitive)

    def test_active_connection_blocks_profile_selection_and_removal(self):
        self.app._controller = self.controller
        self.controller.state = ConnectionState.PROXY_READY
        before = self.app.profile_manager.selected_profile

        self.app.on_profile_selected(None, None)
        self.app.on_remove(None)

        self.assertIs(self.app.profile_manager.selected_profile, before)
        self.assertEqual(len(self.app.profile_manager.profiles), 1)
        self.assertIsNotNone(self.app.profile_list.selected_row)

    def test_disconnect_completion_before_final_state_notification_shows_disconnected(self):
        self.app._controller = self.controller
        self.controller.state = ConnectionState.PROXY_READY
        listener = lambda state: gui.GLib.idle_add(
            self.app._apply_connection_state, self.controller, state
        )
        self.controller.add_state_listener(listener)
        self.app._controller_state_listener = listener

        self.app.on_disconnect(None)
        _run_idle_callbacks()
        self.assertEqual(self.app.status.text, "Status: Disconnecting")

        self.controller.state = ConnectionState.DISCONNECTED
        self.controller.disconnect_future.set_result(ConnectionState.DISCONNECTED)
        self.controller.notify(ConnectionState.DISCONNECTED)
        self.assertEqual(
            [callback.__name__ for callback, _args in _idle_callbacks],
            ["_finish_operation", "_apply_connection_state"],
        )

        _run_idle_callbacks()

        self.assertEqual(self.app.status.text, "Status: Disconnected")
        self.assertIsNone(self.app._controller)

    def test_stale_state_notification_does_not_override_current_state(self):
        self.app._controller = self.controller
        self.controller.state = ConnectionState.PROXY_READY

        self.app._apply_connection_state(
            self.controller, ConnectionState.DISCONNECTING
        )

        self.assertEqual(
            self.app.status.text,
            "Status: Local SOCKS5 proxy ready at 127.0.0.1:10890",
        )

    def test_window_waits_for_shutdown_and_remains_open_on_cleanup_failure(self):
        self.app._controller = self.controller
        self.controller.state = ConnectionState.PROXY_READY
        listener = lambda state: gui.GLib.idle_add(
            self.app._apply_connection_state, self.controller, state
        )
        self.controller.add_state_listener(listener)
        self.app._controller_state_listener = listener

        self.assertTrue(self.app.on_close_request(self.app.window))
        self.assertFalse(self.app.window.closed)
        _run_idle_callbacks()
        self.assertEqual(self.app.status.text, "Status: Disconnecting")
        self.controller.last_error = "Could not stop the local SOCKS proxy cleanly."
        self.controller.state = ConnectionState.ERROR
        self.controller.shutdown_future.set_exception(
            ConnectionControllerError(self.controller.last_error)
        )
        _run_idle_callbacks()

        self.assertFalse(self.app.window.closed)
        self.assertFalse(self.app._closing)
        self.assertIn("Status: Error", self.app.status.text)
        self.assertIn("retry Disconnect", self.app.feedback.text)
        self.assertTrue(self.app.disconnect_button.sensitive)

        self.app._closing = False
        self.app.controller = self.controller
        self.controller.shutdown_future = Future()
        self.assertTrue(self.app.on_close_request(self.app.window))
        self.controller.state = ConnectionState.DISCONNECTED
        self.controller.shutdown_future.set_result(ConnectionState.DISCONNECTED)
        _run_idle_callbacks()
        self.assertTrue(self.app.window.closed)
        self.assertIsNone(self.app._controller)

    def test_close_completes_if_disconnect_finishes_just_before_close_request(self):
        self.app._controller = self.controller
        self.controller.state = ConnectionState.PROXY_READY
        self.app.on_disconnect(None)
        self.controller.state = ConnectionState.DISCONNECTED
        self.controller.disconnect_future.set_result(ConnectionState.DISCONNECTED)

        self.assertTrue(self.app.on_close_request(self.app.window))
        self.controller.shutdown_future.set_result(ConnectionState.DISCONNECTED)
        _run_idle_callbacks()

        self.assertTrue(self.app.window.closed)
        self.assertIsNone(self.app._controller)


if __name__ == "__main__":
    unittest.main()
