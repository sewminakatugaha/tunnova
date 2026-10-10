"""Unprivileged GTK4 prototype. No VPN services or routes are modified."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .profiles import parse_vless_uri, ProfileError


class VpnApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.tunnova.Tunnova")

    def do_activate(self):
        window = Gtk.ApplicationWindow(application=self)
        window.set_title("Tunnova — Prototype")
        window.set_default_size(530, 440)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(22)
        content.set_margin_bottom(22)
        content.set_margin_start(24)
        content.set_margin_end(24)

        heading = Gtk.Label(label="Tunnova")
        heading.set_xalign(0)
        heading.add_css_class("title-1")
        content.append(heading)

        subtitle = Gtk.Label(label="Open-source, beginner-friendly Linux VPN — early prototype")
        subtitle.set_xalign(0)
        subtitle.set_wrap(True)
        content.append(subtitle)

        self.status = Gtk.Label(label="Status: Not connected (networking not implemented yet)")
        self.status.set_xalign(0)
        self.status.set_wrap(True)
        content.append(self.status)

        description = Gtk.Label(label="Paste a VLESS + WebSocket + TLS link to inspect it locally.")
        description.set_xalign(0)
        description.set_wrap(True)
        content.append(description)

        self.entry = Gtk.PasswordEntry()
        self.entry.props.placeholder_text = "vless://..."
        self.entry.props.show_peek_icon = True
        content.append(self.entry)

        inspect_btn = Gtk.Button(label="Inspect Link (Offline)")
        inspect_btn.connect("clicked", self.on_inspect)
        content.append(inspect_btn)

        self.details = Gtk.Label(label="No profile imported")
        self.details.set_xalign(0)
        self.details.set_wrap(True)
        self.details.set_selectable(True)
        content.append(self.details)

        connect_btn = Gtk.Button(label="Connect — coming in a later milestone")
        connect_btn.set_sensitive(False)
        connect_btn.set_tooltip_text("Requires audited privileged networking service")
        content.append(connect_btn)

        note = Gtk.Label(label="Privacy: credentials are not saved or sent anywhere by this prototype.")
        note.set_xalign(0)
        note.set_wrap(True)
        note.add_css_class("dim-label")
        content.append(note)
        window.set_child(content)
        window.present()

    def on_inspect(self, _button):
        try:
            profile = parse_vless_uri(self.entry.get_text())
        except ProfileError as exc:
            self.details.set_text(f"Cannot import: {exc}")
            return
        self.details.set_text(
            f"Profile: {profile.name}\n"
            f"Server: {profile.server}:{profile.port}\n"
            f"Transport: WebSocket + TLS\n"
            f"TLS server name: {profile.server_name or '(not specified)'}\n"
            f"WebSocket path: {profile.ws_path}\n"
            "Authentication key: hidden"
        )
        self.entry.set_text("")  # Do not retain raw link in GUI input after import.


def run():
    return VpnApplication().run(None)
