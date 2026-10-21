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
monitor pbp on <left>           turn PBP on and set the left window
monitor swap                    swap the left and right windows (PBP only)
monitor input <source>          input of the screen (PBP off) / left window (PBP on)
monitor left <source>           same as input
monitor layout <left> <right>   EXPERIMENTAL, unreliable: set both windows (PBP on)
monitor picture                 all picture settings, one per line
monitor brightness [N|+N|-N]    show / set / adjust (also contrast, sharpness, volume)
monitor mute|preset|gamma|mode [value]
monitor get <setting> | set <setting> <value>
monitor gain-red|gain-green|gain-blue [N|+N|-N]   RGB gain 0-100 (VCP 16/18/1A)
monitor scaling noscale|aspect|stretch     (VCP 86)
monitor osd on|off              show / hide the monitor's own menu (VCP CA); `osd on` undoes it
monitor language <name>         the monitor's menu language (VCP CC): english french german italian
                                spanish portuguese ptbr dutch swedish finnish polish czech hungarian
                                greek turkish russian ukrainian japanese korean schinese tchinese
```

To find more controls, `./walk.sh` is a guided, read-only search: you change one setting
at a time with the monitor's buttons and it shows which codes changed (see `walk.sh`).

Picture settings are standard DDC codes: brightness (`10`), contrast (`12`) and
volume (`62`) 0-100, sharpness (`87`) 0-10, `mute` on/off (`8D`), colour `preset`
(`14`: srgb 5000k 6500k 7500k 8200k 9300k 11500k user), `gamma` (`72`: 1.8 2.0 2.2
2.4 2.6) and `mode` (`DC`: standard productivity mixed movie games). The names of the
`mode` values come from the monitor's capability list and are not checked against its menu.

Sources: `hdmi1`, `hdmi2`, `usbc` and `dp` (the DisplayPort connector), named as in the monitor's own menu. Over DDC the monitor numbers them differently: its USB-C port is "DisplayPort-1" (`dp1`), the DisplayPort connector is "DisplayPort-2" (`dp2`), and menu "HDMI 1" / "HDMI 2" are DDC values 0x12 / 0x11 (checked on one monitor by plugging a laptop in and switching to each value). A GNOME shortcut works well for
`monitor swap`.

## App

`monitor-gui.py` (GTK 4 + libadwaita, needs `python3-gi` and `gir1.2-adw-1`;
installed as "Monitor Control"): a PBP switch, a source picker for one screen
or a left and a right picker, Swap and Apply.
A second page, **Picture**, has sliders for brightness, contrast, sharpness and volume,
mute, and dropdowns for colour temperature, gamma and display mode, and, when the colour
temperature is User, red/green/blue gain sliders. (Writing a gain makes the monitor
switch to the User preset by itself.) It reads the
values from the monitor and follows changes made with the monitor's buttons.

## Extra controls from a file

Another monitor (or a TV) has other settings, and other codes. The app and `monitor` can
load more controls from a plain text file, one per line:

```
name | label | code | kind | values
```

- `name`: lower-case letters, digits and `-` (used by `monitor get|set <name>`)
- `label`: what the app shows
- `code`: the two hex digits of the VCP code
- `kind`: `range` (values: the maximum, e.g. `100`) or `choice` (values: `name=number` pairs, e.g. `off=0 on=1`)

```
# the codes below are only an example: use the ones found for YOUR monitor
night-mode | Night mode | 9A | choice | off=0 on=1
backlight  | Backlight  | 9B | range  | 100
```

In the app: Options > Import controls file. It checks the file first (format, then it asks
the monitor, read-only, whether each code answers and whether the numbers fit) and shows what
it found before anything is imported. From the command line: `monitor controls check <file>`,
`monitor controls import <file>`, `monitor controls list|path|remove`. The imported controls
show up on the Picture page, in `monitor picture` and in profiles. Codes for factory resets,
power and the ones the app already uses are refused. Writing a wrong code to a monitor can
change settings you did not expect, so only import files you trust.

`./walk.sh` is how you find the codes: it suggests one menu setting at a time, you change it
with the monitor's buttons, and it shows which code changed (and prints a line to start from).
Its findings (`snapshots/walk-log.md`) stay on your computer: they are not committed.

## What works, and what doesn't

| | Over DDC |
|---|---|
| Input of the left window (VCP `60`) | works |
| PBP off (`A5` = 0) / on (`A5` = 512) | works |
| Swap left and right (`F6` = 1) | works, PBP only |
| Right window input | **no reliable code.** Set it once in the monitor's own menu: the monitor remembers it, also across PBP off/on. `layout` tries it by putting the input on the left, swapping, then setting the left, but the monitor needs several seconds between those steps and often ignores the swap |
| KVM (USB upstream) switch | **no code found**, still needs the monitor's buttons |
| Reading which input a window shows | **unreliable**: code `60` has reported the wrong input |

The app also needs to know which input *this* computer is plugged into, so its source
pickers start there. It guesses from the video port (HDMI is HDMI-1; DisplayPort or
USB-C is USB-C) and the Options menu has "Connected to" to correct it per computer.

Because the inputs can't be read back, the app shows what you last applied
(`~/.config/monitor-ctl/state.json`), and `monitor status` only reports PBP
(VCP `A5`: 0 = one screen, 256 = PBP), which is reliable. If you change inputs with the
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
(fix: `monitor left <the input this computer is on>`) or switch PBP off, which you may then have to turn
on again with `monitor pbp on`.

## License

MIT, see [LICENSE](LICENSE). This project only runs `ddcutil` as a separate
program and doesn't include any of its code. It is not affiliated with or
endorsed by Philips or Koninklijke Philips N.V.; "Philips" is a trademark of its
owner, used here only to name the monitor this was tested on.
