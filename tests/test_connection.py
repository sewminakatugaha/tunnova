from concurrent.futures import CancelledError
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from tunnova.connection import (
    ConnectionController,
    ConnectionControllerError,
    ConnectionState,
)
from tunnova.profiles import parse_vless_uri
from tunnova.xray_process import XrayProcessState


SYNTHETIC_UUID = "11111111-2222-4333-8444-555555555555"


class ObservedEvent:
    def __init__(self, event, observations):
        self._event = event
        self._observations = observations

    def clear(self):
        self._event.clear()

    def set(self):
        self._event.set()

    def is_set(self):
        return self._event.is_set()

    def wait(self, timeout=None):
        self._observations.put("startup_wait")
        return self._event.wait(timeout)


def make_profile():
    return parse_vless_uri(
        f"vless://{SYNTHETIC_UUID}@gateway.example.com:443"
        "?type=ws&security=tls&sni=sni.example.net"
        "&host=websocket.example.org&path=%2Fedge"
    )


class ConnectionControllerTests(unittest.TestCase):
    def make_manager(self):
        manager = Mock()
        manager.state = XrayProcessState.STOPPED
        manager.start.side_effect = lambda _config: setattr(
            manager, "state", XrayProcessState.RUNNING
        )
        manager.is_running.return_value = True
        manager.stop.side_effect = lambda: setattr(
            manager, "state", XrayProcessState.STOPPED
        )
        return manager

    def make_controller(self, manager, **kwargs):
        with patch("tunnova.connection.XrayProcessManager") as manager_type:
            manager_type.return_value = manager
            controller = ConnectionController(
                make_profile(), "/synthetic/path/xray", **kwargs
            )

        def cleanup_controller():
            controller.shutdown().result(timeout=3)
            controller._notification_thread.join(timeout=1)
            self.assertFalse(controller._notification_thread.is_alive())

        self.addCleanup(cleanup_controller)
        return controller

    def test_connect_builds_loopback_config_and_reports_only_proxy_readiness(self):
        manager = self.make_manager()
        controller = self.make_controller(
            manager, socks_port=12345, tls_verify_name="trusted.example.com"
        )

        result = controller.connect().result(timeout=3)

        self.assertEqual(result, ConnectionState.PROXY_READY)
        self.assertEqual(controller.state, ConnectionState.PROXY_READY)
        manager.start.assert_called_once()
        config = manager.start.call_args.args[0]
        self.assertEqual(config["inbounds"][0]["listen"], "127.0.0.1")
        self.assertEqual(config["inbounds"][0]["port"], 12345)
        tls_settings = config["outbounds"][0]["streamSettings"]["tlsSettings"]
        ws_settings = config["outbounds"][0]["streamSettings"]["wsSettings"]
        self.assertEqual(tls_settings["serverName"], "sni.example.net")
        self.assertEqual(
            tls_settings["verifyPeerCertByName"], "trusted.example.com"
        )
        self.assertEqual(ws_settings["host"], "websocket.example.org")
        self.assertIs(tls_settings["allowInsecure"], False)
        self.assertNotIn("connected", controller.state.value)

    def test_repeated_connect_shares_one_start_attempt(self):
        manager = self.make_manager()
        start_entered = threading.Event()
        release_start = threading.Event()

        def delayed_start(_config):
            manager.state = XrayProcessState.STARTING
            start_entered.set()
            release_start.wait(2)
            manager.state = XrayProcessState.RUNNING

        manager.start.side_effect = delayed_start
        controller = self.make_controller(manager)

        first = controller.connect()
        self.assertTrue(start_entered.wait(1))
        second = controller.connect()
        release_start.set()

        self.assertIs(first, second)
        self.assertEqual(first.result(timeout=3), ConnectionState.PROXY_READY)
        manager.start.assert_called_once()

    def test_fast_startup_notifications_are_ordered_and_callbacks_are_unlocked(self):
        manager = self.make_manager()
        controller = self.make_controller(manager)
        connecting_entered = threading.Event()
        release_connecting = threading.Event()
        proxy_ready_seen = threading.Event()
        disconnected_seen = threading.Event()
        states = []
        state_lock = threading.Lock()

        def listener(state):
            controller.state
            with state_lock:
                states.append(state)
            if state == ConnectionState.CONNECTING:
                connecting_entered.set()
                release_connecting.wait(2)
            elif state == ConnectionState.PROXY_READY:
                proxy_ready_seen.set()
            elif state == ConnectionState.DISCONNECTED:
                disconnected_seen.set()

        controller.add_state_listener(listener)
        try:
            connecting = controller.connect()
            self.assertTrue(connecting_entered.wait(1))
            self.assertEqual(connecting.result(timeout=3), ConnectionState.PROXY_READY)
            disconnecting = controller.shutdown()
            self.assertEqual(
                disconnecting.result(timeout=3), ConnectionState.DISCONNECTED
            )
        finally:
            release_connecting.set()

        self.assertTrue(proxy_ready_seen.wait(1))
        self.assertTrue(disconnected_seen.wait(1))
        with state_lock:
            self.assertEqual(
                states,
                [
                    ConnectionState.CONNECTING,
                    ConnectionState.PROXY_READY,
                    ConnectionState.DISCONNECTING,
                    ConnectionState.DISCONNECTED,
                ],
            )

    def test_cancelled_queued_connect_completes_startup_and_shutdown(self):
        manager = self.make_manager()
        controller = self.make_controller(manager)
        workers_entered = threading.Barrier(3)
        release_workers = threading.Event()

        def occupy_worker():
            workers_entered.wait(timeout=2)
            release_workers.wait(2)

        first_worker = controller._executor.submit(occupy_worker)
        second_worker = controller._executor.submit(occupy_worker)
        workers_entered.wait(timeout=2)
        try:
            connecting = controller.connect()
            self.assertTrue(connecting.cancel())
            with self.assertRaises(CancelledError):
                connecting.result()

            disconnecting = controller.shutdown()
            release_workers.set()
            self.assertEqual(
                disconnecting.result(timeout=3), ConnectionState.DISCONNECTED
            )
        finally:
            release_workers.set()
        first_worker.result(timeout=1)
        second_worker.result(timeout=1)
        manager.start.assert_not_called()
        manager.stop.assert_not_called()
        self.assertEqual(controller.state, ConnectionState.DISCONNECTED)

    def test_raising_listener_does_not_block_other_listeners_or_lifecycle(self):
        manager = self.make_manager()
        controller = self.make_controller(manager)
        observed = queue.Queue()

        def raising_listener(_state):
            raise RuntimeError(SYNTHETIC_UUID)

        def recording_listener(state):
            controller.state
            observed.put(state)

        controller.add_state_listener(raising_listener)
        controller.add_state_listener(recording_listener)
        self.assertEqual(controller.connect().result(timeout=3), ConnectionState.PROXY_READY)
        self.assertEqual(controller.disconnect().result(timeout=3), ConnectionState.DISCONNECTED)
        self.assertEqual(
            [observed.get(timeout=1) for _ in range(4)],
            [
                ConnectionState.CONNECTING,
                ConnectionState.PROXY_READY,
                ConnectionState.DISCONNECTING,
                ConnectionState.DISCONNECTED,
            ],
        )

    def test_cancelled_queued_shutdown_future_resubmits_cleanup(self):
        manager = self.make_manager()
        controller = self.make_controller(manager)
        controller.connect().result(timeout=3)
        workers_entered = threading.Barrier(3)
        release_workers = threading.Event()

        def occupy_worker():
            workers_entered.wait(timeout=2)
            release_workers.wait(2)

        first_worker = controller._executor.submit(occupy_worker)
        second_worker = controller._executor.submit(occupy_worker)
        workers_entered.wait(timeout=2)
        try:
            canceled_shutdown = controller.shutdown()
            self.assertTrue(canceled_shutdown.cancel())
            retry_shutdown = controller.disconnect()
            self.assertIsNot(retry_shutdown, canceled_shutdown)
            self.assertEqual(controller.state, ConnectionState.DISCONNECTING)
            release_workers.set()
            self.assertEqual(
                retry_shutdown.result(timeout=3), ConnectionState.DISCONNECTED
            )
        finally:
            release_workers.set()
        first_worker.result(timeout=1)
        second_worker.result(timeout=1)
        self.assertEqual(manager.state, XrayProcessState.STOPPED)
        manager.stop.assert_called_once()

    def test_startup_failure_is_sanitized_and_cleanup_is_attempted(self):
        manager = self.make_manager()

        def fail_start(_config):
            manager.state = XrayProcessState.FAILED
            raise RuntimeError(SYNTHETIC_UUID)

        manager.start.side_effect = fail_start
        controller = self.make_controller(manager)

        with self.assertRaises(ConnectionControllerError) as raised:
            controller.connect().result(timeout=3)

        self.assertEqual(str(raised.exception), "Could not start the local SOCKS proxy.")
        self.assertNotIn(SYNTHETIC_UUID, str(raised.exception))
        self.assertEqual(controller.state, ConnectionState.ERROR)
        self.assertEqual(controller.last_error, str(raised.exception))
        manager.stop.assert_called_once()

    def test_disconnect_during_startup_cancels_and_cleans_up(self):
        manager = self.make_manager()
        start_entered = threading.Event()
        release_start = threading.Event()
        observations = queue.Queue()

        def delayed_start(_config):
            manager.state = XrayProcessState.STARTING
            start_entered.set()
            release_start.wait(2)
            manager.state = XrayProcessState.RUNNING

        def stop_manager():
            observations.put("stop")
            manager.state = XrayProcessState.STOPPED

        manager.start.side_effect = delayed_start
        manager.stop.side_effect = stop_manager
        controller = self.make_controller(manager)
        controller._start_done = ObservedEvent(controller._start_done, observations)

        connecting = controller.connect()
        self.assertTrue(start_entered.wait(1))
        disconnected = controller.disconnect()
        try:
            self.assertEqual(observations.get(timeout=1), "startup_wait")
            self.assertFalse(disconnected.done())
            self.assertIs(controller.disconnect(), disconnected)
            with self.assertRaisesRegex(ConnectionControllerError, "shutting down"):
                controller.connect().result(timeout=1)
        finally:
            release_start.set()

        self.assertEqual(disconnected.result(timeout=3), ConnectionState.DISCONNECTED)
        with self.assertRaisesRegex(ConnectionControllerError, "canceled"):
            connecting.result(timeout=3)
        self.assertEqual(controller.state, ConnectionState.DISCONNECTED)
        manager.stop.assert_called_once()

    def test_late_start_is_stopped_before_disconnected_is_reported(self):
        manager = self.make_manager()
        start_entered = threading.Event()
        release_start = threading.Event()
        observations = queue.Queue()
        disconnected_seen = threading.Event()
        manager_states_at_disconnected = []

        def delayed_start(_config):
            manager.state = XrayProcessState.STARTING
            start_entered.set()
            release_start.wait(2)
            manager.state = XrayProcessState.RUNNING

        def stop_manager():
            observations.put("stop")
            manager.state = XrayProcessState.STOPPED

        manager.start.side_effect = delayed_start
        manager.stop.side_effect = stop_manager
        controller = self.make_controller(manager)

        def on_state(state):
            if state == ConnectionState.DISCONNECTED:
                manager_states_at_disconnected.append(manager.state)
                disconnected_seen.set()

        controller.add_state_listener(on_state)
        controller._start_done = ObservedEvent(controller._start_done, observations)
        connecting = controller.connect()
        self.assertTrue(start_entered.wait(1))
        disconnecting = controller.disconnect()
        self.assertEqual(observations.get(timeout=1), "startup_wait")
        release_start.set()

        self.assertEqual(disconnecting.result(timeout=3), ConnectionState.DISCONNECTED)
        with self.assertRaises(ConnectionControllerError):
            connecting.result(timeout=3)
        self.assertTrue(disconnected_seen.wait(1))
        self.assertEqual(manager_states_at_disconnected, [XrayProcessState.STOPPED])
        self.assertEqual(manager.state, XrayProcessState.STOPPED)

    def test_shutdown_cleans_up_and_rejects_future_connects(self):
        manager = self.make_manager()
        controller = self.make_controller(manager)
        controller.connect().result(timeout=3)

        self.assertEqual(controller.shutdown().result(timeout=3), ConnectionState.DISCONNECTED)
        with self.assertRaisesRegex(ConnectionControllerError, "shut down"):
            controller.connect().result(timeout=1)
        manager.stop.assert_called_once()

    def test_shutdown_during_startup_waits_then_stops_late_process(self):
        manager = self.make_manager()
        start_entered = threading.Event()
        release_start = threading.Event()
        observations = queue.Queue()

        def delayed_start(_config):
            manager.state = XrayProcessState.STARTING
            start_entered.set()
            release_start.wait(2)
            manager.state = XrayProcessState.RUNNING

        manager.start.side_effect = delayed_start
        controller = self.make_controller(manager)
        controller._start_done = ObservedEvent(controller._start_done, observations)

        connecting = controller.connect()
        self.assertTrue(start_entered.wait(1))
        shutting_down = controller.shutdown()
        try:
            self.assertEqual(observations.get(timeout=1), "startup_wait")
            self.assertFalse(shutting_down.done())
        finally:
            release_start.set()

        self.assertEqual(shutting_down.result(timeout=3), ConnectionState.DISCONNECTED)
        with self.assertRaisesRegex(ConnectionControllerError, "canceled"):
            connecting.result(timeout=3)
        self.assertEqual(manager.state, XrayProcessState.STOPPED)

    def test_startup_timeout_does_not_claim_disconnected_and_can_retry(self):
        manager = self.make_manager()
        start_entered = threading.Event()
        release_start = threading.Event()

        def delayed_start(_config):
            manager.state = XrayProcessState.STARTING
            start_entered.set()
            release_start.wait(2)
            manager.state = XrayProcessState.RUNNING

        manager.start.side_effect = delayed_start
        controller = self.make_controller(manager)
        controller._STARTUP_STOP_TIMEOUT = 0.01
        connecting = controller.connect()
        self.assertTrue(start_entered.wait(1))

        disconnecting = controller.disconnect()
        with self.assertRaisesRegex(ConnectionControllerError, "did not finish cleanup"):
            disconnecting.result(timeout=1)
        self.assertEqual(controller.state, ConnectionState.ERROR)
        manager.stop.assert_not_called()

        release_start.set()
        with self.assertRaisesRegex(ConnectionControllerError, "canceled"):
            connecting.result(timeout=3)
        self.assertEqual(manager.state, XrayProcessState.STOPPED)
        self.assertEqual(
            controller.disconnect().result(timeout=3), ConnectionState.DISCONNECTED
        )

    def test_repeated_shutdown_retries_failed_cleanup_without_abandoning_executor(self):
        manager = self.make_manager()
        stop_entered = threading.Event()
        release_failed_stop = threading.Event()
        observed_states = queue.Queue()

        def fail_first_stop():
            stop_entered.set()
            release_failed_stop.wait(2)
            raise RuntimeError(SYNTHETIC_UUID)

        def succeed_retry():
            manager.state = XrayProcessState.STOPPED

        stop_count = 0

        def stop_manager():
            nonlocal stop_count
            stop_count += 1
            if stop_count == 1:
                fail_first_stop()
            else:
                succeed_retry()

        manager.stop.side_effect = stop_manager
        controller = self.make_controller(manager)
        controller.add_state_listener(observed_states.put)
        controller.connect().result(timeout=3)

        first_shutdown = controller.shutdown()
        self.assertTrue(stop_entered.wait(1))
        repeated_shutdown = controller.shutdown()
        self.assertIs(first_shutdown, repeated_shutdown)
        release_failed_stop.set()
        with self.assertRaises(ConnectionControllerError) as raised:
            first_shutdown.result(timeout=3)
        self.assertNotIn(SYNTHETIC_UUID, str(raised.exception))
        self.assertEqual(controller.state, ConnectionState.ERROR)
        self.assertEqual(manager.state, XrayProcessState.RUNNING)
        self.assertEqual(
            [observed_states.get(timeout=1) for _ in range(4)],
            [
                ConnectionState.CONNECTING,
                ConnectionState.PROXY_READY,
                ConnectionState.DISCONNECTING,
                ConnectionState.ERROR,
            ],
        )

        retry_shutdown = controller.shutdown()
        self.assertEqual(
            retry_shutdown.result(timeout=3), ConnectionState.DISCONNECTED
        )
        self.assertEqual(manager.stop.call_count, 2)
        self.assertEqual(
            [observed_states.get(timeout=1) for _ in range(2)],
            [ConnectionState.DISCONNECTING, ConnectionState.DISCONNECTED],
        )
        with self.assertRaisesRegex(ConnectionControllerError, "shut down"):
            controller.connect().result(timeout=1)

    def test_unexpected_process_exit_sets_error_and_cleans_up(self):
        manager = self.make_manager()
        manager.is_running.side_effect = [True, False]
        controller = self.make_controller(manager)
        error_seen = threading.Event()

        def on_state(state):
            if state == ConnectionState.ERROR:
                error_seen.set()

        controller.add_state_listener(on_state)
        controller.connect().result(timeout=3)

        self.assertTrue(error_seen.wait(2))
        self.assertEqual(controller.state, ConnectionState.ERROR)
        self.assertIn("stopped unexpectedly", controller.last_error)
        manager.stop.assert_called_once()

    def test_disconnect_after_proxy_ready_stops_manager(self):
        manager = self.make_manager()
        controller = self.make_controller(manager)
        controller.connect().result(timeout=3)

        self.assertEqual(controller.disconnect().result(timeout=3), ConnectionState.DISCONNECTED)
        self.assertEqual(controller.state, ConnectionState.DISCONNECTED)
        manager.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()