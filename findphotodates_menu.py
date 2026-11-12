"""The interactive text menu (findphotodates.py with no options)."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from findphotodates_common import (  # noqa: F401
    _ask,
    _confirm,
    _expand_path,
    _human_size,
    _safe_filename,
    _unused_path,
    _wrap,
    update_config,
)

from findphotodates_drives import (  # noqa: F401
    unmounted_drives,
    _count_rows,
    _last_updated_label,
    canonical_list_name,
    default_inventory_dir,
    detect_drives,
    find_file_lists,
    find_legacy_lists,
    import_file_lists,
    list_belongs_to_other_drive,
    match_lists_to_drives,
    read_inventory_header,
)

from findphotodates_health import (  # noqa: F401
    detect_health_targets,
    health_log_path,
    print_health_history,
    print_unconnected_health,
    run_health_check,
)

from findphotodates_coverage import (  # noqa: F401
    _coverage_settings,
    print_coverage_report,
    remember_connected_drives,
    update_coverage_settings,
)

import findphotodates as fp


class _InteractiveSettings:
    """Advanced options chosen in the menu (they last until you quit)."""

    def __init__(self):
        self.only_media = False
        self.hash_mode = "off"
        self.locate = False
        self.retry_blank_exif = False
        self.workers = 4
        self.min_image_size = fp.MIN_EXIFTOOL_IMAGE_BYTES
        self.path_style = "auto"
        self.archives = fp.DEFAULT_ARCHIVE_MODE

    def command_line(self, directory, output):
        """The equivalent findphotodates.py command for these settings."""
        args = [os.path.basename(sys.argv[0]) or "findphotodates.py",
                "--directory", directory, "-o", str(output)]
        if self.only_media:
            args.append("--only-media")
        if self.hash_mode != "off":
            args += ["--hash", self.hash_mode]
        if self.locate:
            args.append("--locate")
        if self.retry_blank_exif:
            args.append("--retry-blank-exif")
        if self.workers != 4:
            args += ["--workers", str(self.workers)]
        if self.min_image_size != fp.MIN_EXIFTOOL_IMAGE_BYTES:
            args += ["--min-image-size", str(self.min_image_size)]
        if self.path_style != "auto":
            args.append(f"--{self.path_style}")
        if self.archives != fp.DEFAULT_ARCHIVE_MODE:
            args += ["--archives", self.archives]
        if platform.system() == "Windows":
            return "python " + subprocess.list2cmdline(args)
        import shlex

        return " ".join(shlex.quote(a) for a in args)


def _ask_folder(prompt):
    answer = _ask(prompt)
    if not answer:
        return None
    path = _expand_path(answer.strip().strip('"').strip("'"))
    if not os.path.isdir(path):
        print(f"  '{path}' is not a folder.")
        return None
    return path


def _exiftool_install_hint():
    system = platform.system()
    if system == "Windows":
        return ("Download the Windows executable from https://exiftool.org, rename "
                "'exiftool(-k).exe' to 'exiftool.exe', and put it in a folder on your PATH.")
    if system == "Darwin":
        return "Install it with Homebrew:  brew install exiftool"
    return ("Install it with your package manager, e.g.  sudo apt install "
            "libimage-exiftool-perl  (Debian/Ubuntu)  or  sudo pacman -S perl-image-exiftool  (Arch).")


# Oldest ExifTool that reads dates from every file type we send it; 12.23
# (2021) added JPEG XL, and HEIC, AVIF and Canon CR3 support came before that.
# Older versions still work, but leave dates blank for the newer formats.
EXIFTOOL_MIN_VERSION = 12.23


def _exiftool_version():
    """The installed ExifTool's version number, or None if it can't be run."""
    try:
        result = subprocess.run(["exiftool", "-ver"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip().split()[0])
    except (ValueError, IndexError):
        return 0.0  # it runs, but says something we can't read as a version


def _exiftool_available():
    return _exiftool_version() is not None


def _exiftool_status(version):
    """(usable, up_to_date, message) for an ExifTool version from
    _exiftool_version(). Only a missing or out-of-date ExifTool needs the
    download link."""
    if version is None:
        return False, False, "ExifTool is required to read photo dates. Install it before scanning."
    if version < EXIFTOOL_MIN_VERSION:
        found = f"ExifTool {version:.2f}" if version else "This ExifTool"
        return True, False, (f"{found} is out of date and may miss the dates in newer photo "
                             f"types (HEIC, CR3, JPEG XL). Version {EXIFTOOL_MIN_VERSION:.2f} "
                             f"or later is recommended.")
    return True, True, f"ExifTool {version:.2f} is ready."


_ADVANCED_HELP = {
    "include": (
        "What to include",
        "\"All files\" lists every file on the drive: photos, videos, documents, music, "
        "everything. Choose this if you want to be able to check that anything on the "
        "drive is backed up.\n"
        "\"Photos and videos only\" makes smaller lists and slightly faster scans, but "
        "other files won't be listed, so the backup checker can't vouch for them.",
    ),
    "hash": (
        "File fingerprints",
        "A fingerprint is a short code calculated from a file's contents. Two files with "
        "the same fingerprint are almost certainly identical, even if one was renamed or "
        "its date changed. The backup checker uses fingerprints for its most trustworthy "
        "\"verified\" matches, which is what you want before deleting the only other copy "
        "of a photo.\n"
        "Off: fastest; matches rely on name, size and date only.\n"
        "Quick: reads three small pieces of each file. Adds a little time and is what "
        "most people should use if they want fingerprints.\n"
        "Full: reads every byte of every file. The most certain, but on a large drive "
        "this can take many hours.\n"
        "Fingerprints are remembered, so later updates only fingerprint new or changed files.",
    ),
    "locate": (
        "Place names from GPS",
        "Phones and many cameras record where a photo was taken as GPS coordinates. "
        "Turning this on looks up a place name (like \"Point Reyes Station, California\") "
        "for each location using the free OpenStreetMap service and adds it to the list.\n"
        "It needs an internet connection and is limited to about one lookup per second, "
        "so the first run on a big collection can be slow. Place names are remembered, "
        "so photos taken near each other and later scans reuse earlier lookups.",
    ),
    "retry": (
        "Re-check files with no photo date",
        "Normally a photo or video that had no date last time is not read again as long "
        "as it hasn't changed. Turn this on if an earlier scan was interrupted, if "
        "ExifTool had a problem, or if you installed a newer ExifTool that understands "
        "more file types. Only those undated files are re-read, so it is slower than a "
        "normal update but much faster than starting over.",
    ),
    "workers": (
        "Photos read at the same time",
        "How many photos and videos are read at once. On a single USB hard drive, more "
        "doesn't help because the disk can only read one place at a time (in testing, 1 "
        "and 4 were the same speed). On an SSD or a fast network drive, 4 to 8 can be "
        "faster. The default is 4.",
    ),
    "min_size": (
        "Skip tiny images",
        "JPEG, PNG and WebP images smaller than this (100 KB by default) are almost "
        "always icons, thumbnails or web graphics with no camera date, so they are "
        "listed without being opened. Set it to 0 to open every image, which is useful "
        "if you have genuinely small photos, such as pictures from very old phones.",
    ),
    "path_style": (
        "Path style in file lists",
        "Controls how locations are written in the list: Linux style (/mnt/c/Photos/a.jpg) "
        "or Windows style (C:\\Photos\\a.jpg). Automatic keeps whatever style the list "
        "already uses. The lists work on any computer either way; only change this if "
        "another program needs one particular style.",
    ),
    "archives": (
        "Look inside archives",
        "Files inside archives are listed too, so a photo kept only in a .zip (for example "
        "a Facebook or Google Photos export) counts as a copy. Their location is the "
        "archive's location followed by the path inside it. Nothing is extracted.\n"
        ".zip only (default): reads just each .zip's table of contents, which is quick.\n"
        "All archives: also .tar and .tgz, which have to be read from start to finish; "
        "slow for large ones, and hard work for a failing drive. Unchanged archives are "
        "not read again on later scans.\n"
        "Off: list archives as single files.",
    ),
    "folder_scan": (
        "Make or update a list for one folder",
        "Lists a single folder instead of a whole drive, for example your Pictures "
        "folder, a folder on your computer's internal drive, or a network share. The "
        "list is saved in your file lists folder under a name you choose. Choosing the "
        "same name again updates that list.",
    ),
    "check": (
        "Check which files are backed up",
        "Pick a folder, such as a camera card or an old laptop's Pictures folder. The "
        "checker compares every photo and video in it against all of your file lists and "
        "reports which ones already exist on your backup drives and which don't. It "
        "writes its reports, including a list of folders that are safe to delete, to a "
        "new folder inside your file lists folder. It never deletes anything itself.",
    ),
    "import": (
        "Import file lists",
        "Moves file lists made by earlier versions (for example ~/f:photo.taken.dates.txt) "
        "or kept somewhere else into your file lists folder. Each one is renamed after its "
        "drive if that drive is connected, or \"Drive F\" and so on if it isn't; it gets "
        "its proper name the next time you update it with the drive connected.",
    ),
    "list_dir": (
        "Where file lists are kept",
        "File lists are the only record of what's on drives that are usually unplugged, "
        "so keep them somewhere that is backed up. The default is a findphotodates "
        "folder inside Documents.",
    ),
    "command": (
        "Show the equivalent command",
        "Prints the command that does the same scan with the current settings, which you "
        "can use in scripts or scheduled tasks.",
    ),
}


def _hash_label(mode):
    return {"off": "Off", "sample": "Quick", "full": "Full"}[mode]


def _run_interactive_scan(settings, directory, output, drive=None):
    if drive is not None and os.path.exists(output):
        header, _ = read_inventory_header(output)
        recorded = [i for i in header.get("volume_ids", "").split(",") if i]
        if list_belongs_to_other_drive(recorded, drive):
            owner = header.get("volume_label") or "another drive"
            print(f"\nNot scanning: '{Path(output).name}' is the file list for {owner} "
                  f"(drive ID {', '.join(recorded)}), but {drive.display_name} has drive ID "
                  f"{drive.volume_id}. Its list would have been overwritten.")
            return False
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    print()
    ok = fp.run_scan(
        directory,
        str(output),
        fp.MEDIA_EXTENSIONS if settings.only_media else None,
        settings.locate,
        False,
        False,
        hash_options=fp.parse_hash_args(hash_mode=settings.hash_mode),
        location_options=fp.parse_location_args(),
        path_style=settings.path_style,
        workers=settings.workers,
        min_image_size=settings.min_image_size,
        retry_blank_exif=settings.retry_blank_exif,
        volume_info=drive,
        archives=settings.archives,
    )
    if ok is False:
        print("\nThe scan did not finish. Anything already saved is kept; choose the "
              "same drive again to continue where it left off.")
    print()  # with the menu's own leading newline: two blank lines before the menu
    return ok is not False


def _update_drive(settings, drive, fl, list_dir):
    """Scan *drive* into its existing list *fl* (or a new list if fl is None)."""
    if fl is None:
        output = _unused_path(Path(list_dir) / canonical_list_name(drive))
    else:
        output = fl.path
    if not _run_interactive_scan(settings, drive.mount, output, drive=drive):
        return False
    # Give lists imported from older versions their drive-based name.
    wanted = Path(list_dir) / canonical_list_name(drive)
    if Path(output).parent == Path(list_dir) and Path(output).name != wanted.name and not wanted.exists():
        try:
            os.replace(output, wanted)
            print(f"Renamed the file list to '{wanted.name}'.")
        except OSError:
            pass
    return True


def _drive_table_row(values, widths):
    """Return wrapped ASCII rows, keeping every value visible."""
    import textwrap

    cells = [textwrap.wrap(str(value), width=width, break_long_words=True,
                           break_on_hyphens=False) or [""]
             for value, width in zip(values, widths)]
    height = max(map(len, cells))
    return ["| " + " | ".join((cell[line] if line < len(cell) else "").ljust(width)
                              for cell, width in zip(cells, widths)) + " |"
            for line in range(height)]


def _print_unmounted(unmounted):
    """Plugged-in drives that aren't mounted, with how to mount them."""
    for drive in unmounted:
        size = _human_size(drive.size_bytes)
        name = f"{drive.display_name} ({', '.join(filter(None, (size, drive.fstype)))})"
        if drive.dirty:
            print(f"\n  ✗ {name} is plugged in but wasn't mounted: it wasn't shut down cleanly,")
            print("    so it's marked as needing a check. To scan it, mount it read-only:")
            print(f"      udisksctl mount -b {drive.device} -o ro")
            print("    To fix it, plug it into Windows and run chkdsk on it (chkdsk X: /f).")
        else:
            print(f"\n  ○ {name} is plugged in but not mounted. To scan it, mount it:")
            print(f"      udisksctl mount -b {drive.device}")
    if unmounted:
        print("    Then choose r to look for drives again.")


