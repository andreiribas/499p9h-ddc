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
  `monitor pbp on|off` command waits for E6 to confirm.
- **Reading code 60 is NOT reliable** (it reported dp2 while this laptop,
  hdmi1, was on the left). So `monitor status` only reports PBP (E6, reliable)
  and `layout` never reads 60; it sets both windows explicitly:
  `60 <right>`, `F6 1` (swap), `60 <left>`.
- `monitor input|left <src>`, `monitor layout <left> <right>`, `monitor swap`.
- `monitor-gui.py`: GTK4/libadwaita app (launcher: Monitor Control, app id org.phl499p9h.guisettings). PBP
  switch, one source picker (PBP off) or left/right pickers (PBP on), Swap,
  Apply. Remembers what was last applied in ~/.config/monitor-ctl/state.json.
