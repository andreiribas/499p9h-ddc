#!/bin/sh
# SPDX-License-Identifier: MIT
# Install / uninstall the `monitor` command and the "Monitor Control" app for
# the current user (no root needed). Files are copied, so the checkout can be
# deleted afterwards.
#
#   ./install.sh               install into ~/.local
#   PREFIX=/usr/local ./install.sh   install somewhere else (may need sudo)
#   ./install.sh --uninstall
set -eu

PREFIX="${PREFIX:-$HOME/.local}"
APP_ID=org.phl499p9h.guisettings
LIB="$PREFIX/share/499p9h-ddc"
BIN="$PREFIX/bin"
DESKTOP="$PREFIX/share/applications/$APP_ID.desktop"
here=$(cd "$(dirname "$0")" && pwd)

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$LIB" "$BIN/monitor" "$BIN/monitor-gui" "$DESKTOP"
  echo "removed from $PREFIX (the saved app state in ~/.config/monitor-ctl is left alone)"
  exit 0
fi

# --- dependencies: only report, never install anything ---------------------
missing=""
command -v ddcutil >/dev/null 2>&1 || missing="$missing ddcutil"
/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" \
  >/dev/null 2>&1 || gui_missing=1
if [ -n "$missing" ] || [ -n "${gui_missing:-}" ]; then
  echo "Missing:${missing}${gui_missing:+ (for the app: GTK 4 + libadwaita + PyGObject)}" >&2
  echo "  Debian/Ubuntu: sudo apt install ddcutil python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" >&2
  echo "  Fedora:        sudo dnf install ddcutil python3-gobject gtk4 libadwaita" >&2
  echo "  Arch:          sudo pacman -S ddcutil python-gobject gtk4 libadwaita" >&2
fi

# --- copy files ------------------------------------------------------------
mkdir -p "$LIB" "$BIN" "$(dirname "$DESKTOP")"
install -m 755 "$here/monitor" "$here/monitor-gui.py" "$here/edid-summary.py" "$LIB/"
ln -sf "$LIB/monitor" "$BIN/monitor"
ln -sf "$LIB/monitor-gui.py" "$BIN/monitor-gui"

cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=Monitor Control
Comment=Philips 499P9H: PBP, input sources, left/right windows
Exec=/usr/bin/python3 $LIB/monitor-gui.py
Icon=video-display
Terminal=false
Categories=Settings;HardwareSettings;
StartupWMClass=$APP_ID
DESK

echo "installed: $BIN/monitor, $BIN/monitor-gui and the Monitor Control launcher"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: $BIN is not on your PATH" >&2 ;; esac

# --- can we reach the monitor? ---------------------------------------------
if command -v ddcutil >/dev/null 2>&1; then
  if ddcutil detect 2>/dev/null | grep -q "Model:.*499P9"; then
    echo "found a 499P9 monitor: try 'monitor status'"
  else
    echo "no 499P9 found by 'ddcutil detect'. If it should be there:" >&2
    echo "  - turn DDC/CI on in the monitor menu" >&2
    echo "  - sudo modprobe i2c-dev  (and add i2c-dev to /etc/modules-load.d/ to keep it)" >&2
    echo "  - make sure your user may open /dev/i2c-*: ddcutil's package installs a udev" >&2
    echo "    rule, or add yourself to the i2c group and log in again" >&2
    echo "  - if ddcutil shows a different model name, run: MONITOR_MODEL='<name>' monitor status" >&2
  fi
fi
