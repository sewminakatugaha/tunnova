"""Unprivileged GTK4 prototype. No VPN services or routes are modified."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .profiles import ProfileError, ProfileManager, VlessProfile


class VpnApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.tunnova.Tunnova")
        self.profile_manager = ProfileManager()

    def do_activate(self):
        window = Gtk.ApplicationWindow(application=self)
        window.set_title("Tunnova — Prototype")
        window.set_default_size(530, 590)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
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

        description = Gtk.Label(label="Import a supported VLESS + WebSocket + TLS profile for this session.")
        description.set_xalign(0)
        description.set_wrap(True)
        content.append(description)

        self.entry = Gtk.PasswordEntry()
        self.entry.props.placeholder_text = "vless://..."
        self.entry.props.show_peek_icon = True
        content.append(self.entry)

        import_button = Gtk.Button(label="Import Profile")
        import_button.connect("clicked", self.on_import)
        content.append(import_button)

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

        connect_button = Gtk.Button(label="Connect — coming in a later milestone")
        connect_button.set_sensitive(False)
        connect_button.set_tooltip_text("Requires audited privileged networking service")
        content.append(connect_button)

        note = Gtk.Label(
            label="Privacy: profiles stay in memory for this session only; credentials are not saved or sent."
        )
        note.set_xalign(0)
        note.set_wrap(True)
        note.add_css_class("dim-label")
        content.append(note)
        window.set_child(content)
        window.present()

    def on_import(self, _button):
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
        if row is None:
            self.profile_manager.clear_selection()
            self.remove_button.set_sensitive(False)
            return
        self.profile_manager.select(row.get_index())
        self.remove_button.set_sensitive(True)

    def on_remove(self, _button):
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
            self.remove_button.set_sensitive(False)
        else:
            self.profile_manager.select(selected_index)
            self.profile_list.select_row(self.profile_list.get_row_at_index(selected_index))
        self.feedback.set_text("Profile removed from memory.")

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
