#!/usr/bin/env python3
"""Console helper for the desktop GUI. Keep long jobs off Tk's event loop."""

import argparse
import signal
import sys
from datetime import datetime
from pathlib import Path

import findphotodates as fp


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("scan", "health", "backup"))
    parser.add_argument("--directory")
    parser.add_argument("--output")
    parser.add_argument("--list-dir")
    parser.add_argument("--whole-drive", action="store_true")
    parser.add_argument("--only-media", action="store_true")
    parser.add_argument("--hash", choices=("off", "sample", "full"), default="off")
    parser.add_argument("--locate", action="store_true")
    parser.add_argument("--retry-blank-exif", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--min-image-size", type=int, default=fp.MIN_EXIFTOOL_IMAGE_BYTES)
    parser.add_argument("--path-style", choices=("auto", "linux", "windows"), default="auto")
    parser.add_argument("--extended", action="store_true")
    args = parser.parse_args(argv)

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, fp._sigterm_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, fp._sigterm_handler)

    if args.action == "scan":
        if not args.directory or not args.output:
            parser.error("scan needs --directory and --output")
        settings = fp._InteractiveSettings()
        settings.only_media = args.only_media
        settings.hash_mode = args.hash
        settings.locate = args.locate
        settings.retry_blank_exif = args.retry_blank_exif
        settings.workers = fp._clamp_worker_count(args.workers)
        settings.min_image_size = fp._clamp_min_image_size(args.min_image_size)
        settings.path_style = args.path_style
        drive = fp.identify_drive(args.directory) if args.whole_drive else None
        ok = fp._run_interactive_scan(settings, args.directory, args.output, drive=drive)
        if ok and drive and args.list_dir:
            wanted = Path(args.list_dir) / fp.canonical_list_name(drive)
            output = Path(args.output)
            if output.parent == wanted.parent and output.name != wanted.name and not wanted.exists():
                try:
                    output.rename(wanted)
                    print(f"Renamed the file list to '{wanted.name}'.")
                except OSError:
                    pass
        return 0 if ok else 1

    if args.action == "health":
        targets = fp.health_targets_for(args.directory)
        if not targets:
            print("No drives found to check.")
            return 1
        return 0 if fp.run_health_check(targets, extended=args.extended, interactive=False) else 1

    if not args.directory or not args.list_dir:
        parser.error("backup needs --directory and --list-dir")
    lists = fp.find_file_lists(args.list_dir)
    if not lists:
        print("Make a file list before checking backups.")
        return 1
    import check_photo_backups as checker

    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    name = fp._safe_filename(Path(args.directory).name or "drive")
    report = fp._unused_path(Path(args.list_dir) / "Backup checks" / f"{stamp} {name}")
    report.mkdir(parents=True)
    command = ["--target", args.directory, "--inventories", ",".join(str(fl.path) for fl in lists)]
    for flag, filename in {
        "--out-csv": "report.csv",
        "--missing-list": "not backed up.txt",
        "--safe-list": "safe to delete.txt",
        "--needs-hash-list": "matched without fingerprint.txt",
        "--no-verified-match-list": "no verified match.txt",
        "--hash-config-mismatch-list": "fingerprint settings differ.txt",
    }.items():
        command.extend((flag, str(report / filename)))
    print(f"Reports will be saved in: {report}", flush=True)
    # The checker returns 1 when files need backup. This is a useful result,
    # not an application failure; its report explains which files they are.
    sys.argv = ["check_photo_backups.py", *command]
    result = checker.main()
    print(f"Reports saved in: {report}", flush=True)
    return 0 if result in (0, 1) else result


if __name__ == "__main__":
    raise SystemExit(main())
