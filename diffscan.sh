#!/bin/sh
# SPDX-License-Identifier: MIT
# Show the codes whose value changed between two full scans. Only successful
# reads ("XX: VCP XX ...") are compared; failed reads are ignored.
# usage: ./diffscan.sh <labelA> <labelB>
d="$(dirname "$0")/snapshots"
a=$(mktemp); b=$(mktemp)
grep -E '^(RAW [0-9A-F]{2}: max|[0-9A-F]{2}: VCP [0-9A-F]{2} [^E])' "$d/$1.txt" > "$a"
grep -E '^(RAW [0-9A-F]{2}: max|[0-9A-F]{2}: VCP [0-9A-F]{2} [^E])' "$d/$2.txt" > "$b"
diff "$a" "$b" | grep '^[<>]'
rm -f "$a" "$b"