def _print_drive_menu(drives, list_for, list_dir, lists, unmounted=()):
    import textwrap

    print(f"\nFile lists are kept in: {list_dir}  ({len(lists)} list{'s' if len(lists) != 1 else ''})")
    if not drives:
        print("\nNo drives found. Plug in a drive and choose r to look again, or use the")
        print("advanced menu to list a specific folder.")
    else:
        print("\nConnected drives:")
        widths = (3, 20, 9, 10, 17)
        rule = "+-" + "-+-".join("-" * width for width in widths) + "-+"
        detail_width = len(rule) - 4
        print(rule)
        for line in _drive_table_row(("#", "Drive", "Size", "Filesystem", "Updated"), widths):
            print(line)
        print(rule)
        for n, drive in enumerate(drives, 1):
            fl = list_for.get(drive.mount)
            updated = _last_updated_label(fl.path.stat().st_mtime) if fl else "Never"
            values = (n, drive.display_name, _human_size(drive.size_bytes) or "—",
                      drive.fstype or "—", updated)
            for line in _drive_table_row(values, widths):
                print(line)
            for label, value in (("Location", drive.mount),
                                 ("File list", fl.path.name if fl else
                                  f"New: {canonical_list_name(drive)}")):
                for line in textwrap.wrap(f"{label}: {value}", width=detail_width - 2,
                                          subsequent_indent="  ", break_long_words=True,
                                          break_on_hyphens=False):
                    print("| " + line.ljust(detail_width) + " |")
            print(rule)
    _print_unmounted(unmounted)
    print()
    if len(drives) > 1:
        print("  To do several drives in a row, type their numbers, e.g. 1 3 or 2-4")
        print()
    if any(d.mount in list_for for d in drives):
        print("  u) Update every connected drive that has a file list")
    print("  h) Check drive health")
    print("  c) Coverage report: which photos and videos have a second copy")
    print("  l) Show all file lists, including drives that aren't connected")
    print("  r) Look for drives again (after plugging one in)")
    print("  a) Advanced options")
    print("  q) Quit")


