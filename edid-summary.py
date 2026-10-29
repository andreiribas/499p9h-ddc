#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Print what a monitor's EDID says, one fact per line, so that two EDIDs can be compared
with diff (walk.sh does this to see what a menu setting such as "HDMI EDID Switch" changes).

usage: edid-summary.py <edid file>      (e.g. /sys/class/drm/card1-HDMI-A-1/edid)

Reads the base block, the CTA-861 extension (video codes, HDMI / HDMI Forum blocks, HDR,
colorimetry, audio) and the DisplayID extension (the 5120x1440 modes of a 32:9 monitor).
It is a summary, not a full decoder: for everything use the `edid-decode` tool."""
import sys


def timing(x):
    """pixel clock (kHz), width, height, refresh (Hz) of an 18-byte detailed timing"""
    clk = (x[0] | x[1] << 8) * 10
    ha = x[2] | ((x[4] & 0xF0) << 4)
    hb = x[3] | ((x[4] & 0x0F) << 8)
    va = x[5] | ((x[7] & 0xF0) << 4)
    vb = x[6] | ((x[7] & 0x0F) << 8)
    return clk, ha, va, round(clk * 1000 / ((ha + hb) * (va + vb)), 1)


# The video codes (VIC) of the CTA-861 extension that matter here: code -> (width, height, Hz)
VIC_MODES = {1: (640, 480, 60), 2: (720, 480, 60), 3: (720, 480, 60), 4: (1280, 720, 60),
             16: (1920, 1080, 60), 19: (1280, 720, 50), 31: (1920, 1080, 50), 32: (1920, 1080, 24),
             33: (1920, 1080, 25), 34: (1920, 1080, 30), 93: (3840, 2160, 24), 94: (3840, 2160, 25),
             95: (3840, 2160, 30), 96: (3840, 2160, 50), 97: (3840, 2160, 60), 98: (4096, 2160, 24),
             99: (4096, 2160, 25), 100: (4096, 2160, 30), 101: (4096, 2160, 50), 102: (4096, 2160, 60)}


def modes(d):
    """The (width, height, Hz) modes an EDID announces: detailed timings of the base block, of the
    CTA-861 extension and of the DisplayID extension, and the standard video codes."""
    found = set()
    if len(d) < 128:
        return found
    for off in (54, 72, 90, 108):
        x = d[off:off + 18]
        if x[0] or x[1]:
            _clk, ha, va, hz = timing(x)
            found.add((ha, va, round(hz)))
    for i in range(1, len(d) // 128):
        e = d[i * 128:(i + 1) * 128]
        if e[0] == 0x02:
            end, p = e[2], 4
            while p < end:
                tag, ln = e[p] >> 5, e[p] & 31
                if tag == 2:
                    found.update(VIC_MODES[v & 0x7F] for v in e[p + 1:p + 1 + ln] if (v & 0x7F) in VIC_MODES)
                p += 1 + ln
            for off in range(end, 127, 18):
                x = e[off:off + 18]
                if x[0] or x[1]:
                    _clk, ha, va, hz = timing(x)
                    found.add((ha, va, round(hz)))
        elif e[0] == 0x70:
            p = 5
            while p + 3 < 127:
                tag, ln = e[p], e[p + 2]
                pay = e[p + 3:p + 3 + ln]
                if tag == 0x03:
                    for k in range(0, len(pay) - 19, 20):
                        t = pay[k:k + 20]
                        clk = ((t[0] | t[1] << 8 | t[2] << 16) + 1) * 10
                        ha, hb = (t[4] | t[5] << 8) + 1, (t[6] | t[7] << 8) + 1
                        va, vb = (t[12] | t[13] << 8) + 1, (t[14] | t[15] << 8) + 1
                        found.add((ha, va, round(clk * 1000 / ((ha + hb) * (va + vb)))))
                p += 3 + ln
                if ln == 0:
                    break
    return found


def summarize(d):
    out = [f"{len(d)} bytes = {len(d) // 128} blocks"]
    b = d[:128]
    if len(b) < 128 or b[:8] != bytes([0, 255, 255, 255, 255, 255, 255, 0]):
        return out + ["not a valid EDID"]
    mid = (b[8] << 8) | b[9]
    maker = "".join(chr(((mid >> s) & 31) + 64) for s in (10, 5, 0))
    out.append(f"maker {maker}, product code {b[10] | (b[11] << 8):#06x}, EDID {b[18]}.{b[19]}")
    for off in (54, 72, 90, 108):
        x = b[off:off + 18]
        if x[0] or x[1]:
            clk, ha, va, hz = timing(x)
            out.append(f"base timing: {ha}x{va} @ {hz} Hz (pixel clock {clk / 1000:.1f} MHz)")
        elif x[3] == 0xFC:
            out.append("name: " + x[5:18].decode(errors="replace").strip())
        elif x[3] == 0xFD:
            out.append(f"range limits: V {x[5]}-{x[6]} Hz, H {x[7]}-{x[8]} kHz, max pixel clock {x[9] * 10} MHz")
    for i in range(1, len(d) // 128):
        e = d[i * 128:(i + 1) * 128]
        if e[0] == 0x02:
            out += cta(e)
        elif e[0] == 0x70:
            out += displayid(e)
        else:
            out.append(f"block {i}: tag {e[0]:#04x}")
    return out


EXT = {0: "video capability", 5: "colorimetry", 6: "HDR static metadata", 0x0E: "YCbCr 4:2:0 capability map",
       0x0D: "video format preference", 0x12: "HDMI audio", 0x13: "room configuration", 1: "vendor-specific video"}


def cta(e):
    out = ["CTA-861 extension:"]
    end, p, vics = e[2], 4, []
    while p < end:
        tag, ln = e[p] >> 5, e[p] & 31
        blk = e[p + 1:p + 1 + ln]
        if tag == 2:
            vics = list(blk)
        elif tag == 3 and ln >= 3:
            oui = blk[0] | blk[1] << 8 | blk[2] << 16
            if oui == 0x000C03:
                out.append("  HDMI 1.x vendor block" + (f", max TMDS clock {blk[5] * 5} MHz" if ln >= 6 else ""))
            elif oui == 0xC45DD8 and ln >= 6:
                out.append(f"  HDMI Forum block (HDMI 2.0+): max TMDS rate {blk[4] * 5} MHz, flags {blk[5]:#04x}"
                           + (" (SCDC: scrambling)" if blk[5] & 0x80 else ""))
            else:
                out.append(f"  vendor block {oui:#08x}")
        elif tag == 7 and ln:
            out.append("  extended block: " + EXT.get(blk[0], f"ext tag {blk[0]:#x}"))
        elif tag == 1:
            out.append(f"  audio block ({ln // 3} formats)")
        p += 1 + ln
    out.append(f"  video codes (VIC): {vics}")
    for off in range(end, 127, 18):
        x = e[off:off + 18]
        if x[0] or x[1]:
            clk, ha, va, hz = timing(x)
            out.append(f"  CTA timing: {ha}x{va} @ {hz} Hz")
    return out


def displayid(e):
    out = ["DisplayID extension:"]
    p = 5
    while p + 3 < 127:
        tag, ln = e[p], e[p + 2]
        pay = e[p + 3:p + 3 + ln]
        if tag == 0x03:
            for k in range(0, len(pay) - 19, 20):
                t = pay[k:k + 20]
                clk = ((t[0] | t[1] << 8 | t[2] << 16) + 1) * 10
                ha, hb = (t[4] | t[5] << 8) + 1, (t[6] | t[7] << 8) + 1
                va, vb = (t[12] | t[13] << 8) + 1, (t[14] | t[15] << 8) + 1
                out.append(f"  timing: {ha}x{va} @ {round(clk * 1000 / ((ha + hb) * (va + vb)), 1)} Hz"
                           f" (pixel clock {clk / 1000:.1f} MHz)")
        p += 3 + ln
        if ln == 0:
            break
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        data = open(sys.argv[1], "rb").read()
    except OSError as err:
        sys.exit(f"edid-summary: {err}")
    print("\n".join(summarize(data)))
