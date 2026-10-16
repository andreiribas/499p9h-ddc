#!/bin/sh
# SPDX-License-Identifier: MIT
# Read EVERY VCP code 00-FF (read-only) and save "code: raw reply" lines to
# snapshots/<label>.txt, to diff against another full scan (see diffscan.sh).
# Uses the I2C bus that `monitor status` remembered (fast); falls back to the
# model name. usage: ./scan.sh <label>
label=${1:?usage: scan.sh <label>}
out="$(dirname "$0")/snapshots/$label.txt"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/monitor-ctl/bus"
if [ -r "$cache" ]; then target="--bus $(cat "$cache")"; else target="--model 'PHL 499P9'"; fi
: > "$out"
i=0
while [ $i -le 255 ]; do
  c=$(printf '%02X' $i)
  printf '%s: %s\n' "$c" "$(eval timeout 6 ddcutil $target --skip-ddc-checks --brief getvcp "$c" 2>&1 | head -1)" >> "$out"
  i=$((i + 1))
done
# The brief reply shows only the low byte; A5 (window mode) and EC (PIP
# position) use both, so also record their full raw value.
for c in A5 EC; do
  printf 'RAW %s: %s\n' "$c" "$(eval timeout 6 ddcutil $target --skip-ddc-checks getvcp "$c" --verbose 2>&1 | grep 'Raw value' | sed 's/.*max_val/max_val/')" >> "$out"
done
grep -cE '^[0-9A-F]{2}: VCP [0-9A-F]{2} [^E]' "$out" | sed 's/^/readable codes: /'
