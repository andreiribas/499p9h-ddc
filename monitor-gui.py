#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Small GNOME app for the Philips 499P9H: PBP on/off, input source, and which
source goes on the left and right. It calls the `monitor` script next to it.

The monitor cannot reliably report which inputs it is showing (see NOTES.md),
so the source pickers show what was last applied from here, remembered in
~/.config/monitor-ctl/state.json. Only PBP on/off is read from the monitor.
"""
import json
import os
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
MONITOR = os.path.join(HERE, "monitor")
STATE_FILE = os.path.join(GLib.get_user_config_dir(), "monitor-ctl", "state.json")

# (label shown, name the `monitor` script understands)
SOURCES = [
    ("HDMI-1", "hdmi1"),
    ("HDMI-2", "hdmi2"),
    ("DisplayPort-1", "dp1"),
    ("DisplayPort-2", "dp2"),
]
DEFAULTS = {"single": "hdmi1", "left": "hdmi1", "right": "dp2"}


def run_monitor(*args):
    """Run the `monitor` script. Returns (ok, message)."""
    try:
        r = subprocess.run([MONITOR, *args], capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    text = (r.stdout + r.stderr).strip()
    return r.returncode == 0, text


def load_state():
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    return {k: saved.get(k, v) for k, v in DEFAULTS.items()}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


class MonitorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Monitor", default_width=460, default_height=560)
        self.state = load_state()
        self.busy = False
        self.updating = False  # True while the UI is being set from code

        # header: refresh button and a spinner while a command runs
        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Read PBP state from the monitor")
        refresh.connect("clicked", lambda _b: self.read_status())
        header.pack_start(refresh)
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)

        page = Adw.PreferencesPage()

        mode = Adw.PreferencesGroup(title="Mode")
        self.pbp_row = Adw.SwitchRow(
            title="Picture by picture (PBP)",
            subtitle="Two inputs side by side",
        )
        self.pbp_row.connect("notify::active", self.on_pbp_toggled)
        mode.add(self.pbp_row)
        page.add(mode)

        # PBP off: one screen, one source
        self.single_group = Adw.PreferencesGroup(title="Input")
        self.single_row = self.source_row("Source", "single")
        self.single_group.add(self.single_row)
        self.single_group.add(self.button_row(("Apply", self.on_apply_single, True)))
        page.add(self.single_group)

        # PBP on: a source for each window
        self.dual_group = Adw.PreferencesGroup(title="Windows")
        self.left_row = self.source_row("Left window", "left")
        self.right_row = self.source_row("Right window", "right")
        self.dual_group.add(self.left_row)
        self.dual_group.add(self.right_row)
        self.dual_group.add(
            self.button_row(("Swap", self.on_swap, False), ("Apply", self.on_apply_dual, True))
        )
        page.add(self.dual_group)

        note = Adw.PreferencesGroup()
        label = Gtk.Label(
            label="The monitor cannot report which inputs it shows. The pickers show what "
            "you last applied here; if you change inputs with the monitor's buttons, "
            "they may be out of date.",
            wrap=True,
            xalign=0,
            css_classes=["dim-label", "caption"],
        )
        note.add(label)
        page.add(note)

        self.content = page
        self.toasts = Adw.ToastOverlay(child=page)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        self.set_pbp(False)  # until the monitor answers
        self.read_status()

    # ---- building blocks -------------------------------------------------

    def source_row(self, title, key):
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([name for name, _ in SOURCES]))
        keys = [k for _, k in SOURCES]
        row.set_selected(keys.index(self.state[key]) if self.state[key] in keys else 0)
        return row

    def button_row(self, *buttons):
        row = Adw.ActionRow()
        box = Gtk.Box(spacing=8, halign=Gtk.Align.END, hexpand=True, margin_top=6, margin_bottom=6)
        for label, handler, suggested in buttons:
            b = Gtk.Button(label=label)
            if suggested:
                b.add_css_class("suggested-action")
            b.connect("clicked", lambda _b, h=handler: h())
            box.append(b)
        row.set_child(box)
        return row

    @staticmethod
    def key_of(row):
        return SOURCES[row.get_selected()][1]

    # ---- state / busy ----------------------------------------------------

    def set_pbp(self, on):
        """Show PBP as on/off without triggering the toggle handler."""
        self.updating = True
        self.pbp_row.set_active(on)
        self.updating = False
        self.single_group.set_visible(not on)
        self.dual_group.set_visible(on)

    def set_busy(self, busy):
        self.busy = busy
        self.content.set_sensitive(not busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def remember(self):
        self.state = {
            "single": self.key_of(self.single_row),
            "left": self.key_of(self.left_row),
            "right": self.key_of(self.right_row),
        }
        save_state(self.state)

    def run_async(self, args, done):
        """Run `monitor <args>` off the UI thread, then call done(ok, text)."""
        self.set_busy(True)

        def work():
            ok, text = run_monitor(*args)
            GLib.idle_add(finish, ok, text)

        def finish(ok, text):
            self.set_busy(False)
            done(ok, text)
            return False

        threading.Thread(target=work, daemon=True).start()

    # ---- actions ---------------------------------------------------------

    def read_status(self):
        def done(ok, text):
            if ok and "PBP: on" in text:
                self.set_pbp(True)
            elif ok and "PBP: off" in text:
                self.set_pbp(False)
            else:
                self.toast(text or "The monitor did not answer")

        self.run_async(["status"], done)

    def on_pbp_toggled(self, row, _param):
        if self.updating or self.busy:
            return
        on = row.get_active()

        def done(ok, text):
            if ok:
                self.set_pbp(on)
                self.toast("PBP turned " + ("on" if on else "off"))
            else:
                self.set_pbp(not on)  # put the switch back
                self.toast(text or "The monitor did not respond")

        self.run_async(["pbp", "on" if on else "off"], done)

    def on_apply_single(self):
        key = self.key_of(self.single_row)

        def done(ok, text):
            if ok:
                self.remember()
            self.toast(f"Input: {key}" if ok else (text or "Failed"))

        self.run_async(["input", key], done)

    def on_apply_dual(self):
        left, right = self.key_of(self.left_row), self.key_of(self.right_row)

        def done(ok, text):
            if ok:
                self.remember()
            self.toast(f"Left {left}, right {right}" if ok else (text or "Failed"))

        self.run_async(["layout", left, right], done)

    def on_swap(self):
        def done(ok, text):
            if ok:
                l, r = self.left_row.get_selected(), self.right_row.get_selected()
                self.left_row.set_selected(r)
                self.right_row.set_selected(l)
                self.remember()
            self.toast("Windows swapped" if ok else (text or "Failed"))

        self.run_async(["swap"], done)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.phl499p9h.guisettings")

    def do_activate(self):
        win = self.props.active_window or MonitorWindow(self)
        win.present()


if __name__ == "__main__":
    App().run(None)