def _show_all_lists(lists):
    if not lists:
        print("\nNo file lists yet.")
        return
    print()
    for fl in lists:
        stat_info = fl.path.stat()
        updated = _last_updated_label(stat_info.st_mtime)
        if fl.drive:
            where = f"drive connected at {fl.drive.mount}"
        elif not fl.volume_ids and fl.root and os.path.isdir(fl.root):
            where = "folder list"
        else:
            where = "drive not connected"
        print(f"  {fl.path.name}")
        print(f"      {_count_rows(fl.path):,} files, updated {updated}, "
              f"scanned from {fl.root or 'unknown'}; {where}")


def _advanced_menu(settings, state):
    while True:
        s = settings
        items = [
            ("include", "All files" if not s.only_media else "Photos and videos only"),
            ("hash", _hash_label(s.hash_mode)),
            ("locate", "On" if s.locate else "Off"),
            ("retry", "On" if s.retry_blank_exif else "Off"),
            ("workers", str(s.workers)),
            ("min_size", f"{s.min_image_size:,} bytes" if s.min_image_size else "Off (open every image)"),
            ("path_style", {"auto": "Automatic", "linux": "Linux style", "windows": "Windows style"}[s.path_style]),
            ("archives", fp._ARCHIVE_LABELS[s.archives]),
            ("folder_scan", ""),
            ("check", ""),
            ("import", ""),
            ("list_dir", str(state["list_dir"])),
            ("command", ""),
        ]
        print("\nAdvanced options (settings last until you quit):\n")
        for n, (key, value) in enumerate(items, 1):
            title = _ADVANCED_HELP[key][0]
            print(f"  {n:>2}) {title}" + (f": {value}" if value else ""))
        print("\n   b) Back to the main menu")
        choice = _ask("\nChoose an option (type ? and a number, e.g. ?2, to read about it first): ").lower()
        if choice in ("b", ""):
            return
        explain_only = choice.startswith("?")
        choice = choice.lstrip("?").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(items):
            print("  Please choose one of the numbers shown, or b.")
            continue
        key = items[int(choice) - 1][0]
        title, text = _ADVANCED_HELP[key]
        print(f"\n  {title}\n")
        print(_wrap(text))
        if explain_only:
            continue
        print()
        _change_advanced_setting(key, settings, state)


