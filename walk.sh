#!/bin/sh
# SPDX-License-Identifier: MIT
# Guided search for the VCP code behind each setting of the monitor's own menu.
# It only READS codes. Each round: name ONE setting, change it with the monitor's
# buttons, press Enter; the script reads the monitor again and shows which codes
# changed since the previous round. Results are appended to snapshots/walk-log.md.
# The scans (snapshots/walk-NN.txt) are not meant to be committed.
#
#   ./walk.sh        (an empty name, or "done", stops)
#
# The first run probes all 256 codes (about 20 s) to learn which ones answer, and
# remembers them in snapshots/walk-codes.txt; every later read asks only those, in
# one call (a few seconds). Delete that file to probe again.
#
# Why not read everything every time, or in parallel threads? The monitor answers
# one request at a time over a single I2C bus, and about 75 ms are spent on each of
# the ~215 codes that do not exist; ddcutil also takes a lock on the bus, so parallel
# reads queue up (measured: 4 at once took as long as 4 in a row, with lock errors).
# Asking only the ~40 codes that exist is what makes a read fast.
#
# Tips: change one thing per round, by a clearly visible step (a toggle, or a
# value several steps away); the codes that change between two rounds are the
# candidates. AC and AE (timing) change by themselves, and 20 reports a different
# maximum from one scan to the next, so these three are left out.
cd "$(dirname "$0")" || exit 1
log=snapshots/walk-log.md
codes_file=snapshots/walk-codes.txt
cache="${XDG_CACHE_HOME:-$HOME/.cache}/monitor-ctl/bus"
if [ -r "$cache" ]; then target="--bus $(cat "$cache")"; else target="--model PHL\ 499P9"; fi

# Reads the codes in $codes_file in one ddcutil call, as "XX: VCP XX ..." lines
# (the format scan.sh writes and diffscan.sh compares), plus the full raw value of
# A5, which uses both bytes.
fast_scan() {
  # shellcheck disable=SC2086
  eval ddcutil $target --skip-ddc-checks --brief getvcp $(cat "$codes_file") 2>&1 \
    | awk '/^VCP / {printf "%s: %s\n", $2, $0}'
  # shellcheck disable=SC2086
  printf 'RAW A5: %s\n' "$(eval ddcutil $target --skip-ddc-checks getvcp A5 --verbose 2>&1 \
    | grep 'Raw value' | sed 's/.*max_val/max_val/')"
}

# The first probe: asks every code 00-FF in turn and shows all 256 as a grid. A code
# is grey while it is pending, yellow while it is being asked, then green (OK) or red
# (not responding). One reader goes through them in order: the monitor answers one
# request at a time over a single I2C bus, so asking several at once would not be
# faster (measured). Writes the codes that answer to $codes_file.
probe_codes() {
  state=$(mktemp)
  (
    i=0
    while [ $i -le 255 ]; do
      c=$(printf '%02X' $i)
      # shellcheck disable=SC2086
      r=$(eval timeout 6 ddcutil $target --skip-ddc-checks --brief getvcp "$c" 2> /dev/null | head -1)
      # ddcutil itself says "not readable" for write-only codes (resets, flips, ...):
      # they are not asked, so they are neither answering nor silent
      case "$r" in
        "VCP $c ERR" | "") echo "$c no" ;;
        "Feature "*"not readable"*) echo "$c wo" ;;
        "VCP $c "*) echo "$c ok" ;;
        *) echo "$c no" ;;
      esac >> "$state"
      i=$((i + 1))
    done
  ) &
  pid=$!
  t0=$(date +%s.%N)
  if [ -t 2 ]; then
    printf '\n%.0s' $(seq 1 19) >&2   # room for the grid, redrawn in place
    while kill -0 "$pid" 2> /dev/null; do
      draw_grid "$state" "$t0" >&2
      sleep 0.1
    done
  fi
  wait "$pid"
  draw_grid "$state" "$t0" done >&2
  awk '$2 == "ok" {print $1}' "$state" > "$codes_file"
  rm -f "$state"
}

