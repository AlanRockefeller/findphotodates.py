#!/usr/bin/env python3
"""A df-like summary of your file lists: how many files, how much data, and
how many photos and videos each drive holds, without connecting any drive.

Reads every list in the file lists folder (Documents/findphotodates unless you
chose another one), plus lists from older versions in your home folder
(~/*:photo.taken.dates.txt). Pass list files or folders to summarise those
instead:

    ./df2.py
    ./df2.py "~/Documents/findphotodates/Sierra Club (5F61-DDF5).tsv"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from findphotodates_common import split_list_lines

PHOTO_EXTENSIONS = {
    "jpg", "jpeg", "nef", "orf", "cr2", "cr3", "arw", "dng", "rw2", "raf", "srw", "pef",
    "x3f", "tif", "tiff", "heic", "heif", "psd", "png", "webp", "avif", "jxl",
}
VIDEO_EXTENSIONS = {
    "mp4", "mov", "avi", "mkv", "wmv", "flv", "webm", "m4v", "3gp", "mts", "m2ts", "vob",
    "mpg", "mpeg",
}
_LEGACY_GLOBS = ("*:photo.taken.dates.txt", "*_photo.taken.dates.txt",
                 "*:photo.taken.dates.tsv", "*_photo.taken.dates.tsv")
# "f:photo.taken.dates.txt" (or "f_..." where ":" can't be used) is drive F's list.
_LEGACY_NAME = re.compile(r"^(?P<drive>.+?)[:_]photo\.taken\.dates\.(?:txt|tsv)$")
_OLD_FORMAT = re.compile(r"^(?P<path>.+?):\s+\d{4}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}\s*$")


def human(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1000 or unit == "TB":
            return f"{int(size):,} B" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1000
    return f"{size:,.1f} TB"


def list_dir():
    """The folder findphotodates.py keeps its file lists in."""
    try:
        from findphotodates import default_inventory_dir

        return default_inventory_dir()
    except (Exception, SystemExit):  # unreadable settings, or findphotodates.py missing
        return Path.home() / "Documents" / "findphotodates"


def find_lists(paths):
    """File lists to summarise: the given files and folders, or the defaults."""
    if not paths:
        folder = list_dir()
        found = sorted(folder.glob("*.tsv"), key=lambda p: p.name.lower())
        for pattern in _LEGACY_GLOBS:
            if os.name == "nt" and ":" in pattern:
                continue  # can't be in a Windows file name; pathlib reads "*:" as a drive
            found += sorted(Path.home().glob(pattern))
        return [p for p in found if p.name != "Drive health log.tsv"]
    found = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            found += sorted(path.glob("*.tsv"), key=lambda p: p.name.lower())
        else:
            found.append(path)
    return found


def summarise(path):
    """A dict of totals for one list, or None if it isn't a file list."""
    headers = {}
    totals = {"files": 0, "bytes": 0, "media": 0, "media_bytes": 0, "hashed": 0,
              "placeholders": 0, "in_archives": 0, "extensions": Counter()}
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        first = ""
        for line in handle:  # header comments, then the column names or old-format rows
            if line.startswith("#"):
                key, sep, value = line.lstrip("# ").rstrip("\r\n").partition("=")
                if sep:
                    headers[key] = value
                continue
            if line.strip():
                first = line
                break
        if first.startswith("filepath\t"):
            rows = split_list_lines(_prepend(first, handle))
            columns = {name: i for i, name in enumerate(next(rows))}
            for fields in rows:
                _add_row(totals, fields, columns)
        elif _OLD_FORMAT.match(first.strip()):
            # Old format: "./path/file.jpg: 2016:01:03 18:03:43", no sizes.
            for line in _prepend(first, handle):
                match = _OLD_FORMAT.match(line.strip())
                if match:
                    _add_row(totals, [match.group("path")], {"filepath": 0})
        else:
            return None
    legacy = _LEGACY_NAME.match(path.name)
    totals["name"] = (headers.get("volume_label")
                      or (legacy.group("drive") if legacy else None)
                      or re.sub(r"\s*\([0-9A-Fa-f-]{4,}\)$", "", path.stem))
    totals["updated"] = datetime.fromtimestamp(path.stat().st_mtime)
    return totals


def _prepend(first, rest):
    yield first
    yield from rest


def _add_row(totals, fields, columns):
    def field(name):
        index = columns.get(name)
        return fields[index] if index is not None and index < len(fields) else ""

    filepath = field("filepath")
    if not filepath:
        return
    try:
        size = int(field("size_bytes") or 0)
    except ValueError:
        size = 0
    flags = field("flags")
    if "archive" in flags:
        totals["in_archives"] += 1  # its bytes are already counted in the archive
        size = 0
    totals["files"] += 1
    totals["bytes"] += size
    name = re.split(r"[/\\]", filepath)[-1]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    totals["extensions"][extension] += 1
    if extension in PHOTO_EXTENSIONS or extension in VIDEO_EXTENSIONS:
        totals["media"] += 1
        totals["media_bytes"] += size
    if field("content_hash").strip():
        totals["hashed"] += 1
    if "placeholder" in flags:
        totals["placeholders"] += 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("lists", nargs="*", help="file lists or folders (default: yours)")
    args = parser.parse_args(argv)

    summaries = []
    for path in find_lists(args.lists):
        try:
            summary = summarise(path)
        except OSError as exc:
            print(f"Couldn't read {path}: {exc.strerror or exc}", file=sys.stderr)
            continue
        if summary is not None:
            summaries.append(summary)
    if not summaries:
        print("No file lists found.")
        return 1

    width = max(12, *(len(s["name"]) for s in summaries))
    header = (f"{'Drive':<{width}}  {'Files':>11}  {'Size':>10}  {'Photos+videos':>13}  "
              f"{'Their size':>10}  {'Hashed':>9}  {'Updated':<10}  Most common types")
    print(header)
    print("-" * len(header))
    for s in summaries:
        common = ", ".join(f"{ext or '(none)'} {count:,}"
                           for ext, count in s["extensions"].most_common(4))
        notes = []
        if s["placeholders"]:
            notes.append(f"{s['placeholders']:,} cloud-only")
        if s["in_archives"]:
            notes.append(f"{s['in_archives']:,} inside archives")
        print(f"{s['name']:<{width}}  {s['files']:>11,}  {human(s['bytes']):>10}  "
              f"{s['media']:>13,}  {human(s['media_bytes']):>10}  {s['hashed']:>9,}  "
              f"{s['updated']:%Y-%m-%d}  {common}" + (f"  ({'; '.join(notes)})" if notes else ""))
    print("-" * len(header))
    print(f"{'Total':<{width}}  {sum(s['files'] for s in summaries):>11,}  "
          f"{human(sum(s['bytes'] for s in summaries)):>10}  "
          f"{sum(s['media'] for s in summaries):>13,}  "
          f"{human(sum(s['media_bytes'] for s in summaries)):>10}")
    print("\nTotals count every copy: a photo on three drives is counted three times.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