def _pick(prompt, options):
    """Ask the user to pick one of [(label, value)]; returns value or None."""
    for n, (label, _) in enumerate(options, 1):
        print(f"     {n}) {label}")
    answer = _ask(prompt)
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1][1]
    return None


def _change_advanced_setting(key, s, state):
    list_dir = state["list_dir"]
    if key == "include":
        value = _pick("  Choose 1 or 2 (Enter to keep): ",
                      [("All files", False), ("Photos and videos only", True)])
        if value is not None:
            s.only_media = value
    elif key == "hash":
        value = _pick("  Choose 1-3 (Enter to keep): ",
                      [("Off", "off"), ("Quick", "sample"), ("Full", "full")])
        if value is not None:
            s.hash_mode = value
    elif key == "locate":
        s.locate = _confirm("  Look up place names?", default=s.locate)
    elif key == "retry":
        s.retry_blank_exif = _confirm("  Re-check undated files on the next scan?", default=s.retry_blank_exif)
    elif key == "workers":
        answer = _ask(f"  How many at once, 1-32 (Enter to keep {s.workers}): ")
        if answer.isdigit():
            s.workers = fp._clamp_worker_count(int(answer))
    elif key == "min_size":
        answer = _ask(f"  Size in KB, 0 to open every image (Enter to keep {s.min_image_size // 1000}): ")
        if answer.isdigit():
            s.min_image_size = fp._clamp_min_image_size(int(answer) * 1000)
    elif key == "path_style":
        value = _pick("  Choose 1-3 (Enter to keep): ",
                      [("Automatic", "auto"), ("Linux style", "linux"), ("Windows style", "windows")])
        if value is not None:
            s.path_style = value
    elif key == "archives":
        value = _pick("  Choose 1-3 (Enter to keep): ",
                      [(label, mode) for mode, label in fp._ARCHIVE_LABELS.items()])
        if value is not None:
            s.archives = value
    elif key == "folder_scan":
        folder = _ask_folder("  Folder to list: ")
        if folder:
            default_name = _safe_filename(os.path.basename(folder.rstrip("/\\")) or "Folder")
            name = _ask(f"  Name for this list (Enter for '{default_name}'): ") or default_name
            output = Path(list_dir) / (_safe_filename(name) + ".tsv")
            if not output.exists() or _confirm(f"  '{output.name}' exists. Update it?"):
                _run_interactive_scan(s, folder, output)
                state["dirty"] = True
    elif key == "check":
        _run_backup_check(state)
    elif key == "import":
        _interactive_import(state)
    elif key == "list_dir":
        answer = _ask("  New folder for file lists (Enter to keep the current one): ")
        if answer:
            new_dir = Path(_expand_path(answer.strip('"').strip("'")))
            old_lists = find_file_lists(list_dir)
            new_dir.mkdir(parents=True, exist_ok=True)
            update_config(lambda config: config.update(inventory_dir=str(new_dir)))
            if old_lists and _confirm(f"  Move the {len(old_lists)} existing file lists there too?"):
                for fl in old_lists:
                    shutil.move(str(fl.path), str(_unused_path(new_dir / fl.path.name)))
            state["list_dir"] = new_dir
            state["dirty"] = True
            print(f"  File lists will now be kept in {new_dir}")
    elif key == "command":
        drive = state["drives"][0] if state["drives"] else None
        directory = drive.mount if drive else "/path/to/drive"
        output = state["list_for"].get(drive.mount).path if drive and drive.mount in state["list_for"] \
            else Path(list_dir) / (canonical_list_name(drive) if drive else "My Drive.tsv")
        print("  " + s.command_line(directory, output))


