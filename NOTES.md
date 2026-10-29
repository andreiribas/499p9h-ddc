# Philips 499P9H over DDC/CI (ddcutil 2.2.5, `--display 1`, HDMI-1 to this laptop)

Findings so far. Everything here was found by reading values, comparing
snapshots (`./snap.sh <label>`, files in `snapshots/`) and a few careful writes.

- Standard VCP codes work: 10 brightness, 12 contrast, 14 color preset, 60
  input source (0f DP-1, 10 DP-2, 11 HDMI-1, 12 HDMI-2), 62 volume, DC mode.
- **60 = input of the LEFT (main) window.** Writing it changes the left half.
  Reading it back is NOT a reliable status check: it read 0x10 while the left
  half really showed this laptop on HDMI-1 (state: PBP on, other laptop right).
- **E6**: 1 with PBP off, 0 with PBP on. Max value 0, so it looks read-only:
  probably a PBP status flag, not a switch.
- Unchanged by turning PBP on: E0 (1, max 0x14), E9 (2), EB (0), F0 (0).
- Not found yet: the right window's input, a PBP on/off switch, the KVM switch.

Rule: never write a code without knowing which window it affects. A wrong
write can leave both halves on the same machine (fix: `ddcutil --display 1
setvcp 60 0x11`).

## Update: A5, F6, E0 (from ddcutil issue #163 + tests here)

- **A5** (Philips: window mode). Reads max 0x0200, cur 0x0100 (256) while PBP is on.
  `setvcp A5 0` turned PBP OFF (E6 went 0 -> 1) and worked. `setvcp A5 256` to
  turn it back ON FAILED (ENXIO / DDCRC_RETRIES), tried 5+ times: on this
  monitor PBP can't be switched on over DDC, only off. Turn it on in the OSD.
  DON'T write A5 again without being ready to re-enable PBP in the menu.
- **F6** (reported for the 499P9, untested here): `setvcp --noverify F6 1` swaps
  the left/right inputs, PBP only. Not readable.
- **E0** = audio source (1 = HDMI-1, 3 = DP-1, 4 = ?), not a window source.
- 60 only sets the LEFT window input. Right window input: no code found yet.
- KVM: no code found, here or in the thread.

## The `monitor` tool

`monitor` (symlinked to ~/.local/bin/monitor): `status`, `swap`, `left <input>`.
- `swap` = `F6 1` (confirmed working here; refuses if PBP is off).
- GNOME shortcut Super+F9 runs `monitor swap` (gsettings custom keybinding
  "monitor-swap" under org.gnome.settings-daemon.plugins.media-keys).
- Reading code 60 correctly follows the left window now (dp2 after a swap,
  hdmi1 before), so `status` is reliable as long as PBP is on.
- Not possible over DDC on this monitor: turning PBP on, the KVM switch.

## Update: pbp on/off, layout, GUI

- **PBP on = `setvcp --noverify A5 512`** (it then reads back 256); off = `A5 0`.
  The earlier "can't turn PBP on" was wrong: 256 is rejected, 512 works. The
  `monitor pbp on|off` command waits for A5 to confirm.
- **Reading code 60 is NOT reliable** (it reported dp2 while this laptop,
  hdmi1, was on the left). So `monitor status` only reports PBP (A5, reliable)
  and `layout` never reads 60; it sets both windows explicitly:
  `60 <right>`, `F6 1` (swap), `60 <left>`.
- `monitor input|left <src>`, `monitor layout <left> <right>`, `monitor swap`.
- `monitor-gui.py`: GTK4/libadwaita app (launcher: Monitor Control, app id org.phl499p9h.guisettings). PBP
  switch, one source picker (PBP off) or left/right pickers (PBP on), Swap,
  Apply. Remembers what was last applied in ~/.config/monitor-ctl/state.json.

## Input names

The monitor's input list (VCP 60) has no "USB-C": it reports HDMI-1 (0x11), HDMI-2
(0x12), DisplayPort-1 (0x0f) and DisplayPort-2 (0x10). **USB-C is DisplayPort-1
(0x0f) and the real DisplayPort connector is DisplayPort-2 (0x10).** Checked by
switching to 0x0f while a laptop was plugged in with USB-C and seeing it. (An
earlier note said the opposite: it was based on a test made while that laptop was
on the DisplayPort cable.) The app and `monitor` call them "USB-C"/`usbc` and
"DisplayPort"/`dp`; `dp1` = `usbc` and `dp2` = `dp` still work.

`monitor status` reads A5 on the I2C bus remembered in ~/.cache/monitor-ctl/bus
(0.25 s instead of ~1.6 s); the app polls it every 5 s by default (Options menu).

## PIP / split ratio

This monitor's menu has no PIP option, only PBP (checked in the OSD). PBP is a fixed
50/50 split (two 2560x1440 halves), so there is nothing to adjust. `A5` accepts 0
(single screen) and 512 (turns PBP on); writing 256 is rejected here. The
scan.sh / diffscan.sh scripts were sped up for this search (about 30 s per scan
using the remembered I2C bus) and record A5's full raw value.

## E6 is not a reliable PBP flag; A5 is

