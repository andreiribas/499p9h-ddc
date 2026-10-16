#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Turn GNOME's display mirroring off and use the monitor's biggest mode.

The monitor becomes the main screen at its biggest resolution; the laptop panel is
placed below it. usage: display-ultrawide.py [temporary|persistent] [WxH]
(default: temporary 5120x1440). Temporary changes last until the next hotplug or
reboot; "persistent" saves them in GNOME's display settings.
"""
import sys

from gi.repository import Gio, GLib

method = {"temporary": 1, "persistent": 2}[sys.argv[1] if len(sys.argv) > 1 else "temporary"]
want = sys.argv[2] if len(sys.argv) > 2 else "5120x1440"
w, h = map(int, want.split("x"))

bus = Gio.bus_get_sync(Gio.BusType.SESSION)
args = ("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig", "org.gnome.Mutter.DisplayConfig")
serial, monitors, _logical, _props = bus.call_sync(*args, "GetCurrentState", None, None, Gio.DBusCallFlags.NONE, -1).unpack()

ext = [m for m in monitors if m[0][1] == "PHL"]
panels = [m for m in monitors if m[0][1] != "PHL"]
if not ext:
    sys.exit("the monitor is not connected")
modes = [m for m in ext[0][1] if (m[1], m[2]) == (w, h)]
if not modes:
    sys.exit(f"the monitor does not offer {want}")
best = max(modes, key=lambda m: m[3])  # highest refresh rate at that size

logical = [(0, 0, 1.0, 0, True, [(ext[0][0][0], best[0], {})])]
for p in panels:
    cur = next(m for m in p[1] if m[6].get("is-current"))
    logical.append((0, h, 1.0, 0, False, [(p[0][0], cur[0], {})]))

params = GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (serial, method, logical, {}))
bus.call_sync(*args, "ApplyMonitorsConfig", params, None, Gio.DBusCallFlags.NONE, -1)
print(f"monitor: {ext[0][0][0]} at {best[0]} (main); " + ", ".join(f"{p[0][0]} below it" for p in panels))
