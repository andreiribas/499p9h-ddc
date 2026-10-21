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
import time

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
# After an Apply the monitor can keep answering this computer for a few seconds
# before it switches away, so the "it answers, so it's on my input" inference below
# waits this long.
SETTLE_SECONDS = 20
# The monitor only answers the computer whose input it is showing.
NO_LINK_SUBTITLE = "Not answering: the monitor is probably showing another input"


# Picture page: (setting the `monitor` script knows, label, maximum) for the sliders,
# and (setting, label, [(label shown, value the script takes)]) for the dropdowns.
PICTURE_RANGES = [
    ("brightness", "Brightness", 100),
    ("contrast", "Contrast", 100),
    ("sharpness", "Sharpness", 10),
]
PICTURE_CHOICES = [
    ("Colour", [
        ("preset", "Colour temperature", [(n, n.replace(" ", "").lower()) for n in
            ("sRGB", "5000 K", "6500 K", "7500 K", "8200 K", "9300 K", "11500 K", "User")]),
        ("gamma", "Gamma", [(g, g) for g in ("1.8", "2.0", "2.2", "2.4", "2.6")]),
        ("mode", "Display mode", [(n, n.lower()) for n in
            ("Standard", "Productivity", "Mixed", "Movie", "Games")]),
    ]),
    ("Screen", [
        ("scaling", "Scaling", [("No scaling", "noscale"), ("Keep aspect ratio", "aspect"),
                                ("Stretch to fill", "stretch")]),
    ]),
    ("On-screen menu", [
        ("osd", "Monitor's menu", [("Shown", "on"), ("Hidden (buttons still work)", "off")]),
        ("language", "Language", [
            ("English", "english"), ("French", "french"), ("German", "german"),
            ("Italian", "italian"), ("Spanish", "spanish"), ("Portuguese", "portuguese"),
            ("Portuguese (Brazil)", "ptbr"), ("Dutch", "dutch"), ("Swedish", "swedish"),
            ("Finnish", "finnish"), ("Polish", "polish"), ("Czech", "czech"),
            ("Hungarian", "hungarian"), ("Greek", "greek"), ("Turkish", "turkish"),
            ("Russian", "russian"), ("Ukrainian", "ukrainian"), ("Japanese", "japanese"),
            ("Korean", "korean"), ("Chinese (simplified)", "schinese"),
            ("Chinese (traditional)", "tchinese")]),
    ]),
]
# RGB gain: the monitor applies it with the "user" colour preset
PICTURE_GAIN = [("gain-red", "Red"), ("gain-green", "Green"), ("gain-blue", "Blue")]
# a slider sends its value this long after it stops moving
SLIDER_DELAY_MS = 250

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
        self.last_apply = 0.0  # time.monotonic() of the last command that changed the monitor
        self.debounce = {}  # picture setting -> timer id, while a slider waits to send
        self.pending = {}  # picture setting -> newest value not sent yet
        self.sending = set()  # picture settings with a command running
        self.loading_picture = False
        self.custom_rows = []  # (row, setting name) of the imported controls on the Picture page

        # header: refresh button and a spinner while a command runs
        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Read PBP state from the monitor")
        refresh.connect("clicked", lambda _b: self.refresh(announce=True))
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
        # The monitor has no reliable code for the right window's input: it is set in
        # the monitor's own menu and remembered there. This picker only records what
        # it is set to, so Swap and the display can keep track.
        self.right_row.set_subtitle("Set in the monitor's menu; pick what it is set to, for Swap")
        self.right_row.connect("notify::selected", lambda _r, _p: self.remember())
        self.dual_group.add(self.left_row)
        self.dual_group.add(self.right_row)
        self.dual_group.add(
            self.button_row(("Swap", self.on_swap, False), ("Apply", self.on_apply_dual, True))
        )
        page.add(self.dual_group)

        note = Adw.PreferencesGroup()
        label = Gtk.Label(
            label="The monitor cannot report which inputs it shows. The pickers show what "
            "you last applied here; if you change inputs with the monitor's buttons or "
            "from the other computer, they may be out of date. The monitor only answers the "
            "computer it is showing, so when it answers here with PBP off, Source shows "
            "this computer's input.",
            wrap=True,
            xalign=0,
            css_classes=["dim-label", "caption"],
        )
        note.add(label)
        page.add(note)

        self.content = page
        self.stack = Adw.ViewStack()
        self.stack.add_titled_with_icon(page, "input", "Input", "video-display-symbolic")
        self.stack.add_titled_with_icon(
            self.build_picture_page(), "picture", "Picture", "preferences-desktop-appearance-symbolic"
        )
        self.stack.connect("notify::visible-child", lambda *_a: self.on_page_changed())
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        self.toasts = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        self.set_pbp(False)  # until the monitor answers
        self.read_status()
        self.reload_custom_controls()

        # Another computer (or the monitor's buttons) can change PBP, so keep
        # checking: periodically, and whenever this window gets focus again.
        self.arm_poll()
        self.connect("notify::is-active", lambda w, _p: w.is_active() and w.poll_status())

    # ---- picture page ----------------------------------------------------

    def build_picture_page(self):
        page = Adw.PreferencesPage()
        self.picture_group = Adw.PreferencesGroup(title="Picture")
        self.scales = {}
        for name, title, top in PICTURE_RANGES:
            self.picture_group.add(self.slider_row(name, title, top))
        page.add(self.picture_group)

        self.choice_rows = {}
        for group_title, choices in PICTURE_CHOICES:
            group = Adw.PreferencesGroup(title=group_title)
            for name, title, options in choices:
                row = Adw.ComboRow(title=title, model=Gtk.StringList.new([n for n, _ in options]))
                row.connect("notify::selected", lambda r, _p, n=name, o=options: self.on_choice(n, r, o))
                self.choice_rows[name] = (row, options)
                group.add(row)
            page.add(group)
            if group_title == "Colour":  # the gain belongs right under the colour settings
                self.gain_group = Adw.PreferencesGroup(
                    title="RGB gain", description="Used by the User colour temperature"
                )
                for name, title in PICTURE_GAIN:
                    self.gain_group.add(self.slider_row(name, title, 100))
                self.gain_group.set_visible(False)
                page.add(self.gain_group)

        audio = Adw.PreferencesGroup(title="Audio")
        audio.add(self.slider_row("volume", "Volume", 100))
        self.mute_row = Adw.SwitchRow(title="Mute")
        self.mute_row.connect(
            "notify::active",
            lambda r, _p: self.picture_updating or self.send_setting("mute", "on" if r.get_active() else "off"),
        )
        audio.add(self.mute_row)
        page.add(audio)

        # Controls from a file the user imported (Options): the `monitor` script is the
        # one that reads and checks the file, so this only shows what it lists.
        self.custom_group = Adw.PreferencesGroup(
            title="Imported controls", description="From the controls file imported in Options"
        )
        self.custom_group.set_visible(False)
        page.add(self.custom_group)
        return page

    def reload_custom_controls(self):
        """Ask `monitor controls list` for the imported controls and (re)build their rows."""

        def work():
            ok, text = run_monitor("controls", "list")
            GLib.idle_add(finish, ok, text)

        def finish(ok, text):
            for row, name in self.custom_rows:
                self.custom_group.remove(row)
                self.scales.pop(name, None)
                self.choice_rows.pop(name, None)
            self.custom_rows = []
            for line in (text.splitlines() if ok else []):
                parts = [x.strip() for x in line.split("|")]
                if len(parts) != 5:
                    continue
                name, label, _code, kind, values = parts
                if kind == "range" and values.isdigit():
                    row = self.slider_row(name, label, int(values))
                elif kind == "choice":
                    options = [(v.split("=")[0].replace("-", " ").capitalize(), v.split("=")[0])
                               for v in values.split()]
                    row = Adw.ComboRow(title=label, model=Gtk.StringList.new([n for n, _ in options]))
                    row.connect("notify::selected", lambda r, _p, n=name, o=options: self.on_choice(n, r, o))
                    self.choice_rows[name] = (row, options)
                else:
                    continue
                self.custom_group.add(row)
                self.custom_rows.append((row, name))
            self.custom_group.set_visible(bool(self.custom_rows))
            self.load_picture()
            return False

        threading.Thread(target=work, daemon=True).start()

    def import_controls(self):
        """Pick a controls file, show what it contains, and import it if the user agrees."""
        chooser = Gtk.FileDialog(title="Import controls file")

        def chosen(dialog, result):
            try:
                path = dialog.open_finish(result).get_path()
            except GLib.Error:
                return  # cancelled
            self.check_controls_file(path)

        chooser.open(self, None, chosen)

    def check_controls_file(self, path):
        def work():
            ok, text = run_monitor("controls", "check", path)
            GLib.idle_add(finish, ok, text)

        def finish(ok, text):
            if not ok:
                alert = Adw.AlertDialog(heading="This file cannot be imported", body=text or "Unknown error")
                alert.add_response("ok", "OK")
                alert.present(self)
                return False
            count = len([l for l in text.splitlines() if " (code " in l])
            alert = Adw.AlertDialog(
                heading=f"Import {count} control{'s' if count != 1 else ''}?",
                body=text + "\n\nThese controls write to the monitor using the codes in the file, "
                "so import only files you trust: a wrong code can change settings you did not "
                "expect. Resets and power codes are refused. Importing replaces controls "
                "imported before.",
            )
            alert.add_response("cancel", "Cancel")
            alert.add_response("import", "Import")
            alert.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
            alert.set_default_response("cancel")
            alert.connect("response", lambda _a, r: r == "import" and self.do_import_controls(path))
            alert.present(self)
            return False

        threading.Thread(target=work, daemon=True).start()

    def do_import_controls(self, path):
        def done(ok, text):
            self.toast(text if text else ("Imported" if ok else "Import failed"))
            if ok:
                self.reload_custom_controls()

        self.run_async(["controls", "import", path], done)

    def remove_controls(self):
        def done(ok, text):
            self.toast("Imported controls removed" if ok else (text or "Failed"))
            self.reload_custom_controls()

        self.run_async(["controls", "remove"], done)

    picture_updating = False  # True while the widgets are being set from the monitor

    def slider_row(self, name, title, top):
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, top, 1)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_digits(0)
        scale.set_size_request(220, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.connect("value-changed", lambda sc, n=name: self.on_slider(n, sc))
        row = Adw.ActionRow(title=title)
        row.add_suffix(scale)
        self.scales[name] = scale
        return row

    def on_slider(self, name, scale):
        if self.picture_updating:
            return
        if name in self.debounce:
            GLib.source_remove(self.debounce[name])
        value = int(scale.get_value())

        def fire():
            del self.debounce[name]
            self.send_setting(name, value)
            return False

        self.debounce[name] = GLib.timeout_add(SLIDER_DELAY_MS, fire)

    def on_choice(self, name, row, options):
        self.update_gain_visibility()
        if not self.picture_updating:
            self.send_setting(name, options[row.get_selected()][1])

    def update_gain_visibility(self):
        row, options = self.choice_rows["preset"]
        self.gain_group.set_visible(options[row.get_selected()][1] == "user")

    def send_setting(self, name, value):
        """Send `monitor set <name> <value>` in the background. A newer value that
        arrives while one is being sent replaces any older waiting one."""
        self.pending[name] = value
        if name not in self.sending:
            self.sending.add(name)
            self.flush_setting(name)

    def flush_setting(self, name):
        value = self.pending.pop(name)

        def work():
            ok, text = run_monitor("set", name, str(value))
            GLib.idle_add(finish, ok, text)

        def finish(ok, text):
            self.last_apply = time.monotonic()
            if not ok:
                self.pending.pop(name, None)
                self.sending.discard(name)
                self.toast(text or "The monitor did not respond")
                self.load_picture()  # show what the monitor really has
            elif name in self.pending:
                self.flush_setting(name)
            else:
                self.sending.discard(name)
            return False

        threading.Thread(target=work, daemon=True).start()

    def load_picture(self, announce=False):
        """Read all picture settings from the monitor into the page. Skipped while
        the user is changing one, so a slow read never moves a slider back."""
        if self.loading_picture or self.debounce or self.pending or self.sending:
            return
        self.loading_picture = True

        def work():
            ok, text = run_monitor("picture")
            GLib.idle_add(finish, ok, text)

        def finish(ok, text):
            self.loading_picture = False
            if self.debounce or self.pending or self.sending:
                return False  # the user moved something meanwhile: it wins
            if not ok:
                self.picture_group.set_description(NO_LINK_SUBTITLE)
                if announce:
                    self.toast(text or "The monitor did not answer")
                return False
            self.picture_group.set_description("")
            self.picture_updating = True
            for line in text.splitlines():
                parts = line.split()
                if len(parts) < 2 or parts[1] == "n/a":
                    continue
                name, value = parts[0], parts[1]
                if name in self.scales and value.isdigit():
                    self.scales[name].set_value(int(value))
                elif name == "mute":
                    self.mute_row.set_active(value == "on")
                elif name in self.choice_rows:
                    row, options = self.choice_rows[name]
                    values = [v for _, v in options]
                    if value in values:
                        row.set_selected(values.index(value))
            self.picture_updating = False
            if announce:
                self.toast("Read from the monitor")
            return False

        threading.Thread(target=work, daemon=True).start()

    def on_page_changed(self):
        if self.stack.get_visible_child_name() == "picture":
            self.load_picture()

    def refresh(self, announce=False):
        if self.stack.get_visible_child_name() == "picture":
            self.load_picture(announce=announce)
        else:
            self.read_status(announce=announce)

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
        if args[0] != "status":
            self.last_apply = time.monotonic()

        def work():
            ok, text = run_monitor(*args)
            GLib.idle_add(finish, ok, text)

        def finish(ok, text):
            self.set_busy(False)
            if args[0] != "status":
                self.last_apply = time.monotonic()  # settle time counts from the end
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
                self.infer_source(False)
                if announce:
                    self.toast("Read from the monitor: PBP is off")
            else:
                self.pbp_row.set_subtitle(NO_LINK_SUBTITLE)
                self.toast(text or "The monitor did not answer")

        self.run_async(["status"], done)

    def infer_source(self, pbp_on):
        """The monitor only answers the computer whose input it is showing. So if it
        answers us and PBP is off, the screen is on this computer's own input: show
        that in the Source picker, instead of whatever was last applied from here."""
        if pbp_on or time.monotonic() - self.last_apply < SETTLE_SECONDS:
            return
        keys = [k for _, k in self.visible_sources()]
        mine = self.state["connected_to"]
        if mine in keys and self.key_of(self.single_row) != mine:
            self.single_row.set_selected(keys.index(mine))
            self.remember()

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

        extra = Adw.PreferencesGroup(
            title="Extra controls",
            description="Add controls this app does not have, from a file listing them "
            "(one per line: name | label | code | kind | values). Useful for another monitor "
            "or TV: ./walk.sh finds the codes. See the README for the format.",
        )
        imp = Adw.ActionRow(title="Import controls file…", activatable=True)
        imp.add_suffix(Gtk.Image.new_from_icon_name("document-open-symbolic"))
        imp.connect("activated", lambda _r: self.import_controls())
        extra.add(imp)
        rem = Adw.ActionRow(title="Remove imported controls", activatable=True)
        rem.set_sensitive(bool(self.custom_rows))
        rem.connect("activated", lambda _r: self.remove_controls())
        extra.add(rem)
        page.add(extra)

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
                self.infer_source(False)
            else:
                self.pbp_row.set_subtitle(NO_LINK_SUBTITLE)
            if ok and self.stack.get_visible_child_name() == "picture":
                self.load_picture()  # settings changed with the monitor's buttons
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

        # Turning PBP on splits the screen; the right half shows the input set in the
        # monitor's menu (the monitor remembers it). Only the left input is sent.
        args = ["pbp", "on", self.key_of(self.left_row)] if on else ["pbp", "off"]
        self.run_async(args, done)

    def on_apply_single(self):
        key = self.key_of(self.single_row)

        def done(ok, text):
            if ok:
                self.remember()
            self.toast(f"Input: {key}" if ok else (text or "Failed"))

        self.run_async(["input", key], done)

    def on_apply_dual(self):
        left = self.key_of(self.left_row)

        def done(ok, text):
            if ok:
                self.remember()
            self.toast(f"Left window: {left}" if ok else (text or "Failed"))

        self.run_async(["input", left], done)

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
