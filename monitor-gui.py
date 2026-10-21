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
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

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


# The sidebar's menus, in the order of the monitor's own menu: (key, title shown on the
# left, icon, [item, ...]). An item is
#   ("range", setting, label, maximum)  a slider,
#   ("choice", setting, label, [(label shown, value the `monitor` script takes)])  a dropdown,
#   ("mute",)  the mute switch.
CATEGORIES = [
    ("picture", "Picture", "display-brightness-symbolic", [
        ("range", "brightness", "Brightness", 100),
        ("range", "contrast", "Contrast", 100),
        ("range", "sharpness", "Sharpness", 100),
        ("choice", "smartresponse", "SmartResponse", [
            ("Off", "off"), ("Fast", "fast"), ("Faster", "faster"), ("Fastest", "fastest")]),
        ("choice", "smartcontrast", "SmartContrast", [("Off", "off"), ("On", "on")]),
        ("choice", "gamma", "Gamma", [(g, g) for g in ("1.8", "2.0", "2.2", "2.4", "2.6")]),
        ("choice", "scaling", "Picture format", [
            ("No scaling", "noscale"), ("Keep aspect ratio", "aspect"), ("Stretch to fill", "stretch")]),
        ("choice", "mode", "Display mode", [(n, n.lower()) for n in
            ("Standard", "Productivity", "Mixed", "Movie", "Games")]),
    ]),
    ("colour", "Colour", "preferences-color-symbolic", [
        ("choice", "preset", "Colour temperature", [(n, n.replace(" ", "").lower()) for n in
            ("sRGB", "5000 K", "6500 K", "7500 K", "8200 K", "9300 K", "11500 K", "User")]),
    ]),
    ("audio", "Audio", "audio-volume-high-symbolic", [
        ("range", "volume", "Volume", 100),
        ("mute",),
    ]),
    ("menu", "On-screen menu", "preferences-system-symbolic", [
        ("choice", "osd", "Monitor's menu", [("Shown", "on"), ("Hidden (buttons still work)", "off")]),
        ("choice", "language", "Language", [
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
# Settings the monitor itself warns about (turning them on can increase its energy
# consumption): the app asks first, unless that is switched off in Options. Imported
# controls get the same when their line in the controls file ends with "| energy".
ENERGY_SETTINGS = {"smartcontrast": "SmartContrast"}
# Sharpness is 0-100 like the monitor's menu, but the monitor only has 11 steps of it
SLIDER_STEPS = {"sharpness": 10}
# The status bar at the bottom is always there. While a change is being sent to the monitor
# it says what is being applied and the controls are read-only; "Done" stays this long
# after the last change, so that a fast one does not just flash by
STATUS_READY = "Ready"
BUSY_MIN_SECONDS = 0.6
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
        "energy_warning": bool(saved.get("energy_warning", True)),
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
        super().__init__(application=app, title="Monitor", default_width=900, default_height=640)
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
        self.busy_count = 0  # changes being applied; the controls are read-only while > 0
        self.busy_hide_source = None  # timer that hides the status bar after the last change
        self.energy_custom = {}  # imported setting -> (label, values that mean "off")
        self.choice_prev = {}  # dropdown setting -> index it had before the user's change

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

        # Menus on the left (like the monitor's own), the controls of the chosen one on the right
        self.pages = {"input": page}
        self.pages.update(self.build_category_pages())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for key, pg in self.pages.items():
            self.stack.add_named(pg, key)

        entries = [("input", "Input", "video-display-symbolic")]
        entries += [(key, title, icon) for key, title, icon, _items in CATEGORIES]
        entries.append(("imported", "Imported controls", "list-add-symbolic"))
        self.sidebar_keys = [key for key, _t, _i in entries]
        self.page_titles = {key: title for key, title, _i in entries}
        self.sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["navigation-sidebar"])
        for _key, title, icon in entries:
            box = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10, margin_start=6, margin_end=6)
            box.append(Gtk.Image.new_from_icon_name(icon))
            box.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            self.sidebar.append(box)
        self.sidebar.connect("row-selected", self.on_sidebar_row)
        side_view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=self.sidebar, vexpand=True))
        side_view.add_top_bar(Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Monitor")))
        sidebar_page = Adw.NavigationPage(title="Monitor", child=side_view)

        self.content_title = Adw.WindowTitle(title="Input")
        header.set_title_widget(self.content_title)
        self.banner = Adw.Banner(title=NO_LINK_SUBTITLE, revealed=False)
        self.toasts = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.content_page = Adw.NavigationPage(title="Input", child=view)

        self.split = Adw.NavigationSplitView(
            sidebar=sidebar_page, content=self.content_page, min_sidebar_width=190, max_sidebar_width=250
        )
        try:  # on a narrow window the menus and the controls take turns
            narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
            narrow.add_setter(self.split, "collapsed", True)
            self.add_breakpoint(narrow)
        except (AttributeError, TypeError, GLib.Error):
            pass
        outer = Adw.ToolbarView(content=self.split)
        outer.add_bottom_bar(self.build_status_bar())  # the whole window's width, always there
        self.set_content(outer)
        self.sidebar.select_row(self.sidebar.get_row_at_index(0))

        self.set_pbp(False)  # until the monitor answers
        self.read_status()
        self.reload_custom_controls()

        # Another computer (or the monitor's buttons) can change PBP, so keep
        # checking: periodically, and whenever this window gets focus again.
        self.arm_poll()
        self.connect("notify::is-active", lambda w, _p: w.is_active() and w.poll_status())

    # ---- picture page ----------------------------------------------------

    def build_category_pages(self):
        """One page per menu in CATEGORIES (plus the imported controls), as {key: page}. The
        widgets are remembered by setting name, so reads and changes find them."""
        self.scales = {}
        self.choice_rows = {}
        pages = {}
        for key, _title, _icon, items in CATEGORIES:
            page = Adw.PreferencesPage()
            group = Adw.PreferencesGroup()
            for item in items:
                if item[0] == "range":
                    group.add(self.slider_row(item[1], item[2], item[3]))
                elif item[0] == "choice":
                    _kind, name, label, options = item
                    row = Adw.ComboRow(title=label, model=Gtk.StringList.new([n for n, _ in options]))
                    row.connect("notify::selected", lambda r, _p, n=name, o=options: self.on_choice(n, r, o))
                    self.choice_rows[name] = (row, options)
                    group.add(row)
                else:  # mute
                    self.mute_row = Adw.SwitchRow(title="Mute")
                    self.mute_row.connect(
                        "notify::active",
                        lambda r, _p: self.picture_updating
                        or self.send_setting("mute", "on" if r.get_active() else "off"),
                    )
                    group.add(self.mute_row)
            page.add(group)
            if key == "colour":  # the gain belongs under the colour temperature it applies to
                self.gain_group = Adw.PreferencesGroup(
                    title="RGB gain", description="Used by the User colour temperature"
                )
                for name, title in PICTURE_GAIN:
                    self.gain_group.add(self.slider_row(name, title, 100))
                self.gain_group.set_visible(False)
                page.add(self.gain_group)
            pages[key] = page

        # Controls from a file the user imported (Options): the `monitor` script is the
        # one that reads and checks the file, so this only shows what it lists.
        imported = Adw.PreferencesPage()
        self.custom_group = Adw.PreferencesGroup(description="From the controls file imported in Options")
        imported.add(self.custom_group)
        pages["imported"] = imported
        return pages

    def on_sidebar_row(self, _listbox, row):
        if row is None:
            return
        key = self.sidebar_keys[row.get_index()]
        self.stack.set_visible_child_name(key)
        self.content_title.set_title(self.page_titles[key])
        self.content_page.set_title(self.page_titles[key])
        self.split.set_show_content(True)
        if key != "input":
            self.load_picture()

    def update_imported_row(self):
        """The 'Imported controls' menu only exists while there are imported controls."""
        row = self.sidebar.get_row_at_index(len(self.sidebar_keys) - 1)
        row.set_visible(bool(self.custom_rows))
        if not self.custom_rows and self.stack.get_visible_child_name() == "imported":
            self.sidebar.select_row(self.sidebar.get_row_at_index(0))

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
                self.energy_custom.pop(name, None)
            self.custom_rows = []
            for line in (text.splitlines() if ok else []):
                parts = [x.strip() for x in line.split("|")]
                if len(parts) not in (5, 6):
                    continue
                name, label, _code, kind, values = parts[:5]
                energy = len(parts) == 6 and parts[5] == "energy"
                if kind == "range" and values.isdigit():
                    row = self.slider_row(name, label, int(values))
                elif kind == "choice":
                    options = [(v.split("=")[0].replace("-", " ").capitalize(), v.split("=")[0])
                               for v in values.split()]
                    row = Adw.ComboRow(title=label, model=Gtk.StringList.new([n for n, _ in options]))
                    row.connect("notify::selected", lambda r, _p, n=name, o=options: self.on_choice(n, r, o))
                    self.choice_rows[name] = (row, options)
                    if energy:  # "off" and whatever the file numbers 0 need no question
                        zero = [v.split("=")[0] for v in values.split() if v.split("=")[1:] == ["0"]]
                        self.energy_custom[name] = (label, ("off", *zero))
                else:
                    continue
                self.custom_group.add(row)
                self.custom_rows.append((row, name))
            self.update_imported_row()
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
        step = SLIDER_STEPS.get(name, 1)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, top, step)
        scale.set_draw_value(False)
        scale.set_digits(0)
        scale.set_size_request(220, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.connect("value-changed", lambda sc, n=name: self.on_slider(n, sc))
        row = Adw.ActionRow(title=title)
        value = Gtk.Label(width_chars=4, xalign=1, css_classes=["numeric"])
        scale.connect("value-changed", lambda sc, lab=value: lab.set_label(str(int(sc.get_value()))))
        value.set_label(str(int(scale.get_value())))
        row.add_suffix(scale)
        row.add_suffix(value)
        self.scales[name] = scale
        return row

    def on_slider(self, name, scale):
        if self.picture_updating:
            return
        if name in self.debounce:
            GLib.source_remove(self.debounce[name])
        value = int(scale.get_value())
        step = SLIDER_STEPS.get(name, 1)
        if step > 1:  # land on the monitor's own steps
            snapped = int(round(value / step)) * step
            if snapped != value:
                self.picture_updating = True
                scale.set_value(snapped)
                self.picture_updating = False
                value = snapped

        def fire():
            if self.pointer_down():  # still dragging: the dialog would interrupt the drag
                return True
            del self.debounce[name]
            self.send_setting(name, value)
            return False

        self.debounce[name] = GLib.timeout_add(SLIDER_DELAY_MS, fire)

    def on_choice(self, name, row, options):
        self.update_gain_visibility()
        index = row.get_selected()
        if self.picture_updating:  # set from the monitor, not by the user
            self.choice_prev[name] = index
            return
        value = options[index][1]
        text = self.energy_warning_for(name, value)
        if text is None:
            self.choice_prev[name] = index
            self.send_setting(name, value)
            return
        prev = self.choice_prev.get(name, 0)

        def answer(_alert, response):
            if response == "continue":
                self.choice_prev[name] = index
                self.send_setting(name, value)
            else:  # put the dropdown back; nothing is sent
                GLib.idle_add(revert)

        def revert():
            self.picture_updating = True
            row.set_selected(prev)
            self.picture_updating = False
            return False

        alert = Adw.AlertDialog(heading="This can increase energy consumption", body=text)
        alert.add_response("cancel", "Cancel")
        alert.add_response("continue", "Turn on")
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")
        alert.connect("response", answer)
        alert.present(self)

    def energy_warning_for(self, name, value):
        """The question to ask before turning on `name`, or None when no question is needed
        (not an energy setting, switched off in Options, or the value means off)."""
        if not self.state.get("energy_warning", True):
            return None
        if name in ENERGY_SETTINGS:
            label, off_values = ENERGY_SETTINGS[name], ("off",)
        elif name in self.energy_custom:
            label, off_values = self.energy_custom[name]
        else:
            return None
        if value in off_values:
            return None
        return f"{label} can increase the monitor's energy consumption. Turn it on anyway?"

    def set_energy_warning(self, on):
        self.state["energy_warning"] = bool(on)
        save_state(self.state)

    def update_gain_visibility(self):
        row, options = self.choice_rows["preset"]
        self.gain_group.set_visible(options[row.get_selected()][1] == "user")

    def send_setting(self, name, value):
        """Send `monitor set <name> <value>` in the background. A newer value that
        arrives while one is being sent replaces any older waiting one."""
        self.pending[name] = value
        if name not in self.sending:
            self.sending.add(name)
            self.begin_busy(f"Applying {name.replace('-', ' ')}…")
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
                self.end_busy()
                self.toast(text or "The monitor did not respond")
                self.load_picture()  # show what the monitor really has
            elif name in self.pending:
                self.flush_setting(name)
            else:
                self.sending.discard(name)
                self.end_busy()
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
                self.banner.set_revealed(True)
                if announce:
                    self.toast(text or "The monitor did not answer")
                return False
            self.banner.set_revealed(False)
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
                        self.choice_prev[name] = values.index(value)
            self.picture_updating = False
            if announce:
                self.toast("Read from the monitor")
            return False

        threading.Thread(target=work, daemon=True).start()

    def refresh(self, announce=False):
        if self.stack.get_visible_child_name() != "input":
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
        self.refresh_lock()
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

    def build_status_bar(self):
        """The bar at the bottom of the window. It is always there: "Ready" when idle, a
        spinner and what is being applied while a change is sent to the monitor."""
        self.status_spinner = Gtk.Spinner(width_request=16, height_request=16)
        self.status_label = Gtk.Label(
            label=STATUS_READY, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END
        )
        row = Gtk.Box(spacing=10, margin_top=7, margin_bottom=7, margin_start=16, margin_end=16)
        row.append(self.status_spinner)
        row.append(self.status_label)
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar.append(Gtk.Separator())
        bar.append(row)
        return bar

    def refresh_lock(self):
        """The controls are read-only while a change is being applied (or a read runs)."""
        free = self.busy_count == 0 and not self.busy
        for page in self.pages.values():
            page.set_sensitive(free)

    def begin_busy(self, text):
        """Show `text` in the status bar and make the controls read-only."""
        self.busy_count += 1
        if self.busy_hide_source is not None:
            GLib.source_remove(self.busy_hide_source)
            self.busy_hide_source = None
        self.status_label.set_label(text)
        self.status_spinner.start()
        self.refresh_lock()

    def end_busy(self):
        self.busy_count = max(0, self.busy_count - 1)
        self.refresh_lock()
        if self.busy_count:
            return
        self.status_spinner.stop()
        self.status_label.set_label("Done")

        def idle():  # "Done" stays a moment, then the bar goes back to "Ready"
            self.busy_hide_source = None
            if self.busy_count == 0:
                self.status_label.set_label(STATUS_READY)
            return False

        self.busy_hide_source = GLib.timeout_add(int(BUSY_MIN_SECONDS * 1000), idle)

    @staticmethod
    def busy_text(args):
        """What the dialog says while `monitor <args>` runs."""
        if args[:2] == ["pbp", "on"]:
            return "Turning PBP on… (this can take several seconds)"
        if args[:2] == ["pbp", "off"]:
            return "Turning PBP off… (this can take several seconds)"
        return {
            "input": "Switching the input…",
            "swap": "Swapping the windows…",
            "controls": "Updating the controls…",
        }.get(args[0], "Applying the change…")

    def pointer_down(self):
        """True while the main mouse button is held (a slider is being dragged)."""
        try:
            surface = self.get_surface()
            _ok, _x, _y, mask = surface.get_device_position(self.get_display().get_default_seat().get_pointer())
            return bool(mask & Gdk.ModifierType.BUTTON1_MASK)
        except (AttributeError, TypeError):
            return False

    def run_async(self, args, done):
        """Run `monitor <args>` off the UI thread, then call done(ok, text). Anything but
        a read shows what is being applied in the status bar and locks the controls."""
        self.set_busy(True)
        changes = args[0] != "status"
        if changes:
            self.last_apply = time.monotonic()
            self.begin_busy(self.busy_text(args))

        def work():
            ok, text = run_monitor(*args)
            GLib.idle_add(finish, ok, text)

        def finish(ok, text):
            self.set_busy(False)
            if changes:
                self.last_apply = time.monotonic()  # settle time counts from the end
                self.end_busy()
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

        energy = Adw.PreferencesGroup(title="Energy")
        warn = Adw.SwitchRow(
            title="Warn before changing energy settings",
            subtitle="Ask first when turning on a setting that can increase the monitor's "
            "energy consumption, like SmartContrast.",
            active=self.state.get("energy_warning", True),
        )
        warn.connect("notify::active", lambda r, _p: self.set_energy_warning(r.get_active()))
        energy.add(warn)
        page.add(energy)

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
            if ok and self.stack.get_visible_child_name() != "input":
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
