#!/bin/sh
# SPDX-License-Identifier: MIT
# Show the codes that changed between two full scans (ERR lines ignored).
# usage: ./diffscan.sh <labelA> <labelB>
d="$(dirname "$0")/snapshots"
diff "$d/$1.txt" "$d/$2.txt" | grep '^[<>]' | grep -v ERR