def _interactive_import(state):
    list_dir = state["list_dir"]
    folder = _ask_folder("  Folder to import from (Enter for your home folder): ") or str(Path.home())
    paths = find_legacy_lists(folder)
    if not paths:
        paths = [fl.path for fl in find_file_lists(folder) if Path(folder) != Path(list_dir)]
    if not paths:
        print("  No file lists found there.")
        return
    print(f"  Found {len(paths)} file list(s):")
    for p in paths:
        print(f"     {p.name}")
    if not _confirm(f"  Move them into {list_dir}?"):
        return
    for old, new in import_file_lists(paths, list_dir, state["drives"]):
        print(f"     {old.name}  ->  {new.name}")
    state["dirty"] = True


def _run_backup_check(state):
    checker = Path(__file__).with_name("check_photo_backups.py")
    if not checker.exists():
        print(f"  The backup checker (check_photo_backups.py) should be next to this program "
              f"in {checker.parent}, but it isn't there.")
        return
    lists = find_file_lists(state["list_dir"])
    if not lists:
        print("  You need at least one file list first.")
        return
    target = _ask_folder("  Folder to check: ")
    if not target:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    target_name = os.path.basename(target.rstrip("/\\")) or "drive"
    report_dir = _unused_path(Path(state["list_dir"]) / "Backup checks" / _safe_filename(
        f"{stamp} {target_name}"))
    report_dir.mkdir(parents=True)
    outputs = {
        "--out-csv": "report.csv",
        "--missing-list": "not backed up.txt",
        "--safe-list": "safe to delete.txt",
        "--needs-hash-list": "matched without fingerprint.txt",
        "--no-verified-match-list": "no verified match.txt",
        "--hash-config-mismatch-list": "fingerprint settings differ.txt",
    }
    cmd = [sys.executable, str(checker), "--target", target,
           "--inventories", ",".join(str(fl.path) for fl in lists)]
    for flag, name in outputs.items():
        cmd += [flag, str(report_dir / name)]
    print(f"\n  Comparing {target} against {len(lists)} file list(s)...\n")
    sys.stdout.flush()
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        print("\n  Backup check stopped.")
    print(f"\n  Reports saved in: {report_dir}")
    print("  'not backed up.txt' lists files found on none of your drives; "
          "'safe to delete.txt' lists files with a trustworthy copy elsewhere.")


