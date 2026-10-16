"""Asynchronous orchestration for a local Xray SOCKS proxy."""
from concurrent.futures import Future, ThreadPoolExecutor
from enum import Enum
import os
import queue
import threading
from typing import Callable

from .profiles import VlessProfile
from .xray_config import build_xray_config
from .xray_process import XrayProcessManager, XrayProcessState


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    PROXY_READY = "proxy_ready"
    DISCONNECTING = "disconnecting"
    ERROR = "error"


class ConnectionControllerError(RuntimeError):
    """A safe, credential-free connection lifecycle error."""


class ConnectionController:
    """Manage Xray asynchronously without changing system networking state.

    ``proxy_ready`` means only that the local SOCKS5 listener passed its
    readiness check. It does not verify remote internet connectivity.
    State listeners run serially on a dedicated callback thread, outside the
    controller lock. GUI callers should marshal updates onto their main context.
    """

    _MONITOR_INTERVAL = 0.2
    _STARTUP_STOP_TIMEOUT = 15.0
    _ALLOWED_TRANSITIONS = {
        ConnectionState.DISCONNECTED: frozenset({ConnectionState.CONNECTING}),
        ConnectionState.CONNECTING: frozenset(
            {
                ConnectionState.PROXY_READY,
                ConnectionState.DISCONNECTING,
                ConnectionState.ERROR,
            }
        ),
        ConnectionState.PROXY_READY: frozenset(
            {ConnectionState.DISCONNECTING, ConnectionState.ERROR}
        ),
        ConnectionState.DISCONNECTING: frozenset(
            {ConnectionState.DISCONNECTED, ConnectionState.ERROR}
        ),
        ConnectionState.ERROR: frozenset(
            {ConnectionState.CONNECTING, ConnectionState.DISCONNECTING}
        ),
    }

    def __init__(
        self,
        profile: VlessProfile,
        xray_executable_path: str | os.PathLike[str],
        socks_port: int = 10890,
        *,
        tls_verify_name: str | None = None,
    ) -> None:
        self._profile = profile
        self._socks_port = socks_port
        self._tls_verify_name = tls_verify_name
        self._manager = XrayProcessManager(xray_executable_path)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tunnova-connection")
        self._lock = threading.RLock()
        self._manager_lock = threading.RLock()
        self._state_notifications: queue.Queue[
            tuple[tuple[Callable[[ConnectionState], None], ...], ConnectionState]
            | None
        ] = queue.Queue()
        self._notification_thread = threading.Thread(
            target=self._dispatch_state_notifications,
            name="tunnova-connection-state",
            daemon=True,
        )
        self._notification_thread.start()
        self._state = ConnectionState.DISCONNECTED
        self._last_error: str | None = None
        self._cleanup_ok = True
        self._closed = False
        self._cancel_connect = threading.Event()
        self._start_done = threading.Event()
        self._start_done.set()
        self._connect_future: Future[ConnectionState] | None = None
        self._disconnect_future: Future[ConnectionState] | None = None
        self._monitor_stop: threading.Event | None = None
        self._monitor_thread: threading.Thread | None = None
        self._state_listeners: set[Callable[[ConnectionState], None]] = set()
        self._shutdown_started = False

    @property
    def state(self) -> ConnectionState:
        with self._lock:
            return self._state

    @property
    def last_error(self) -> str | None:
        """Return the sanitized error for the most recent failed operation."""
        with self._lock:
            return self._last_error

    def add_state_listener(
        self, listener: Callable[[ConnectionState], None]
    ) -> None:
        """Register a callback that receives lifecycle states, never secrets."""
        with self._lock:
            self._state_listeners.add(listener)

    def remove_state_listener(
        self, listener: Callable[[ConnectionState], None]
    ) -> None:
        with self._lock:
            self._state_listeners.discard(listener)

    def connect(self) -> Future[ConnectionState]:
        """Start the local proxy in a worker and return its completion future."""
        with self._lock:
            if self._closed:
                return self._failed_future("The connection controller is shut down.")
            if self._state == ConnectionState.CONNECTING:
                assert self._connect_future is not None
                return self._connect_future
            if self._state == ConnectionState.PROXY_READY:
                return self._completed_future(ConnectionState.PROXY_READY)
            if self._state == ConnectionState.DISCONNECTING:
                return self._failed_future("The local proxy is shutting down.")
            if not self._cleanup_ok:
                return self._failed_future("Disconnect the failed local proxy before retrying.")

            self._set_state_locked(ConnectionState.CONNECTING)
            self._last_error = None
            self._cancel_connect.clear()
            self._start_done.clear()
            future = self._executor.submit(self._connect_worker)
            self._connect_future = future
            future.add_done_callback(self._connect_future_finished)
        return future

    def disconnect(self) -> Future[ConnectionState]:
        """Stop the local proxy in a worker and return its completion future."""
        with self._lock:
            if self._state == ConnectionState.DISCONNECTED:
                return self._completed_future(ConnectionState.DISCONNECTED)
            if self._state == ConnectionState.DISCONNECTING:
                assert self._disconnect_future is not None
                return self._disconnect_future

            self._cancel_connect.set()
            if self._monitor_stop is not None:
                self._monitor_stop.set()
            self._set_state_locked(ConnectionState.DISCONNECTING)
            future = self._executor.submit(self._disconnect_worker)
            self._disconnect_future = future
            future.add_done_callback(self._disconnect_future_finished)
        return future

    def shutdown(self) -> Future[ConnectionState]:
        """Reject future connects and asynchronously clean up the Xray process."""
        with self._lock:
            self._closed = True
            already_stopped = (
                self._state == ConnectionState.DISCONNECTED and self._cleanup_ok
            )
        future = (
            self._completed_future(ConnectionState.DISCONNECTED)
            if already_stopped
            else self.disconnect()
        )
        future.add_done_callback(self._finish_shutdown)
        return future

    def _connect_worker(self) -> ConnectionState:
        try:
            if self._cancel_connect.is_set():
                raise ConnectionControllerError("Connection attempt was canceled.")
            config = build_xray_config(
                self._profile,
                self._socks_port,
                tls_verify_name=self._tls_verify_name,
            )
            with self._manager_lock:
                with self._lock:
                    if (
                        self._state != ConnectionState.CONNECTING
                        or self._cancel_connect.is_set()
                    ):
                        raise ConnectionControllerError(
                            "Connection attempt was canceled."
                        )
                self._manager.start(config)
                if self._cancel_connect.is_set():
                    raise ConnectionControllerError("Connection attempt was canceled.")
                if not self._manager.is_running():
                    raise ConnectionControllerError(
                        "The local SOCKS proxy stopped during startup."
                    )

            with self._lock:
                if self._state != ConnectionState.CONNECTING or self._cancel_connect.is_set():
                    raise ConnectionControllerError("Connection attempt was canceled.")
                self._set_state_locked(ConnectionState.PROXY_READY)
                self._cleanup_ok = True
                self._last_error = None
                self._monitor_stop = threading.Event()
                self._monitor_thread = threading.Thread(
                    target=self._monitor_process,
                    args=(self._monitor_stop,),
                    name="tunnova-xray-monitor",
                    daemon=True,
                )
                self._monitor_thread.start()
            return ConnectionState.PROXY_READY
        except BaseException:
            cancelled = self._cancel_connect.is_set()
            cleanup_ok = self._stop_manager()
            message = (
                "Could not start the local SOCKS proxy."
                if cleanup_ok
                else "Could not start the local SOCKS proxy; cleanup failed."
            )
            with self._lock:
                self._cleanup_ok = cleanup_ok
                if self._state not in {
                    ConnectionState.DISCONNECTING,
                    ConnectionState.ERROR,
                }:
                    self._set_state_locked(ConnectionState.ERROR)
                    self._last_error = (
                        "Connection attempt was canceled." if cancelled else message
                    )
            if cancelled:
                raise ConnectionControllerError("Connection attempt was canceled.") from None
            raise ConnectionControllerError(message) from None
        finally:
            self._start_done.set()

    def _disconnect_worker(self) -> ConnectionState:
        monitor_thread = self._monitor_thread
        if (
            monitor_thread is not None
            and monitor_thread.ident is not None
            and monitor_thread is not threading.current_thread()
        ):
            monitor_thread.join()

        if not self._start_done.wait(self._STARTUP_STOP_TIMEOUT):
            state = ConnectionState.ERROR
            message = "The connection attempt did not finish cleanup in time."
            with self._lock:
                self._cleanup_ok = False
                self._set_state_locked(state)
                self._last_error = message
            raise ConnectionControllerError(message) from None

        cleanup_ok = self._stop_manager()
        if cleanup_ok:
            state = ConnectionState.DISCONNECTED
            message = None
        else:
            state = ConnectionState.ERROR
            message = "Could not stop the local SOCKS proxy cleanly."

        with self._lock:
            self._cleanup_ok = cleanup_ok
            self._set_state_locked(state)
            self._last_error = message
        if not cleanup_ok:
            raise ConnectionControllerError(message) from None
        return state

    def _monitor_process(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self._MONITOR_INTERVAL):
            with self._lock:
                if self._state != ConnectionState.PROXY_READY:
                    return
            try:
                with self._manager_lock:
                    process_running = self._manager.is_running()
            except BaseException:
                process_running = False
            if process_running:
                continue

            cleanup_ok = self._stop_manager()
            with self._lock:
                if self._state != ConnectionState.PROXY_READY:
                    return
                self._cleanup_ok = cleanup_ok
                self._set_state_locked(ConnectionState.ERROR)
                self._last_error = "The Xray process stopped unexpectedly; the local proxy is unavailable."
            return

    def _stop_manager(self) -> bool:
        with self._manager_lock:
            try:
                if self._manager.state == XrayProcessState.STOPPED:
                    return True
                self._manager.stop()
            except BaseException:
                pass
            try:
                return self._manager.state == XrayProcessState.STOPPED
            except BaseException:
                return False

    @staticmethod
    def _completed_future(result: ConnectionState) -> Future[ConnectionState]:
        future: Future[ConnectionState] = Future()
        future.set_result(result)
        return future

    @staticmethod
    def _failed_future(message: str) -> Future[ConnectionState]:
        future: Future[ConnectionState] = Future()
        future.set_exception(ConnectionControllerError(message))
        return future

    def _set_state_locked(self, state: ConnectionState) -> None:
        if state not in self._ALLOWED_TRANSITIONS[self._state]:
            raise RuntimeError("Invalid connection lifecycle transition.")
        self._state = state
        self._state_notifications.put((tuple(self._state_listeners), state))

    def _dispatch_state_notifications(self) -> None:
        while True:
            notification = self._state_notifications.get()
            if notification is None:
                return
            listeners, state = notification
            for listener in listeners:
                self._invoke_listener(listener, state)

    @staticmethod
    def _invoke_listener(
        listener: Callable[[ConnectionState], None], state: ConnectionState
    ) -> None:
        try:
            listener(state)
        except BaseException:
            return

    def _connect_future_finished(self, future: Future[ConnectionState]) -> None:
        if not future.cancelled():
            return
        with self._lock:
            self._cancel_connect.set()
            self._start_done.set()
            if self._state == ConnectionState.CONNECTING:
                self._last_error = "Connection attempt was canceled."
                self._cleanup_ok = True
                self._set_state_locked(ConnectionState.ERROR)

    def _disconnect_future_finished(self, future: Future[ConnectionState]) -> None:
        if not future.cancelled():
            return
        with self._lock:
            if (
                self._disconnect_future is not future
                or self._state != ConnectionState.DISCONNECTING
            ):
                return
            try:
                retry = self._executor.submit(self._disconnect_worker)
            except RuntimeError:
                self._cleanup_ok = False
                self._set_state_locked(ConnectionState.ERROR)
                self._last_error = "Could not schedule local proxy cleanup."
                return
            self._disconnect_future = retry
            retry.add_done_callback(self._disconnect_future_finished)
            if self._closed:
                retry.add_done_callback(self._finish_shutdown)

    def _finish_shutdown(self, future: Future[ConnectionState]) -> None:
        try:
            if future.result() != ConnectionState.DISCONNECTED:
                return
        except Exception:
            return
        with self._lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True
            self._state_notifications.put(None)
        self._executor.shutdown(wait=False)