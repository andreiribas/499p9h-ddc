#!/bin/sh
# SPDX-License-Identifier: MIT
# Read EVERY VCP code 00-FF (read-only) and save "code: raw reply" lines to
# snapshots/<label>.txt, to diff against another full scan. Unsupported codes
# show as ERR and are skipped in the diff output of diffscan.sh.
# usage: ./scan.sh <label>
label=${1:?usage: scan.sh <label>}
out="$(dirname "$0")/snapshots/$label.txt"
: > "$out"
i=0
while [ $i -le 255 ]; do
  c=$(printf '%02X' $i)
  printf '%s: %s\n' "$c" "$(timeout 6 ddcutil --display 1 --brief getvcp "$c" 2>&1 | head -1)" >> "$out"
  i=$((i + 1))
done
grep -vc ERR "$out" | sed 's/^/readable codes: /'