def _interactive_health():
    print("\nLooking for drives...")
    targets = detect_health_targets()
    if not targets:
        print("No drives found to check.")
        return
    print("\nDrives connected now:")
    for n, target in enumerate(targets, 1):
        where = ", ".join(target.mounts) if target.mounts else "not mounted"
        print(f"  {n}) {target.description()}   [{where}]")
    print_unconnected_health(targets)
    print(f"\nEvery check is added to {health_log_path()}")
    answer = _ask("\nType a number to check one drive, press Enter to check all of them, "
                  "or type h to see the health history: ").lower()
    if answer == "h":
        print_health_history()
        return
    chosen = parse_drive_choices(answer, len(targets))
    if chosen:
        targets = [targets[n - 1] for n in chosen]
    elif answer:
        print("Please choose one of the numbers shown.")
        return
    print()
    print(_wrap(
        "Quick check (a few seconds): reads the health information the drive keeps about "
        "itself, such as bad sectors, errors, temperature and age.\n"
        "Extended check (hours): does the quick check, then has the drive test its whole "
        "surface, which finds weak spots in places that are rarely read. Drives that "
        "can't test themselves, like SD cards, instead have every file read back. Worth "
        "doing once in a while for drives that hold your only copy of something.",
        indent="  "))
    extended = _ask("\nQuick or extended check? [Q/e] ").lower().startswith("e")
    run_health_check(targets, extended=extended, interactive=True)


