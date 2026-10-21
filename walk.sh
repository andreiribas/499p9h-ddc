#!/bin/sh
# SPDX-License-Identifier: MIT
# Guided search for the VCP code behind each setting of the monitor's own menu.
# It only READS codes. Each round: name ONE setting, change it with the monitor's
# buttons, press Enter; the script reads the monitor again and shows which codes
# changed. Results are appended to snapshots/walk-log.md, which is also how it knows
# what is done. The settings to look for are in snapshots/walk-todo.txt.
# The log and the scans are your own findings for your own monitor: they are
# git-ignored on purpose. snapshots/walk-todo.txt is a generic list you can edit.
#
#   ./walk.sh                      suggests the next setting to look for (one round per run)
#   ./walk.sh list                 the settings still to look for, and what the log says
#   ./walk.sh "Name" | 3           walk that setting (by name, or its number in the list)
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
todo_file=snapshots/walk-todo.txt
cache="${XDG_CACHE_HOME:-$HOME/.cache}/monitor-ctl/bus"
if [ -r "$cache" ]; then target="--bus $(cat "$cache")"; else target="--model '${MONITOR_MODEL:-PHL 499P9}'"; fi

# The codes the `monitor` command already controls, to tell them from new finds.
known_name() {
  case "$1" in
    10) echo "monitor brightness" ;; 12) echo "monitor contrast" ;; 14) echo "monitor preset" ;;
    16 | 18 | 1A) echo "monitor gain-red|green|blue" ;; 60) echo "monitor input" ;;
    62) echo "monitor volume" ;; 72) echo "monitor gamma" ;; 86) echo "monitor scaling" ;;
    87) echo "monitor sharpness" ;; 8D) echo "monitor mute" ;; A5) echo "monitor pbp" ;;
    CA) echo "monitor osd" ;; CC) echo "monitor language" ;; DC) echo "monitor mode" ;;
    F6) echo "monitor swap" ;;
  esac
}

# What `monitor` already controls, so these are not walked again.
KNOWN_LIST="brightness, contrast, sharpness, volume, mute, colour temperature, gamma, RGB gain, display mode, scaling, OSD on/off, OSD language, PBP on/off, left input, swap"

# The monitor command for a setting name that is already controlled (empty if not).
looks_known() {
  l=$(echo "$1" | tr 'A-Z' 'a-z')
  case "$l" in *smart*) return 0 ;; esac   # SmartContrast, SmartImage...: not the plain setting
  case "$l" in
    *brightness*) echo "monitor brightness" ;; *contrast*) echo "monitor contrast" ;;
    *sharpness*) echo "monitor sharpness" ;; *volume*) echo "monitor volume" ;;
    *mute*) echo "monitor mute" ;; *gamma*) echo "monitor gamma" ;;
    *"colour temp"* | *"color temp"* | *preset*) echo "monitor preset" ;;
    *gain*) echo "monitor gain-red|gain-green|gain-blue" ;; *scaling*) echo "monitor scaling" ;;
    *language*) echo "monitor language" ;; *swap*) echo "monitor swap" ;;
    *pbp*"on"* | *"picture by picture"*) echo "monitor pbp" ;;
    *"display mode"* | *smartimage*) echo "monitor mode" ;;
  esac
}

# todo_items: the names in $todo_file, in order (numbers in the list refer to these)
todo_items() { grep -v -e '^#' -e '^$' "$todo_file" 2> /dev/null; }

# item_status NAME: what the log says about that setting: found (a NEW code), known
# (only codes we already control), none (no code changed), or empty (not walked yet)
item_status() {
  awk -v n="$(echo "$1" | tr 'A-Z' 'a-z')" '
    /^## / {cur = tolower(substr($0, 4)); st_set = (cur == n); if (st_set) st = "known"; next}
    st_set && /NEW/ {st = "found"}
    st_set && /No readable/ {st = "none"}
    st_set && /^Skipped/ {st = "skipped"}
    st_set && /^Not in this/ {st = "absent"}
    END {print st}' "$log" 2> /dev/null
}

show_todo() {
  echo "Already controlled by the monitor command (no need to walk): $KNOWN_LIST"
  echo "Settings to look for (the log says what each one gave):"
  i=0
  todo_items | while IFS= read -r item; do
    i=$((i + 1))
    case "$(item_status "$item")" in
      found) mark="   [done: found a NEW code, see the log]" ;;
      known) mark="   [done: only codes we already control]" ;;
      none) mark="   [done: no DDC code]" ;;
      skipped) mark="   [skipped]" ;;
      absent) mark="   [not in this monitor's menu]" ;;
      *) mark="" ;;
    esac
    printf '  %2d) %s%s\n' "$i" "$item" "$mark"
  done
}

