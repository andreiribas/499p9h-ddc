# 499p9h-ddc

Control a **Philips 499P9H** (49", 32:9) from Linux instead of the monitor's
buttons: switch inputs, turn PBP (picture-by-picture) on and off, choose what
goes on the left and right, swap the halves. It talks to the monitor over
DDC/CI with [ddcutil](https://www.ddcutil.com/). No kernel driver is involved.

Written and tested on one monitor (499P9H, firmware as shipped in 2022) over
HDMI, on Ubuntu with GNOME on Wayland. Other Philips monitors may use the same
codes, but nothing here is verified for them.

## Install

Needs [ddcutil](https://www.ddcutil.com/); the app also needs GTK 4,
libadwaita and PyGObject.

```
# Debian/Ubuntu
sudo apt install ddcutil python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
# Fedora:  sudo dnf install ddcutil python3-gobject gtk4 libadwaita
# Arch:    sudo pacman -S ddcutil python-gobject gtk4 libadwaita

git clone https://github.com/andreiribas/499p9h-ddc
cd 499p9h-ddc
./install.sh              # into ~/.local, no root needed
```

`PREFIX=/usr/local ./install.sh` installs elsewhere; `./install.sh --uninstall`
removes it. The installer copies the files, so the checkout can be deleted.

If `monitor status` finds no monitor: turn DDC/CI on in the monitor's menu,
load the `i2c-dev` kernel module (`sudo modprobe i2c-dev`), and make sure your
user may open `/dev/i2c-*` (ddcutil's package installs a udev rule; otherwise
join the `i2c` group and log in again). If ddcutil reports your monitor under a
different model name, set `MONITOR_MODEL='<name>'`.

## Command line

```
monitor status                  PBP on/off
monitor pbp on|off              turn PBP on / off
monitor swap                    swap the left and right windows (PBP only)
monitor input <source>          input of the screen (PBP off) / left window (PBP on)
monitor left <source>           same as input
monitor layout <left> <right>   set both windows (PBP on)
```

Sources: `hdmi1`, `hdmi2`, `dp1`, `dp2`. A GNOME shortcut works well for
`monitor swap`.

## App

`monitor-gui.py` (GTK 4 + libadwaita, needs `python3-gi` and `gir1.2-adw-1`;
installed as "Monitor Control"): a PBP switch, a source picker for one screen
or a left and a right picker, Swap and Apply.

## What works, and what doesn't

| | Over DDC |
|---|---|
| Input of the left window (VCP `60`) | works |
| PBP off (`A5` = 0) / on (`A5` = 512) | works |
| Swap left and right (`F6` = 1) | works, PBP only |
| Right window input | no code; `layout` sets it by putting it on the left, swapping, then setting the left |
| KVM (USB upstream) switch | **no code found**, still needs the monitor's buttons |
| Reading which input a window shows | **unreliable**: code `60` has reported the wrong input |

Because the inputs can't be read back, the app shows what you last applied
(`~/.config/monitor-ctl/state.json`), and `monitor status` only reports PBP
(VCP `E6`: 0 = on, 1 = off), which is reliable. If you change inputs with the
buttons, the app can be out of date.

`A5` is the Philips window-mode code. `256` is what it reads back while PBP is
on, but writing 256 is rejected; writing `512` is what turns PBP on.

## How the codes were found

`NOTES.md` has the whole story, and `snapshots/` the raw data: ddcutil's
capability list, short snapshots (`snap.sh`) and full scans of all 256 VCP
codes (`scan.sh`, `diffscan.sh`) taken before and after changing a setting in
the monitor's menu. The `F6` swap and the `A5` mode codes come from
[ddcutil issue #163](https://github.com/rockowitz/ddcutil/issues/163), where
other Philips owners reported them; they were confirmed on this monitor.

**Careful when writing unknown VCP codes.** Everything here only writes codes
that were confirmed. A wrong write can leave both halves on the same input
(fix: `monitor left hdmi1`) or switch PBP off, which you may then have to turn
on again with `monitor pbp on`.

## License

MIT, see [LICENSE](LICENSE). This project only runs `ddcutil` as a separate
program and doesn't include any of its code. It is not affiliated with or
endorsed by Philips or Koninklijke Philips N.V.; "Philips" is a trademark of its
owner, used here only to name the monitor this was tested on.