def parse_drive_choices(text, count):
    """Numbers typed in the menu, e.g. "3", "3 4", "3,4" or "2-4".

    Returns the list of chosen drive numbers (1-based, in order, no repeats),
    or [] if the text isn't a valid selection.
    """
    chosen = []
    tokens = text.replace(",", " ").split()
    if not tokens:
        return []
    for token in tokens:
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not m:
            return []
        first = int(m.group(1))
        last = int(m.group(2)) if m.group(2) else first
        if not (1 <= first <= last <= count):
            return []
        for n in range(first, last + 1):
            if n not in chosen:
                chosen.append(n)
    return chosen


def _update_drives(settings, drives, list_for, state):
    """Update (or create) the lists for several drives in turn, then summarise."""
    print(f"\nUpdating {len(drives)} drive(s): " + ", ".join(d.display_name for d in drives))
    results = []
    for drive in drives:
        print(f"\n=== {drive.display_name} ({drive.mount}) ===")
        results.append((drive, _update_drive(settings, drive, list_for.get(drive.mount), state["list_dir"])))
    print("Summary:")
    for drive, ok in results:
        print(f"  {drive.display_name:<24} {'done' if ok else 'NOT finished'}")
    print()
    state["dirty"] = True


def _interactive_coverage(state):
    from findphotodates_coverage import (
        BACKBLAZE_HISTORY_CHOICES, ROLE_AUTO, ROLE_GONE, ROLE_KEEP, ROLE_LABELS, ROLE_WEAK,
        list_display_name,
    )

    print()
    print(_wrap(
        "The coverage report compares all your file lists. A photo or video counts as "
        "having a copy when another drive has a file with the same name and size (and the "
        "same date taken, when both have one). Drives you mark as weak (failing, or about "
        "to be retired) don't count as safe copies.", indent=""))
    print_coverage_report(lists=state["lists"], list_dir=state["list_dir"])
    lists = state["lists"]
    while True:
        settings = _coverage_settings()
        print("Settings:")
        print(f"  b) Backblaze version history: "
              f"{BACKBLAZE_HISTORY_CHOICES[settings.backblaze_history_days]}")
        print("  Drives (role / backed up by Backblaze):")
        for n, fl in enumerate(lists, 1):
            s = settings.drive(fl.path.name)
            print(f"  {n:>2}) {list_display_name(fl)}: {ROLE_LABELS[s.role]}, "
                  f"Backblaze {'yes' if s.backblaze else 'no'}")
        print("  r) Show the report again   q) Back")
        choice = _ask("Choose a setting or drive number: ").lower()
        if choice in ("q", ""):
            return
        if choice == "r":
            print_coverage_report(lists=lists, list_dir=state["list_dir"])
        elif choice == "b":
            print("Backblaze version history:")
            picked = _pick("Choose: ", [(label, days) for days, label
                                        in BACKBLAZE_HISTORY_CHOICES.items()])
            if picked is not None:
                update_coverage_settings(
                    lambda saved: setattr(saved, "backblaze_history_days", picked))
        elif choice.isdigit() and 1 <= int(choice) <= len(lists):
            fl = lists[int(choice) - 1]
            s = settings.drive(fl.path.name)
            print(f"What is {list_display_name(fl)}? (now: {ROLE_LABELS[s.role]})")
            picked = _pick("Choose (Enter keeps it): ", [
                ("Automatic: keep it unless its health check shows it failing", ROLE_AUTO),
                ("Keep: a drive you rely on", ROLE_KEEP),
                ("Weak: failing or about to be retired; copies here don't count as safe",
                 ROLE_WEAK),
                ("Gone: wiped or lost; ignore its file list", ROLE_GONE),
            ])
            backblaze = _confirm("Does Backblaze back up this drive?", default=s.backblaze)

            def change(saved, name=fl.path.name, role=picked, backblaze=backblaze):
                if role is not None:
                    saved.drive(name).role = role
                saved.drive(name).backblaze = backblaze

            update_coverage_settings(change)
        else:
            print("Please choose one of the options shown.")


