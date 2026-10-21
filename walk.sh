#!/bin/sh
# SPDX-License-Identifier: MIT
# Guided search for the VCP code behind each setting of the monitor's own menu.
# It only READS codes (scan.sh). Each round: name ONE setting, change it with the
# monitor's buttons, press Enter; the script scans again and shows which codes
# changed since the previous round. Results are appended to snapshots/walk-log.md.
# The full scans (snapshots/walk-NN.txt) are not meant to be committed.
#
#   ./walk.sh        (empty name to stop)
#
# Tips: change one thing per round, by a clearly visible step (a toggle, or a
# value several steps away); the codes that change between two rounds are the
# candidates. AC and AE (timing) change by themselves, and 20 reports a different
# maximum from one scan to the next, so these three are left out.
cd "$(dirname "$0")" || exit 1
log=snapshots/walk-log.md
n=0
label() { printf 'walk-%02d' "$1"; }
echo "Reading the monitor (about 30 s) ..."
./scan.sh "$(label 0)" || exit 1
printf '\n# Walk started %s\n' "$(date '+%Y-%m-%d %H:%M')" >> "$log"
while :; do
  printf '\nSetting you are about to change (e.g. "Gamma 2.2 -> 1.8"; empty to stop): '
  read -r name
  [ -n "$name" ] || break
  printf 'Change it on the monitor, then press Enter. '
  read -r _
  n=$((n + 1))
  echo "Reading the monitor (about 30 s) ..."
  ./scan.sh "$(label "$n")" > /dev/null
  d=$(./diffscan.sh "$(label $((n - 1)))" "$(label "$n")" | grep -vE '^[<>] (AC|AE|20):')
  printf '\n## %s\n' "$name" >> "$log"
  if [ -n "$d" ]; then
    echo "Changed:"; echo "$d"
    printf '```\n%s\n```\n' "$d" >> "$log"
  else
    echo "No readable code changed (this setting may have no DDC code)."
    echo "No readable code changed." >> "$log"
  fi
done
echo "Log: $log"