# resolve_name "<what was typed>": a number picks that item of the list, anything else is a name
resolve_name() {
  case "$1" in
    "" | *[!0-9]*) echo "$1" ;;
    *) todo_items | sed -n "${1}p" ;;
  esac
}

# summarize "<diff lines>": one line per changed code, "XX: before -> after", marked
# as already controlled or NEW (a code to look into).
summarize() {
  echo "$1" | awk '/^</ {c = substr($2, 1, 2); sub(/^< ..: VCP .. /, ""); old[c] = $0; if (!(c in seen)) {seen[c] = 1; order[++n] = c}}
                     /^>/ {c = substr($2, 1, 2); sub(/^> ..: VCP .. /, ""); new[c] = $0; if (!(c in seen)) {seen[c] = 1; order[++n] = c}}
                     END {for (i = 1; i <= n; i++) print order[i] "|" old[order[i]] "|" new[order[i]]}' \
  | while IFS='|' read -r c o w; do
      k=$(known_name "$c")
      if [ -n "$k" ]; then printf '  %s: %s -> %s   (already controlled: %s)\n' "$c" "$o" "$w" "$k"
      else printf '  %s: %s -> %s   <== NEW\n' "$c" "$o" "$w"; fi
    done
}

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

# next_item: the setting to suggest: the first in the list the log has nothing about,
# else one that was skipped earlier. Empty when there is nothing left.
next_item() {
  for want in "" skipped; do
    todo_items | while IFS= read -r item; do
      [ "$(item_status "$item")" = "$want" ] && { echo "$item"; break; }
    done | head -1 | grep . && return 0
  done
  return 1
}

case "${1:-}" in
  list) show_todo; exit 0 ;;
esac

# which setting: the one given (a number from `list` or a name), else the next suggested
if [ $# -gt 0 ]; then
  name=$(resolve_name "$*")
  [ -n "$name" ] || { echo "no item $* in the list (./walk.sh list)"; exit 1; }
else
  name=$(next_item) || {
    echo "Every setting in $todo_file has been walked. Add more lines there, or name one:"
    echo '  ./walk.sh "Setting name"'
    exit 0
  }
fi
known=$(looks_known "$name")
if [ -n "$known" ]; then
  printf 'That one is already controlled (%s): walking it only repeats what we know.\nWalk it anyway? [y/N] ' "$known"
  read -r yn
  case "$yn" in y | Y) ;; *) exit 0 ;; esac
fi

run_timed "Read the monitor" fast_scan > "snapshots/walk-00.txt"
printf '\n=== Next setting to look for:  %s\n' "$name"
printf 'Change it on the monitor with its buttons, then press Enter here.\n'
printf '(s + Enter: skip it for now;  x + Enter: this monitor has no such setting) '
read -r answer
case "$answer" in
  s | S) printf '\n## %s\n_%s_\nSkipped.\n' "$name" "$(date +%F)" >> "$log"; echo "Skipped. Run ./walk.sh again for another."; exit 0 ;;
  x | X) printf '\n## %s\n_%s_\nNot in this monitor'"'"'s menu.\n' "$name" "$(date +%F)" >> "$log"; echo "Noted. Run ./walk.sh again for another."; exit 0 ;;
esac
run_timed "Read the monitor" fast_scan > "snapshots/walk-01.txt"
d=$(./diffscan.sh walk-00 walk-01 | grep -vE '^[<>] (AC|AE|20):')
printf '\n## %s\n_%s_\n' "$name" "$(date +%F)" >> "$log"
if [ -n "$d" ]; then
  sum=$(summarize "$d")
  echo "Changed:"; echo "$sum"
  printf '```\n%s\n```\n' "$sum" >> "$log"
  newcode=$(echo "$sum" | awk '/<== NEW/ {sub(":", "", $1); print $1; exit}')
  if [ -n "$newcode" ]; then
    echo
    echo "To control it, add a line like this to a controls file and import it in the app"
    echo "(Options > Import controls file), or save it as \"\$(./monitor controls path)\":"
    echo "  name | $name | $newcode | choice | off=0 on=1        (use the values you saw above)"
  fi
else
  echo "No readable code changed (this setting may have no DDC code)."
  echo "No readable code changed." >> "$log"
fi
echo "Logged in $log."
if nxt=$(next_item); then echo "Run ./walk.sh again for the next one: $nxt"; else echo "That was the last one in the list."; fi
