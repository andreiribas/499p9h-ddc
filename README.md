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
monitor lowblue off|1|2|3|4|on  LowBlue Mode. The monitor's own mode cannot be given a level over DDC (it remembers the one last
                                chosen with its buttons, and ignores colour-gain writes while a SmartImage mode is on), so a level
                                is applied by this command: the level's colour gains, gamma 2.2 and the brightness and sharpness the
                                monitor's own preset uses. The values it replaces are saved in ~/.config/monitor-ctl/lowblue-saved and
                                `lowblue off` puts them back (only those still as it set them). The monitor's menu then shows LowBlue
                                Mode off with the colour temperature User. `lowblue on` is the monitor's own mode, with the level it
                                remembers. `monitor get lowblue` shows off or the level
monitor smartimage <mode>       the menu's SmartImage (VCP DC): off easyread office photo movie game economy lowblue
                                smartuniformity. Each mode also sets the monitor's own brightness, gamma, sharpness ...;
                                Movie turns SmartContrast and SmartResponse on, Economy turns them off
monitor get <setting> | set <setting> <value>
monitor gain-red|gain-green|gain-blue [N|+N|-N]   RGB gain 0-100 (VCP 16/18/1A)
monitor scaling widescreen|4:3|1:1     the menu's Picture Format (VCP 86). The monitor reports 16:9 (0xFF) but
                                ignores it when written: pick 16:9 in its menu. (Old names: stretch, aspect, noscale)
monitor smartcontrast on|off    (VCP F0, found with walk.sh on this monitor; its menu warns that "on"
                                can increase the energy consumption. The command does not ask, the app does)
monitor smartresponse off|fast|faster|fastest   (VCP EB, found with walk.sh: the monitor's response-time setting)
monitor osd on|off              show / hide the monitor's own menu (VCP CA); `osd on` undoes it
monitor language <name>         the monitor's menu language (VCP CC): english french german italian
                                spanish portuguese ptbr dutch swedish finnish polish czech hungarian
                                greek turkish russian ukrainian japanese korean schinese tchinese
```

To find more controls, `./walk.sh` is a guided, read-only search: you change one setting
at a time with the monitor's buttons and it shows which codes changed (see `walk.sh`).
It also copies the monitor's EDID (what the monitor tells the computer about its modes, HDR and so on)
before and after, and says what changed: that is how a menu entry such as "HDMI EDID Switch" shows up,
with or without a DDC code. At step 1 and step 3 you can type what the setting is set to and what you changed it to ("1", "Level 3" ...),
and the log records it; `./walk.sh note "text"` adds a correction to the last entry.
`./walk.sh again` walks the last setting once more, to check a result; `./walk.sh <number>` (from `./walk.sh list`) repeats any item.
`edid-summary.py <file>` prints an EDID's main facts (for example
`edid-summary.py /sys/class/drm/card1-HDMI-A-1/edid`).

Picture settings are standard DDC codes: brightness (`10`), contrast (`12`) and
volume (`62`) 0-100, sharpness (`87`) 0-100 like the monitor's menu (the monitor has 11
steps, so it moves in steps of 10: 54 is rounded to 50), `mute` on/off (`8D`), colour `preset`
(`14`: srgb 5000k 6500k 7500k 8200k 9300k 11500k user), `gamma` (`72`: 1.8 2.0 2.2
2.4 2.6) and `mode` (`DC`, the menu's SmartImage; the values were found with `walk.sh`, see NOTES.md).

Sources: `hdmi1`, `hdmi2`, `usbc` and `dp` (the DisplayPort connector), named as in the monitor's own menu. Over DDC the monitor numbers them differently: its USB-C port is "DisplayPort-1" (`dp1`), the DisplayPort connector is "DisplayPort-2" (`dp2`), and menu "HDMI 1" / "HDMI 2" are DDC values 0x12 / 0x11 (checked on one monitor by plugging a laptop in and switching to each value). A GNOME shortcut works well for
`monitor swap`.

## App

`monitor-gui.py` (GTK 4 + libadwaita, needs `python3-gi` and `gir1.2-adw-1`; installed as
"Monitor Control") is laid out like the monitor's own menu: the menus are listed on the left,
and the controls of the one you click are on the right (on a narrow window they take turns).

| Menu | Controls |
|---|---|
| Input | the PBP switch, a source picker for one screen or a left and a right picker, Swap and Apply, and a read-only line with what the monitor announces to this computer (below) |
| Picture | SmartImage (the modes), brightness, contrast, sharpness, SmartResponse, SmartContrast, gamma, picture format |
| Colour | LowBlue Mode (off, levels 1-4), colour temperature, and the red/green/blue gain when it is User (writing a gain makes the monitor switch to the User preset by itself) |
| Audio | volume, mute |
| On-screen menu | show/hide the monitor's menu, its language |
| Imported controls | controls imported from a file (only listed when there are some) |

The values are read from the monitor when you open a menu, and follow changes made with the
monitor's buttons. A status bar along the bottom of the window is always there: it says "Ready",
and while a change is being sent to the monitor it says what is being applied and all the
controls are read-only until it is done. A banner appears at the top when the monitor does
not answer (it is probably showing another input).

The app also reads the monitor on its own (every few seconds, when the window gets focus, and when
you open a menu), and the bar says "Reading the monitor…" and then "Finished reading the monitor".
Those reads can get in the way of the monitor's own on-screen menu, so the **pause button** next to
the refresh button stops all of them ("Paused" shows in the bar); the refresh button still reads
when you press it, and resuming does one read to catch up.

Changes you make in the app always go to the monitor, paused or not, and the app reads the monitor back
afterwards, so the screen shows what the monitor really has. The status bar names a change the way the menu does, for example "Applying LowBlue Mode to Level 3…". While paused, what the screen shows can be
out of date, so a banner says so ("Read now" reads), and picking the item a dropdown already shows still
sends it. If the monitor does not take a value (for example because its own menu is open), a message
says what it reports instead.

The Input page ends with "What the monitor announces": the modes the monitor tells this computer it can show
(its EDID, read from the system, so nothing is asked of the monitor and it works while the reading is
paused). It says "Full list: up to 5120×1440 at 70 Hz" or, when the monitor's HDMI EDID Switch is on 2,
"Limited list: ... 5120×1440 is not offered", with a hint to set the switch back to 1 in the monitor's menu.

Turning on a setting that can increase the monitor's energy consumption (SmartContrast, and
imported controls marked `energy`) asks for confirmation first, like the monitor's own menu.
Options > "Warn before changing energy settings" switches that off (it is on by default).

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
- an optional sixth field, `energy`, for a choice that can increase the monitor's power use
  (`night-mode | Night mode | 9A | choice | off=0 on=1 | energy`): the app then asks for
  confirmation before turning it on, like it does for SmartContrast

```
# the codes below are only an example: use the ones found for YOUR monitor
night-mode | Night mode | 9A | choice | off=0 on=1
backlight  | Backlight  | 9B | range  | 100
```

In the app: Options > Import controls file. It checks the file first (format, then it asks
the monitor, read-only, whether each code answers and whether the numbers fit) and shows what
it found before anything is imported. From the command line: `monitor controls check <file>`,
`monitor controls import <file>`, `monitor controls list|path|remove`. The imported controls
show up under "Imported controls", in `monitor picture` and in profiles. Codes for factory resets,
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