`E6` (1 = off, 0 = on) was used for `monitor status`, but on a second laptop connected by
USB-C it read 0 ("on") while the monitor showed one picture, and `A5` read 0 there.
`A5` (window mode, read with `getvcp A5 --verbose`, field `cur_val`): 0 = one screen,
256 = PBP. It agreed with reality from both laptops, so `status`, `pbp on|off`,
`swap` and `layout` now use A5. `monitor doctor` prints both for comparison.

## HDMI numbering follows the monitor's menu, not DDC

A laptop on the port the monitor's menu calls "HDMI 2" appears when VCP 60 is set to
0x11, which DDC calls HDMI-1. So the tool and the app now use the menu's names:
`hdmi1` = 0x12, `hdmi2` = 0x11 (they were the other way round before). The app's saved
settings carry a `names_version`; files without it are swapped once on load so each choice
keeps pointing at the same physical input.


## Switching speed

`ddcutil --model ... setvcp 60 ...` took 3-7 s: it scans every display and probes the
monitor first, and the monitor is slow to answer the probes. `ddcutil --bus N
--skip-ddc-checks setvcp 60 ...` takes 0.1 s. `monitor` now finds the bus from sysfs (a
connected display whose EDID names the model, so a write can't reach another screen),
skips the probing if this ddcutil has --skip-ddc-checks (2.x; 1.4.1 may not), retries
once after 0.3 s, and only then falls back to the slow lookup. In 10 spaced-out writes:
7 took 0.1-0.2 s and 3 took about 1.5 s (retry path). The monitor's own switching time
(re-sync of the new input) comes on top and can't be changed.

## The right window is set in the monitor's menu

`layout` (60 = right input, F6 swap, 60 = left input) did not put USB-C on the right on
this monitor, even with 1.5 s pauses: the monitor needs several seconds to settle after
each step and ignores the swap. The monitor's own menu has a setting for the right
window's input, and the monitor **remembers it across PBP off/on** (checked: set to USB-C
in the menu, `monitor pbp off`, `monitor pbp on`, and the other laptop was back on the
right). So `monitor pbp on [left]` only turns PBP on and sets the left window, and the
app's Apply in PBP mode sets only the left. The app's Right picker just records what the
menu is set to, so Swap can keep the pickers in step. `layout` stays as an experimental
command.


## Found with walk.sh (2026-10-21)

- **F0 = SmartContrast** (0 = off, 1 = on). Found by turning SmartContrast on in the
  monitor's menu: only F0 changed (0 -> 1). The menu asks for a confirmation first (it can
  increase the energy consumption); the app asks as well.
- **EB = SmartResponse**: 0 Off, 1 Fast, 2 Faster, 3 Fastest (one walk.sh round per step: only
  EB changed each time). The read reports a maximum of 1, which is wrong: the capability list
  gives EB the values 00-03.
- **Picture Format = VCP 86**: Wide Screen 0x08, 4:3 0x02, 16:9 0xFF. Writing 0xFF is not
  accepted (verification fails; with --noverify the value stays unchanged): 16:9 can only be
  chosen in the monitor's menu. **1:1 = 0x01** (confirmed with walk.sh: FF -> 01, and writing
  01 works). Movie 1 and Movie 2 are greyed out in the menu.
- **PBP Input** (the menu's name for the right window's source): one round showed code `60`
  moving (0x10 -> 0x11). `60` is what sets the LEFT window, and reading it has been
  unreliable, so this may mean that reading `60` follows the right window. Not settled: PBP
  was off when it was read afterwards. To check with PBP on: read `60`, change PBP Input,
  read `60`, then `monitor swap` and read `60` again.
- The monitor's Picture menu (as listed by the owner): HDR (Normal, greyed out), Picture Format
  (Wide Screen), Brightness, Contrast, Sharpness, SmartResponse, SmartContrast, Gamma, Over Scan
  (Off, greyed out). HDR and Over Scan cannot be changed, so there is nothing to find.
  Picture Format is probably VCP 86 (read as 0x08 = "Wide Screen"?). The menu's Sharpness
  scale is 0-100 while VCP 87 reports 0-10 (maximum 10): the menu value is 10x. `monitor` and
  the app now use the menu's 0-100 (steps of 10; other values are rounded).

## SmartImage = VCP DC (found with walk.sh, 2026-10-21/22)

The monitor's first button lists the SmartImage modes. Each one sets DC and also the
monitor's own preset for other values (what changed when moving to it from the previous mode):

| Mode | DC | Preset applied |
|---|---|---|
| Off | 0x00 | back to the user's own values (brightness, gamma, sharpness) |
| EasyRead | 0x0e | brightness 100, gamma 2.2, sharpness 50 |
| Office | 0x01 | brightness 70, sharpness 60 |
| Photo | 0x02 | brightness 100, sharpness 70 |
| Movie | 0x03 | colour temperature 7500 K, gamma 2.4, sharpness 90, SmartResponse Fast, **SmartContrast on** |
| Game | 0x05 | colour temperature User, gamma 2.2 |
| Economy | 0x08 | brightness 8, sharpness 50, 6500 K, SmartResponse off, SmartContrast off |
| LowBlue Mode | 0x0b | colour temperature User, blue gain 68, brightness 100 |
| SmartUniformity | 0x1f | 6500 K, blue gain 100 |

