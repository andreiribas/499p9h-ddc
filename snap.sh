#!/bin/sh
# SPDX-License-Identifier: MIT
# Read the VCP codes that might matter on the Philips 499P9H and save them as
# snapshots/<label>.txt, so two snapshots can be diffed to see what a button
# press changed. Read-only: nothing is written to the monitor.
# usage: ./snap.sh <label>
label=${1:?usage: snap.sh <label>}
out="$(dirname "$0")/snapshots/$label.txt"
: > "$out"
for c in 10 12 14 60 62 86 8D CA D6 DC E0 E6 E9 EB F0; do
  printf '%s: %s\n' "$c" "$(timeout 10 ddcutil --display 1 --brief getvcp "$c" 2>&1 | head -1)" >> "$out"
done
cat "$out"
