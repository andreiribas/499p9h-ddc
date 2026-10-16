#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Small GNOME app for the Philips 499P9H: PBP on/off, input source, and which
source goes on the left and right. It calls the `monitor` script next to it.

The monitor cannot reliably report which inputs it is showing (see NOTES.md),
so the source pickers show what was last applied from here, remembered in
~/.config/monitor-ctl/state.json. Only PBP on/off is read from the monitor.
"""
import glob
import json
import os
import re
import subprocess
import sys
import threading

try:
    import gi
except ModuleNotFoundError:
    # Started with a Python that lacks PyGObject (e.g. Homebrew's python3):
    # the GNOME libraries live in the system Python, so run under that one.
    if os.path.realpath(sys.executable) != os.path.realpath("/usr/bin/python3") and os.path.exists(
        "/usr/bin/python3"
    ):
        os.execv("/usr/bin/python3", ["/usr/bin/python3", *sys.argv])
    sys.exit(
        "monitor-gui: PyGObject (GTK 4 + libadwaita) is not installed. "
        "Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
    )

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
MONITOR = os.path.join(HERE, "monitor")
STATE_FILE = os.path.join(GLib.get_user_config_dir(), "monitor-ctl", "state.json")

# (label shown, name the `monitor` script understands). The labels are the ones in
# the monitor's own menu. Over DDC the monitor reports its USB-C port as
# "DisplayPort-1" (VCP 60 = 0x0f) and the DisplayPort connector as
# "DisplayPort-2" (0x10), and numbers the HDMI ports the other way round: menu
# "HDMI 2" is 0x11 and menu "HDMI 1" is 0x12. All checked by plugging a laptop in
# and switching to each value.
SOURCES = [
    ("HDMI-1", "hdmi1"),
    ("HDMI-2", "hdmi2"),
    ("USB-C", "usbc"),
    ("DisplayPort", "dp"),
]
# names saved by older versions of the app (same VCP values, different labels)
OLD_NAMES = {"dp1": "usbc", "dp2": "dp"}
# Until names_version 2 the HDMI inputs were numbered as DDC numbers them, which is
# the other way round from the monitor's menu; saved choices are swapped once so
# they keep pointing at the same physical input.
SWAP_HDMI = {"hdmi1": "hdmi2", "hdmi2": "hdmi1"}
NAMES_VERSION = 2
# poll_seconds: how often PBP is re-read in the background, so changes made from
# another computer or the monitor's buttons show up (0 = never)
DEFAULT_POLL_SECONDS = 5
PBP_SUBTITLE = "Two inputs side by side"
# The monitor only answers the computer whose input it is showing.
NO_LINK_SUBTITLE = "Not answering: the monitor is probably showing another input"


# The monitor handles one DDC command at a time: a background check must not
# overlap with a button press.
_monitor_lock = threading.Lock()


def run_monitor(*args):
    """Run the `monitor` script. Returns (ok, message)."""
    try:
        with _monitor_lock:
            r = subprocess.run([MONITOR, *args], capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    text = (r.stdout + r.stderr).strip()
    return r.returncode == 0, text


def detect_connection(root="/sys/class/drm"):
    """Guess which monitor input this computer is plugged into, from the video
    connector the 499P9 is on: HDMI -> HDMI-1; DisplayPort or USB-C -> USB-C
    (laptops rarely have a full-size DisplayPort). Only a guess: the Options
    menu can override it. Returns None if the monitor isn't found, which also
    happens while the monitor shows another input (this cable then reads as
    disconnected): so the first successful guess is saved."""
    for status in glob.glob(os.path.join(root, "card*-*/status")):
        conn = os.path.dirname(status)
        name = os.path.basename(conn).split("-", 1)[1]  # e.g. HDMI-A-1, DP-3
        try:
            with open(status) as f:
                if f.read().strip() != "connected":
                    continue
            with open(os.path.join(conn, "edid"), "rb") as f:
                edid = f.read()
        except OSError:
            continue
        if b"PHL 499P9" not in edid:
            continue
        if name.startswith("HDMI"):
            return "hdmi1"
        if name.startswith(("DP-", "USB")):
            return "usbc"
    return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    saved = {k: OLD_NAMES.get(v, v) if isinstance(v, str) else v for k, v in saved.items()}
    if saved.get("names_version") != NAMES_VERSION:
        for k in ("single", "left", "right", "connected_to"):
            if isinstance(saved.get(k), str):
                saved[k] = SWAP_HDMI.get(saved[k], saved[k])
        if isinstance(saved.get("visible"), list):
            saved["visible"] = [SWAP_HDMI.get(x, x) for x in saved["visible"]]
    keys = [k for _, k in SOURCES]
    # Where this computer is plugged in: what the user set, else a guess. The
    # pickers start there, because the monitor can't tell us which input it shows.
    this = saved.get("connected_to")
    detected = None
    if this not in keys:
        detected = detect_connection()
        this = detected or "hdmi1"
    # Which inputs the pickers offer: what the user ticked in Options (default all),
    # and always this computer's own input.
    visible = saved.get("visible")
    if not isinstance(visible, list) or not [k for k in keys if k in visible]:
        visible = list(keys)
    visible = [k for k in keys if k in visible or k == this]
    state = {
        "names_version": NAMES_VERSION,
        "connected_to": this,
        "visible": visible,
        "single": saved.get("single", this),
        "left": saved.get("left", this),
        "right": saved.get("right", "hdmi1" if this == "usbc" else "usbc"),
        "poll_seconds": saved.get("poll_seconds", DEFAULT_POLL_SECONDS),
    }
    try:
        state["poll_seconds"] = max(0, min(300, int(state["poll_seconds"])))
    except (TypeError, ValueError):
        state["poll_seconds"] = DEFAULT_POLL_SECONDS
    if detected:
        save_state(state)  # keep the guess for when the monitor shows another input
    return state


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
        self.polling = False  # a background check is running
        self.poll_source = None  # id of the repeating background-check timer

        # header: refresh button and a spinner while a command runs
        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Read PBP state from the monitor")
        refresh.connect("clicked", lambda _b: self.read_status(announce=True))
        header.pack_start(refresh)
        menu = Gio.Menu()
        menu.append("Options", "win.options")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True))
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)
        options = Gio.SimpleAction.new("options", None)
        options.connect("activate", lambda _a, _p: self.show_options())
        self.add_action(options)

        page = Adw.PreferencesPage()

        mode = Adw.PreferencesGroup(title="Mode")
        self.pbp_row = Adw.SwitchRow(
            title="Picture by picture (PBP)",
            subtitle=PBP_SUBTITLE,
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

        # Another computer (or the monitor's buttons) can change PBP, so keep
        # checking: periodically, and whenever this window gets focus again.
        self.arm_poll()
        self.connect("notify::is-active", lambda w, _p: w.is_active() and w.poll_status())

    # ---- building blocks -------------------------------------------------

    def visible_sources(self):
        """(label, key) of the inputs the pickers offer, in the monitor's order."""
        return [(n, k) for n, k in SOURCES if k in self.state["visible"]]

    def source_row(self, title, key):
        vis = self.visible_sources()
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([n for n, _ in vis]))
        keys = [k for _, k in vis]
        row.set_selected(keys.index(self.state[key]) if self.state[key] in keys else 0)
        return row

    def set_visible_inputs(self, keys):
        """Change which inputs the three pickers offer, keeping each one's choice
        when it is still offered."""
        rows = (self.single_row, self.left_row, self.right_row)
        current = [self.key_of(r) for r in rows]
        self.state["visible"] = keys
        vis = self.visible_sources()
        labels, vkeys = [n for n, _ in vis], [k for _, k in vis]
        for row, cur in zip(rows, current):
            row.set_model(Gtk.StringList.new(labels))
            row.set_selected(vkeys.index(cur) if cur in vkeys else 0)
        self.remember()

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

    def key_of(self, row):
        return self.visible_sources()[row.get_selected()][1]

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
        self.state.update(
            single=self.key_of(self.single_row),
            left=self.key_of(self.left_row),
            right=self.key_of(self.right_row),
        )
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

    def read_status(self, announce=False):
        def done(ok, text):
            if ok and "PBP: on" in text:
                self.set_pbp(True)
                self.pbp_row.set_subtitle(PBP_SUBTITLE)
                if announce:
                    self.toast("Read from the monitor: PBP is on")
            elif ok and "PBP: off" in text:
                self.set_pbp(False)
                self.pbp_row.set_subtitle(PBP_SUBTITLE)
                if announce:
                    self.toast("Read from the monitor: PBP is off")
            else:
                self.pbp_row.set_subtitle(NO_LINK_SUBTITLE)
                self.toast(text or "The monitor did not answer")

        self.run_async(["status"], done)

    def arm_poll(self):
        """(Re)start the background-check timer with the current interval."""
        if self.poll_source is not None:
            GLib.source_remove(self.poll_source)
            self.poll_source = None
        seconds = self.state["poll_seconds"]
        if seconds > 0:
            self.poll_source = GLib.timeout_add_seconds(seconds, self.poll_status)

    def set_poll_seconds(self, seconds):
        self.state["poll_seconds"] = seconds
        save_state(self.state)
        self.arm_poll()

    def set_connected_to(self, key):
        """The user says where this computer is plugged in: show it in the pickers too."""
        self.state["connected_to"] = key
        if key not in self.state["visible"]:  # this computer's input is always offered
            self.set_visible_inputs([k for _, k in SOURCES if k in self.state["visible"] or k == key])
        keys = [k for _, k in self.visible_sources()]
        self.single_row.set_selected(keys.index(key))
        self.left_row.set_selected(keys.index(key))
        if self.right_row.get_selected() == keys.index(key):  # not the same input on both halves
            others = [i for i, k in enumerate(keys) if k != key]
            if others:
                self.right_row.set_selected(others[0])
        self.remember()

    def show_options(self):
        dialog = Adw.PreferencesDialog(title="Options")
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Background refresh",
            description="How often the app re-reads the PBP state from the monitor, "
            "so changes made from another computer or the monitor's buttons show up. "
            "0 turns it off (the refresh button still works).",
        )
        row = Adw.SpinRow.new_with_range(0, 300, 1)
        row.set_title("Refresh interval")
        row.set_subtitle("seconds")
        row.set_value(self.state["poll_seconds"])
        row.connect("notify::value", lambda r, _p: self.set_poll_seconds(int(r.get_value())))
        group.add(row)
        page.add(group)

        here = Adw.PreferencesGroup(
            title="This computer",
            description="Which monitor input this computer is plugged into. The monitor can't "
            "report it, so the app guesses from the video port; correct it here if it's wrong. "
            "The source pickers start there.",
        )
        keys = [k for _, k in SOURCES]
        conn = Adw.ComboRow(title="Connected to", model=Gtk.StringList.new([n for n, _ in SOURCES]))
        conn.set_selected(keys.index(self.state["connected_to"]))
        here.add(conn)
        page.add(here)

        shown = Adw.PreferencesGroup(
            title="Inputs shown",
            description="The pickers offer only the inputs ticked here. This computer's own "
            "input is always shown.",
        )
        switches = {}

        def refresh_switches():
            for k, sw in switches.items():
                mine = k == self.state["connected_to"]
                sw.set_sensitive(not mine)
                if mine:
                    sw.set_active(True)

        def on_switch(_row, _p):
            self.set_visible_inputs([k for _, k in SOURCES if switches[k].get_active()])

        for name, k in SOURCES:
            sw = Adw.SwitchRow(title=name, active=k in self.state["visible"])
            switches[k] = sw
            shown.add(sw)
        refresh_switches()
        for sw in switches.values():
            sw.connect("notify::active", on_switch)
        page.add(shown)

        def on_connected(r, _p):
            self.set_connected_to(keys[r.get_selected()])
            refresh_switches()

        conn.connect("notify::selected", on_connected)

        dialog.add(page)
        dialog.present(self)

    def poll_status(self):
        """Quietly re-read PBP from the monitor (no spinner, no toast).
        Returns True so it can also be used as a repeating timer."""
        if self.busy or self.polling:
            return True
        self.polling = True

        def work():
            ok, text = run_monitor("status")
            GLib.idle_add(finish, ok, text)

        def finish(ok, text):
            self.polling = False
            if self.busy:  # the user started something meanwhile: it wins
                return False
            if ok and "PBP: on" in text:
                self.set_pbp(True)
                self.pbp_row.set_subtitle(PBP_SUBTITLE)
            elif ok and "PBP: off" in text:
                self.set_pbp(False)
                self.pbp_row.set_subtitle(PBP_SUBTITLE)
            else:
                self.pbp_row.set_subtitle(NO_LINK_SUBTITLE)
            return False

        threading.Thread(target=work, daemon=True).start()
        return True

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
