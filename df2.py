#!/usr/bin/env python3
"""df-like summary of photo inventory files (~/*:photo.taken.dates.txt)"""

import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


def human(size):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or u == "TB":
            return f"{size:,.1f} {u}" if u != "B" else f"{int(size):,} B"
        size /= 1024


def main():
    inv_files = sorted(Path.home().glob("*:photo.taken.dates.txt")) + sorted(
        Path.home().glob("*:photo.taken.dates.tsv")
    )

    if not inv_files:
        print("No inventory files found.")
        return

    grand_files = 0
    grand_size = 0

    # Per-drive stats
    drives = []

    for inv in inv_files:
        name = inv.name
        drive = name.split(":")[0]
        total_files = 0
        total_size = 0
        has_hash = 0
        ext_counts = defaultdict(lambda: [0, 0])  # ext -> [count, size]
        fieldnames = None

        with inv.open("r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f, delimiter="\t")
            for parts in reader:
                if not parts or parts[0].startswith("#"):
                    continue
                if len(parts) > 1 and fieldnames is None:
                    if parts[0] == "filepath":
                        fieldnames = parts
                        continue
                if fieldnames and len(parts) == len(fieldnames):
                    row = dict(zip(fieldnames, parts))
                    filepath = row.get("filepath", "")
                    try:
                        sz = int(row.get("size_bytes", "0") or "0")
                    except ValueError:
                        sz = 0
                    ch = (row.get("content_hash") or "").strip()
                    if ch:
                        has_hash += 1
                elif len(parts) == 1:
                    # old format - e.g. "./foo/bar.jpg: 2016:01:03 18:03:43"
                    m = re.match(
                        r"^(?P<path>.+?):\s+\d{4}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}\s*$",
                        parts[0],
                    )
                    filepath = m.group("path") if m else parts[0]
                    sz = 0
                else:
                    continue

                total_files += 1
                total_size += sz
                ext = os.path.splitext(filepath)[1].lower()
                ext_counts[ext][0] += 1
                ext_counts[ext][1] += sz

        top_exts = sorted(ext_counts.items(), key=lambda x: -x[1][1])[:8]
        ext_summary = ", ".join(
            f"{e or '(none)'}: {c[0]:,} ({human(c[1])})" for e, c in top_exts
        )

        drives.append((drive, total_files, total_size, has_hash, ext_summary))
        grand_files += total_files
        grand_size += total_size

    # Print table
    print(
        f"{'Drive':<6} {'Files':>12} {'Total Size':>14} {'Hashed':>10}  Top extensions"
    )
    print("-" * 100)
    for drive, nf, sz, nh, exts in drives:
        print(f"{drive:<6} {nf:>12,} {human(sz):>14} {nh:>10,}  {exts}")
    print("-" * 100)
    print(f"{'TOTAL':<6} {grand_files:>12,} {human(grand_size):>14}")


if __name__ == "__main__":
    main()
