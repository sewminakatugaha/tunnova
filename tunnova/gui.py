"""Unprivileged GTK4 interface for a local Xray SOCKS proxy."""
from concurrent.futures import CancelledError, Future
import os
import shutil

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from .connection import (
    ConnectionController,
    ConnectionControllerError,
    ConnectionState,
)
from .profiles import ProfileError, ProfileManager, VlessProfile


class VpnApplication(Gtk.Application):
    def __init__(
        self,
        *,
        xray_executable_path: str | None = None,
        controller_factory=ConnectionController,
    ):
        super().__init__(application_id="org.tunnova.Tunnova")
        self.profile_manager = ProfileManager()
        self._xray_executable_path = (
            xray_executable_path
            if xray_executable_path is not None
            else os.environ.get("TUNNOVA_XRAY_EXECUTABLE") or shutil.which("xray") or ""
        )
        self._controller_factory = controller_factory
        self._controller: ConnectionController | None = None
        self._controller_state_listener = None
        self._closing = False
        self._close_allowed = False
        self.window = None

    def do_activate(self):
        if self.window is not None:
            self.window.present()
            return

        window = Gtk.ApplicationWindow(application=self)
        self.window = window
        window.set_title("Tunnova — Local SOCKS proxy")
        window.set_default_size(530, 700)
        window.connect("close-request", self.on_close_request)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(22)
        content.set_margin_bottom(22)
        content.set_margin_start(24)
        content.set_margin_end(24)

        heading = Gtk.Label(label="Tunnova")
        heading.set_xalign(0)
        heading.add_css_class("title-1")
        content.append(heading)

        subtitle = Gtk.Label(label="Local SOCKS5 proxy — no system-wide VPN or proxy changes")
        subtitle.set_xalign(0)
        subtitle.set_wrap(True)
        content.append(subtitle)

        self.status = Gtk.Label(label="Status: Disconnected")
        self.status.set_xalign(0)
        self.status.set_wrap(True)
        content.append(self.status)

        description = Gtk.Label(
            label="Import a supported VLESS + WebSocket + TLS profile for this session."
        )
        description.set_xalign(0)
        description.set_wrap(True)
        content.append(description)

        self.entry = Gtk.PasswordEntry()
        self.entry.props.placeholder_text = "vless://..."
        self.entry.props.show_peek_icon = True
        content.append(self.entry)

        self.import_button = Gtk.Button(label="Import Profile")
        self.import_button.connect("clicked", self.on_import)
        content.append(self.import_button)

        self.feedback = Gtk.Label(label="No profiles imported")
        self.feedback.set_xalign(0)
        self.feedback.set_wrap(True)
        content.append(self.feedback)

        self.profile_list = Gtk.ListBox()
        self.profile_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.profile_list.set_placeholder(Gtk.Label(label="No profiles imported yet"))
        self.profile_list.connect("row-selected", self.on_profile_selected)

        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(170)
        scroller.set_child(self.profile_list)
        content.append(scroller)

        self.remove_button = Gtk.Button(label="Remove Selected Profile")
        self.remove_button.set_sensitive(False)
        self.remove_button.connect("clicked", self.on_remove)
        content.append(self.remove_button)

        executable_label = Gtk.Label(label="Xray executable path")
        executable_label.set_xalign(0)
        content.append(executable_label)

        self.xray_path_entry = Gtk.Entry()
        self.xray_path_entry.set_text(self._xray_executable_path)
        self.xray_path_entry.set_placeholder_text("/path/to/xray")
        self.xray_path_entry.connect("changed", self.on_connection_setting_changed)
        content.append(self.xray_path_entry)

        verify_label = Gtk.Label(label="TLS certificate verification hostname (optional)")
        verify_label.set_xalign(0)
        content.append(verify_label)

        self.tls_verify_name_entry = Gtk.Entry()
        self.tls_verify_name_entry.set_placeholder_text(
            "Only enter a trusted certificate hostname; never inferred"
        )
        self.tls_verify_name_entry.connect("changed", self.on_connection_setting_changed)
        content.append(self.tls_verify_name_entry)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.connect_button = Gtk.Button(label="Connect")
        self.connect_button.connect("clicked", self.on_connect)
        buttons.append(self.connect_button)

        self.disconnect_button = Gtk.Button(label="Disconnect")
        self.disconnect_button.connect("clicked", self.on_disconnect)
        buttons.append(self.disconnect_button)
        content.append(buttons)

        note = Gtk.Label(
            label=(
                "Connect starts Xray with a SOCKS5 listener on 127.0.0.1:10890 only. "
                "It does not configure system proxies, routes, DNS, or firewall rules. "
                "Profiles stay in memory and credentials are never displayed."
            )
        )
        note.set_xalign(0)
        note.set_wrap(True)
        note.add_css_class("dim-label")
        content.append(note)

        window.set_child(content)
        self._update_controls()
        window.present()

    def on_import(self, _button):
        if self._connection_active():
            return
        try:
            profile = self.profile_manager.import_link(self.entry.get_text())
        except ProfileError as exc:
            self.feedback.set_text(f"Cannot import profile: {exc}")
            self.entry.set_text("")
            return

        row = self._create_profile_row(profile)
        self.profile_list.append(row)
        self.profile_list.select_row(row)
        self.feedback.set_text("Profile imported into memory.")
        self.entry.set_text("")

    def on_profile_selected(self, _list_box, row):
        if self._connection_active():
            self._restore_profile_selection()
            return
        if row is None:
            self.profile_manager.clear_selection()
        else:
            self.profile_manager.select(row.get_index())
        self._update_controls()

    def on_remove(self, _button):
        if self._connection_active():
            return
        row = self.profile_list.get_selected_row()
        if row is None:
            return

        self.profile_manager.select(row.get_index())
        self.profile_manager.remove_selected()
        selected_index = self.profile_manager.selected_index
        self.profile_list.remove(row)

        if selected_index is None:
            self.profile_list.select_row(None)
            self.profile_manager.clear_selection()
        else:
            self.profile_manager.select(selected_index)
            self.profile_list.select_row(self.profile_list.get_row_at_index(selected_index))
        self.feedback.set_text("Profile removed from memory.")
        self._update_controls()

    def on_connection_setting_changed(self, _widget):
        self._update_controls()

    def on_connect(self, _button):
        profile = self.profile_manager.selected_profile
        executable = self.xray_path_entry.get_text()
        if (
            profile is None
            or not self._is_executable_available(executable)
            or self._connection_active()
            or self._closing
        ):
            self._update_controls()
            return

        verify_name = self.tls_verify_name_entry.get_text()
        try:
            controller = self._controller_factory(
                profile,
                executable,
                tls_verify_name=verify_name if verify_name else None,
            )
            self._controller = controller
            listener = lambda state: GLib.idle_add(
                self._apply_connection_state, controller, state
            )
            self._controller_state_listener = listener
            controller.add_state_listener(listener)
            future = controller.connect()
        except ConnectionControllerError as exc:
            self._show_error(str(exc))
            self._update_controls()
            return
        except (OSError, ValueError):
            self._show_error("Could not initialize the local SOCKS proxy.")
            self._update_controls()
            return

        self._update_controls()
        self._watch_operation("connect", controller, future)

    def on_disconnect(self, _button):
        controller = self._controller
        if controller is None:
            return
        try:
            future = controller.disconnect()
        except ConnectionControllerError as exc:
            self._show_error(str(exc))
            return
        self._watch_operation("disconnect", controller, future)

    def on_close_request(self, _window):
        if self._close_allowed:
            self._close_allowed = False
            return False

        controller = self._controller
        if controller is None:
            return False

        self._closing = True
        self._update_controls()
        try:
            future = controller.shutdown()
        except ConnectionControllerError as exc:
            self._closing = False
            self._show_error(str(exc))
            self._update_controls()
            return True
        self._watch_operation("shutdown", controller, future)
        return True

    def _watch_operation(
        self,
        operation: str,
        controller: ConnectionController,
        future: Future[ConnectionState],
    ) -> None:
        def completed(done_future: Future[ConnectionState]) -> None:
            try:
                error = done_future.exception()
            except CancelledError as exc:
                error = exc
            GLib.idle_add(
                self._finish_operation, operation, controller, error
            )

        future.add_done_callback(completed)

    def _finish_operation(
        self,
        operation: str,
        controller: ConnectionController,
        error: BaseException | None,
    ) -> bool:
        if self._controller is not controller:
            if (
                operation == "shutdown"
                and self._closing
                and error is None
                and controller.state == ConnectionState.DISCONNECTED
            ):
                self._close_allowed = True
                self.window.close()
            return False

        if operation == "shutdown":
            if error is not None or controller.state != ConnectionState.DISCONNECTED:
                self._closing = False
                self._show_error(
                    controller.last_error or "Could not clean up the local SOCKS proxy."
                )
                self.feedback.set_text(
                    "Cleanup failed. The window is still open; retry Disconnect before closing."
                )
                self._update_controls()
                return False
            self._controller = None
            self._close_allowed = True
            self.window.close()
            return False

        if operation == "disconnect":
            if error is None and controller.state == ConnectionState.DISCONNECTED:
                self._apply_connection_state(
                    controller, ConnectionState.DISCONNECTED
                )
                if self._controller_state_listener is not None:
                    controller.remove_state_listener(self._controller_state_listener)
                controller.shutdown()
                self._controller = None
                self._controller_state_listener = None
            elif error is not None:
                self._show_error(
                    controller.last_error or "Could not clean up the local SOCKS proxy."
                )
        elif error is not None:
            self._show_error(
                controller.last_error or "Could not start the local SOCKS proxy."
            )

        self._update_controls()
        return False

    def _apply_connection_state(
        self, controller: ConnectionController, state: ConnectionState
    ) -> bool:
        if self._controller is not controller:
            return False
        state = controller.state
        if state == ConnectionState.DISCONNECTED:
            text = "Status: Disconnected"
        elif state == ConnectionState.CONNECTING:
            text = "Status: Connecting"
        elif state == ConnectionState.PROXY_READY:
            text = "Status: Local SOCKS5 proxy ready at 127.0.0.1:10890"
        elif state == ConnectionState.DISCONNECTING:
            text = "Status: Disconnecting"
        else:
            text = f"Status: Error — {controller.last_error or 'The local proxy is unavailable.'}"
        self.status.set_text(text)
        self._update_controls()
        return False

    def _connection_active(self) -> bool:
        return self._controller is not None

    def _update_controls(self) -> None:
        if self.window is None or not hasattr(self, "connect_button"):
            return
        state = (
            self._controller.state
            if self._controller is not None
            else ConnectionState.DISCONNECTED
        )
        active = self._controller is not None
        available = self._is_executable_available(self.xray_path_entry.get_text())
        selected = self.profile_manager.selected_profile is not None
        mutable = not active and not self._closing

        self.profile_list.set_sensitive(mutable)
        self.entry.set_sensitive(mutable)
        self.import_button.set_sensitive(mutable)
        self.xray_path_entry.set_sensitive(mutable)
        self.tls_verify_name_entry.set_sensitive(mutable)
        self.remove_button.set_sensitive(
            mutable and self.profile_list.get_selected_row() is not None
        )
        self.connect_button.set_sensitive(
            mutable and selected and available
        )
        self.disconnect_button.set_sensitive(
            not self._closing
            and self._controller is not None
            and state
            in {
                ConnectionState.CONNECTING,
                ConnectionState.PROXY_READY,
                ConnectionState.ERROR,
            }
        )

    def _restore_profile_selection(self) -> None:
        selected_index = self.profile_manager.selected_index
        row = (
            self.profile_list.get_row_at_index(selected_index)
            if selected_index is not None
            else None
        )
        self.profile_list.select_row(row)

    def _show_error(self, message: str) -> None:
        self.status.set_text(f"Status: Error — {message}")

    @staticmethod
    def _is_executable_available(path: str) -> bool:
        return bool(path and os.path.isfile(path) and os.access(path, os.X_OK))

    @staticmethod
    def _create_profile_row(profile: VlessProfile) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        details.set_margin_top(10)
        details.set_margin_bottom(10)
        details.set_margin_start(12)
        details.set_margin_end(12)

        name = Gtk.Label(label=profile.name)
        name.set_xalign(0)
        name.add_css_class("heading")
        details.append(name)

        address = Gtk.Label(label=f"{profile.server}:{profile.port}")
        address.set_xalign(0)
        address.add_css_class("dim-label")
        details.append(address)

        transport = Gtk.Label(label=f"{profile.transport.upper()} · TLS enabled")
        transport.set_xalign(0)
        transport.add_css_class("dim-label")
        details.append(transport)

        row.set_child(details)
        return row


def run():
    return VpnApplication().run(None)
