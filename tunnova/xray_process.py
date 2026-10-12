"""Manage an Xray-core subprocess without persisting its configuration."""
import atexit
import json
import os
import selectors
import signal
import socket
import subprocess
import threading
import time
from enum import Enum
from typing import Any


_MAX_CONFIG_BYTES = 1024 * 1024


class XrayProcessError(RuntimeError):
    """A safe, credential-free Xray lifecycle error."""


class XrayProcessState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class XrayProcessManager:
    """Start Xray with an in-memory config and supervise its local SOCKS port."""

    def __init__(
        self,
        executable_path: str | os.PathLike[str],
        *,
        startup_timeout: float = 10.0,
        shutdown_timeout: float = 3.0,
        readiness_interval: float = 0.05,
    ) -> None:
        executable = os.fspath(executable_path)
        if not executable:
            raise ValueError("An Xray executable path must be configured.")
        if startup_timeout <= 0 or shutdown_timeout <= 0 or readiness_interval <= 0:
            raise ValueError("Lifecycle timeouts and intervals must be positive.")

        self._executable_path = executable
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._readiness_interval = readiness_interval
        self._process: subprocess.Popen[str] | None = None
        self._state = XrayProcessState.STOPPED
        self._condition = threading.Condition()
        self._launching = False
        self._cleanup_in_progress = False

    @property
    def state(self) -> XrayProcessState:
        with self._condition:
            return self._state

    def start(self, config: dict[str, Any]) -> None:
        """Start Xray and wait for its local SOCKS listener without remote traffic."""
        try:
            config_data = json.dumps(config, separators=(",", ":")).encode("utf-8")
            socks_port = self._get_socks_port(config)
        except (TypeError, ValueError, KeyError, AttributeError):
            raise XrayProcessError("The Xray configuration is invalid.") from None
        if len(config_data) > _MAX_CONFIG_BYTES:
            raise XrayProcessError("The Xray configuration exceeds the supported size limit.")
        if not self._port_available(socks_port):
            raise XrayProcessError("The local SOCKS port is already in use.")

        with self._condition:
            if self._state in {
                XrayProcessState.STARTING,
                XrayProcessState.RUNNING,
                XrayProcessState.STOPPING,
            }:
                raise XrayProcessError("Xray is already starting, running, or stopping.")
            if self._process is not None:
                raise XrayProcessError("A previous Xray process still requires cleanup.")
            self._state = XrayProcessState.STARTING
            self._launching = True

        try:
            process = subprocess.Popen(
                [self._executable_path, "run", "-format", "json", "-config", "stdin:"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=False,
                bufsize=0,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, ValueError):
            with self._condition:
                self._launching = False
                self._state = (
                    XrayProcessState.STOPPED
                    if self._state == XrayProcessState.STOPPING
                    else XrayProcessState.FAILED
                )
                self._condition.notify_all()
            raise XrayProcessError("Xray could not be started; check the configured executable.") from None

        cleanup_owner = False
        with self._condition:
            self._process = process
            self._launching = False
            atexit.register(self.stop)
            if self._state == XrayProcessState.STOPPING and not self._cleanup_in_progress:
                self._cleanup_in_progress = True
                cleanup_owner = True
            interrupted = self._state != XrayProcessState.STARTING
            self._condition.notify_all()

        if interrupted:
            if cleanup_owner:
                self._cleanup_and_finish(process, XrayProcessState.STOPPED)
            raise XrayProcessError("Xray startup was stopped.")

        deadline = time.monotonic() + self._startup_timeout
        try:
            self._write_configuration(process, config_data, deadline)
            self._wait_until_ready(process, socks_port, deadline)
        except BaseException as exc:
            cleanup_owner, stop_requested = self._claim_start_cleanup(process)
            if cleanup_owner:
                self._cleanup_and_finish(
                    process,
                    XrayProcessState.STOPPED if stop_requested else XrayProcessState.FAILED,
                )
            if isinstance(exc, XrayProcessError):
                raise
            if isinstance(exc, Exception):
                raise XrayProcessError("Xray startup failed; process cleanup was attempted.") from None
            raise

    def stop(self) -> None:
        """Stop Xray and its Linux process group, escalating after a timeout."""
        deadline = time.monotonic() + self._shutdown_timeout
        with self._condition:
            if self._state != XrayProcessState.STOPPING:
                self._state = XrayProcessState.STOPPING

            while self._cleanup_in_progress or (self._process is None and self._launching):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise XrayProcessError("Timed out waiting for Xray lifecycle work to finish.")
                self._condition.wait(remaining)

            process = self._process
            if process is None:
                self._state = XrayProcessState.STOPPED
                self._condition.notify_all()
                atexit.unregister(self.stop)
                return
            self._cleanup_in_progress = True

        self._cleanup_and_finish(process, XrayProcessState.STOPPED)
        if self.state == XrayProcessState.FAILED:
            raise XrayProcessError("Xray could not be stopped cleanly.")

    def is_running(self) -> bool:
        """Return whether Xray is running and record unexpected child exits."""
        with self._condition:
            if self._process is None or self._state != XrayProcessState.RUNNING:
                return False
            process = self._process
            try:
                alive = process.poll() is None
            except Exception:
                alive = False
            if alive:
                return True
            self._state = XrayProcessState.STOPPING
            self._cleanup_in_progress = True

        self._cleanup_and_finish(process, XrayProcessState.FAILED)
        return False

    @staticmethod
    def _get_socks_port(config: dict[str, Any]) -> int:
        if not isinstance(config, dict) or not isinstance(config.get("inbounds"), list):
            raise ValueError
        local_socks = [
            inbound for inbound in config["inbounds"]
            if isinstance(inbound, dict)
            and inbound.get("protocol") == "socks"
            and inbound.get("listen") == "127.0.0.1"
        ]
        if len(local_socks) != 1:
            raise ValueError
        port = local_socks[0].get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError
        return port

    @staticmethod
    def _port_available(port: int) -> bool:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    @staticmethod
    def _listener_ready(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return True
        except OSError:
            return False

    def _write_configuration(
        self, process: subprocess.Popen[bytes], config_data: bytes, deadline: float
    ) -> None:
        if process.stdin is None:
            raise XrayProcessError("Xray did not accept configuration input.")
        selector = selectors.DefaultSelector()
        try:
            descriptor = process.stdin.fileno()
            os.set_blocking(descriptor, False)
            selector.register(descriptor, selectors.EVENT_WRITE)
            view = memoryview(config_data)
            while view:
                with self._condition:
                    if self._process is not process or self._state != XrayProcessState.STARTING:
                        raise XrayProcessError("Xray startup was stopped.")
                    if process.poll() is not None:
                        raise XrayProcessError("Xray exited while receiving its configuration.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise XrayProcessError("Xray configuration input timed out.")
                if not selector.select(min(remaining, 0.1)):
                    continue
                try:
                    written = os.write(descriptor, view)
                except BlockingIOError:
                    continue
                if written <= 0:
                    raise XrayProcessError("Xray did not accept configuration input.")
                view = view[written:]
        finally:
            selector.close()
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    def _wait_until_ready(
        self, process: subprocess.Popen[bytes], port: int, deadline: float
    ) -> None:
        while time.monotonic() < deadline:
            with self._condition:
                if self._process is not process or self._state != XrayProcessState.STARTING:
                    raise XrayProcessError("Xray startup was stopped.")
                if process.poll() is not None:
                    raise XrayProcessError("Xray exited before its local SOCKS listener became ready.")

            ready = self._listener_ready(port)
            with self._condition:
                if self._process is not process or self._state != XrayProcessState.STARTING:
                    raise XrayProcessError("Xray startup was stopped.")
                if process.poll() is not None:
                    raise XrayProcessError("Xray exited during local SOCKS readiness checks.")
                if ready:
                    self._state = XrayProcessState.RUNNING
                    self._condition.notify_all()
                    return
            time.sleep(min(self._readiness_interval, max(0, deadline - time.monotonic())))
        raise XrayProcessError("Xray did not become ready before the startup timeout.")

    def _claim_start_cleanup(self, process: subprocess.Popen[bytes]) -> tuple[bool, bool]:
        with self._condition:
            if self._process is not process or self._cleanup_in_progress:
                return False, self._state == XrayProcessState.STOPPING
            stop_requested = self._state == XrayProcessState.STOPPING
            if self._state == XrayProcessState.STARTING:
                self._state = XrayProcessState.STOPPING
            elif not stop_requested:
                return False, False
            self._cleanup_in_progress = True
            self._condition.notify_all()
            return True, stop_requested

    def _cleanup_and_finish(
        self, process: subprocess.Popen[bytes], completed_state: XrayProcessState
    ) -> None:
        try:
            cleaned = self._terminate_process(process)
        except Exception:
            cleaned = False
        with self._condition:
            if cleaned and self._process is process:
                self._process = None
                atexit.unregister(self.stop)
            self._state = completed_state if cleaned else XrayProcessState.FAILED
            self._cleanup_in_progress = False
            self._condition.notify_all()

    def _group_exists(self, process_id: int) -> bool:
        try:
            os.killpg(process_id, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def _terminate_process(self, process: subprocess.Popen[bytes]) -> bool:
        try:
            process_id = process.pid
        except Exception:
            return False
        try:
            os.killpg(process_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass

        deadline = time.monotonic() + self._shutdown_timeout
        try:
            process.wait(timeout=self._shutdown_timeout)
        except Exception:
            pass

        try:
            while self._group_exists(process_id) and time.monotonic() < deadline:
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            group_alive = self._group_exists(process_id)
        except Exception:
            group_alive = True

        try:
            process_alive = process.poll() is None
        except Exception:
            process_alive = True
        if process_alive or group_alive:
            try:
                os.killpg(process_id, signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            try:
                process.wait(timeout=self._shutdown_timeout)
            except Exception:
                pass

        try:
            return process.poll() is not None and not self._group_exists(process_id)
        except Exception:
            return False