0C (colour temperature readout) follows the colour temperature (14), so it moves with the
preset and is not a control. The capability list also has DC = 0xe1, which no mode used.

## LowBlue Mode (the menu's first item: Off, or On at level 1-4)

It is the SmartImage mode LowBlue (DC = 0x0b, User colour temperature, gamma 2.2) plus values that
depend on the level (measured with walk.sh, one round per step; gain = red/green/blue):

| Level | Gain | Brightness | Sharpness |
|---|---|---|---|
| 1 | 99 / 97 / 90 | 100 | 50 |
| 2 | 100 / 100 / 68 | 100 | 50 |
| 3 | 100 / 100 / 68 | 70 | 100 |
| 4 | 100 / 100 / 63 | 70 | 100 |

No code holds the level itself. Findings from testing over DDC:
- Writing DC = 0x0b turns the mode on with **the level the monitor remembers**, i.e. the one last
  chosen in its menu (it applied level 2's values at first, only because level 2 was remembered
  then; after the owner had picked level 4 in the menu it applied level 4's: blue gain 63,
  brightness 70, sharpness 100). Writing DC = 0 restores the user's own values.
- **While a SmartImage mode is on, the monitor ignores writes to the colour gains** (16/18/1A):
  the value stays, even with verification off. Brightness and sharpness can be written. So over DDC
  the level cannot be chosen, only Off / On; the level on can be read back by matching the values.
- **Writes made while the mode is on become the mode's stored values** (writing brightness 90 there
  made every later DC = 0x0b apply 90). An earlier `monitor lowblue 2|3` imitated levels by writing
  brightness and sharpness inside the mode; that was wrong (the gains still came from the remembered
  level) and could overwrite stored values, so it was removed: `monitor lowblue on|off` writes only DC.
- A bug of that earlier version (after switching the mode off it used a stale reading, skipped
  re-entering the mode, and wrote brightness 70 as the user's own value) was caught and undone; the
  command now never writes brightness or sharpness at all.

### How the app offers levels 1-4 (software)

Since the monitor's own mode cannot be given a level over DDC (above), `monitor lowblue 1|2|3|4` applies
the level's values with the monitor's mode off: gains (the part that reduces the blue), gamma 2.2,
brightness and sharpness, from the table. The values it replaces are saved in
~/.config/monitor-ctl/lowblue-saved ("saved" = before, "applied" = what the level set), and
`lowblue off` writes back each saved value only if it is still the applied one, the colour temperature
last (writing a gain switches it to User). A level is recognised from the values, so it also shows when
the monitor's own mode is on at that level. Tested on the real monitor: levels 1-4 read back as 1-4 and
`off` restores all 16 settings exactly. The difference from the buttons: the monitor's menu keeps showing
LowBlue Mode off (colour temperature User). A hidden code that selects the real level has not been found;
the write-only codes (B0, CF, DE, A2, 76 ...) were not probed.

## HDMI EDID Switch (found with walk.sh, 2026-10-29)

The menu has HDMI1 EDID Switch and HDMI2 EDID Switch, each with the values 1 and 2 (1 is the default).
Changing HDMI1 EDID Switch from 1 to 2 changes the EDID the monitor announces on HDMI 1: the 384-byte EDID
(base block, CTA-861 extension, DisplayID extension with 5120x1440 at 30/60/70 Hz) becomes 256 bytes
without the DisplayID extension, so the computer can no longer use 5120x1440 and the maximum is 4K. The
HDMI 2.0 block (600 MHz), HDR and the other modes stay. No DDC code changed, so it cannot be set from the
app (and it would make the screen reconnect). Set it back to 1 in the monitor's menu to get 5120x1440.
HDMI2 EDID Switch (values 1 and 2 as well) is presumably the same thing for the HDMI 2 port, but that is
**unverified**: nothing is connected to HDMI 2 and this laptop is on HDMI 1, so what it does to HDMI 2's EDID
was not observed (it would need a computer connected to HDMI 2). What was checked, with a clean round: changing
only HDMI2 EDID Switch (2 -> 1) left the EDID on HDMI-1 unchanged and moved no DDC code, so it does not affect
HDMI-1's EDID, and neither entry can be set over DDC.
walk.sh sees this by comparing the EDID before and after (edid-summary.py decodes it).

## Input menu (found with walk.sh, 2026-10-29)

The monitor's Input menu lists "1 HDMI 2.0", "2 HDMI 2.0", DisplayPort, USB C and **Auto** (an On/Off switch, On by
default). The four inputs are VCP 60, which the app already uses (HDMI 1 = 0x12, HDMI 2 = 0x11, USB-C = 0x0f,
DisplayPort = 0x10; the monitor's menu numbers the HDMI ports the other way round from DDC). Auto: turning it Off and
On again moved no readable code and left the EDID unchanged, so it has no readable DDC code. Tested with only HDMI 1
connected, so what it does when a second source appears was not seen, and a code that can only be written would not
show in this kind of search.