def interactive_main():
    """Menu-driven mode used when the program is started with no arguments."""
    print(f"\nfindphotodates {fp.__version__}: lists of the files on your drives\n")
    usable, up_to_date, message = _exiftool_status(_exiftool_version())
    if not usable:
        print("ExifTool is needed to read the dates inside photos and videos, but it isn't installed.")
        print(_wrap(_exiftool_install_hint(), indent="  "))
        return
    if not up_to_date:
        print(_wrap(message + " Get the latest from https://exiftool.org", indent=""))
        print()
    fp.migrate_default_hash_cache(quiet=True)
    settings = _InteractiveSettings()
    state = {"list_dir": default_inventory_dir(), "dirty": True}

    try:
        legacy = [p for p in find_legacy_lists() if p.parent != state["list_dir"]]
        if legacy:
            print(f"Found {len(legacy)} file list(s) from an earlier version in {Path.home()}.")
            if _confirm(f"Move them into {state['list_dir']} and name them after their drives?"):
                print("Matching them to connected drives...")
                for old, new in import_file_lists(legacy, state["list_dir"], detect_drives()):
                    print(f"  {old.name}  ->  {new.name}")

        while True:
            if state["dirty"]:
                print("Looking for drives and file lists...")
                state["drives"] = detect_drives()
                state["lists"] = find_file_lists(state["list_dir"])
                match_lists_to_drives(state["lists"], state["drives"])
                state["list_for"] = {fl.drive.mount: fl for fl in state["lists"] if fl.drive}
                state["dirty"] = False
                remember_connected_drives(state["lists"])
                try:
                    state["unmounted"] = unmounted_drives()
                except Exception:
                    state["unmounted"] = []
            drives, list_for = state["drives"], state["list_for"]
            _print_drive_menu(drives, list_for, state["list_dir"], state["lists"],
                              state.get("unmounted", ()))
            choice = _ask("\nChoose: ").lower()
            if choice in ("q", "quit", "exit"):
                return
            if choice == "r":
                state["dirty"] = True
            elif choice == "h":
                _interactive_health()
                print()
            elif choice == "c":
                _interactive_coverage(state)
            elif choice == "l":
                _show_all_lists(state["lists"])
            elif choice == "a":
                _advanced_menu(settings, state)
            elif choice == "u" and list_for:
                _update_drives(settings, [d for d in drives if d.mount in list_for], list_for, state)
            elif parse_drive_choices(choice, len(drives)):
                chosen = [drives[n - 1] for n in parse_drive_choices(choice, len(drives))]
                if len(chosen) == 1:
                    drive = chosen[0]
                    fl = list_for.get(drive.mount)
                    if fl:
                        question = f"Update the file list for {drive.display_name}? Only new or changed files are read."
                    else:
                        print(_wrap(
                            f"This makes a new file list for {drive.display_name} named "
                            f"'{canonical_list_name(drive)}'. It records every file's name, size and "
                            "date, plus the date and GPS location inside photos and videos. The "
                            "first scan of a large drive can take a few hours; later updates only "
                            "read new or changed files and are much faster.", indent=""))
                        question = "Make the file list now?"
                    if _confirm(question):
                        _update_drive(settings, drive, fl, state["list_dir"])
                        state["dirty"] = True
                else:
                    print()
                    for drive in chosen:
                        action = "update its file list" if drive.mount in list_for else "make a new file list"
                        print(f"  {drive.display_name}: {action}")
                    if _confirm(f"Go ahead with these {len(chosen)} drives, one after another?"):
                        _update_drives(settings, chosen, list_for, state)
            else:
                print("Please choose one of the options shown.")
    except KeyboardInterrupt:
        print("\nBye.")