# One frame of the probe grid, drawn over the previous one (19 lines up). Without a
# terminal only the summary line is printed.
draw_grid() {
  awk -v t="$(awk -v a="$2" -v b="$(date +%s.%N)" 'BEGIN {printf "%.1f", b - a}')" \
      -v fin="${3:-}" -v tty="$([ -t 2 ] && echo 1 || echo 0)" '
    { st[$1] = $2; if ($2 == "ok") n++; if ($2 == "wo") w++ }
    END {
      if (!tty) { if (fin == "done") printf "Probed all codes: %d OK, %d write-only, %d not responding, %ss\n", n, w, NR - n - w, t; exit }
      printf "\033[19A"
      printf "\033[KProbing the monitor, one code at a time (green = OK, red = not responding, cyan = write-only)\n"
      for (r = 0; r < 16; r++) {
        printf "\033[K  "
        for (c = 0; c < 16; c++) {
          v = r * 16 + c
          code = sprintf("%02X", v)
          if (code in st) s = st[code]
          else if (v == NR && fin != "done") s = "now"
          else s = "pending"
          if (s == "ok") printf "\033[1;32m%s\033[0m ", code
          else if (s == "no") printf "\033[31m%s\033[0m ", code
          else if (s == "wo") printf "\033[36m%s\033[0m ", code
          else if (s == "now") printf "\033[1;30;43m%s\033[0m ", code
          else printf "\033[90m%s\033[0m ", code
        }
        printf "\n"
      }
      if (fin == "done") printf "\033[KProbed all codes: %d OK, %d write-only, %d not responding, %ss\n", n, w, 256 - n - w, t
      else printf "\033[KProbing %02X ... pending   %d OK so far   %ss\n", NR, n, t
    }' "$1"
}

# run_timed "message" command args... : runs the command (stdout kept for the
# caller), with a spinner and the seconds elapsed on the terminal, then the total.
run_timed() {
  msg=$1; shift
  out=$(mktemp)
  t0=$(date +%s.%N)
  "$@" > "$out" &
  pid=$!
  if [ -t 2 ]; then
    i=0
    while kill -0 "$pid" 2> /dev/null; do
      case $((i % 4)) in 0) f='|' ;; 1) f='/' ;; 2) f='-' ;; *) f='\' ;; esac
      printf '\r%s %s %ss ' "$f" "$msg" "$(awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN {printf "%.1f", b - a}')" >&2
      i=$((i + 1))
      sleep 0.1
    done
    printf '\r\033[K' >&2
  fi
  wait "$pid"
  printf '%s: %ss\n' "$msg" "$(awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN {printf "%.1f", b - a}')" >&2
  cat "$out"; rm -f "$out"
}

if [ ! -s "$codes_file" ]; then
  echo "First run: finding which codes the monitor answers (all 256, about 20 s) ..."
  probe_codes
  [ -s "$codes_file" ] || { rm -f "$codes_file"; echo "The monitor did not answer: is it showing this computer's input?" >&2; exit 1; }
  echo "$(wc -l < "$codes_file") codes answer; they are read on their own from now on."
fi

n=0
label() { printf 'walk-%02d' "$1"; }
run_timed "Read the monitor" fast_scan > "snapshots/$(label 0).txt"
printf '\n# Walk started %s\n' "$(date '+%Y-%m-%d %H:%M')" >> "$log"
while :; do
  printf '\nSetting you are about to change (e.g. "Gamma 2.2 -> 1.8"; Enter or "done" to stop): '
  read -r name
  case "$name" in "" | done | quit | q | exit) break ;; esac
  printf 'Change it on the monitor, then press Enter. '
  read -r _
  n=$((n + 1))
  run_timed "Read the monitor" fast_scan > "snapshots/$(label "$n").txt"
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
