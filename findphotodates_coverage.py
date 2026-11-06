"""Coverage report: which photos and videos have a copy on another drive.

Reads every file list and, for each drive, counts the photos and videos that
are the only copy anywhere, the ones whose only other copies are on drives
marked weak (failing or about to be retired), and the ones that are safe.
It also warns when a drive holding the only copy of anything has not been
connected recently enough for Backblaze to still have its backup.

Two files are the same photo or video when they have the same name and size,
and the same date taken when both have one. Lightroom's "YYYYMMDD-" import
prefix is ignored, so an imported photo matches its original from the card.
"""

from __future__ import annotations

import csv
import datetime as _datetime
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PHOTO_EXTENSIONS = frozenset({
    "jpg", "jpeg", "nef", "orf", "cr2", "cr3", "arw", "dng", "rw2", "raf", "srw",
    "pef", "x3f", "tif", "tiff", "heic", "heif", "psd", "png", "webp", "avif", "jxl",
})
# Below this size these are almost always icons and web graphics, or (.ts)
# TypeScript source files rather than video.
_SMALL_IMAGE_EXTENSIONS = frozenset({"png", "webp", "avif", "ts"})
_SMALL_IMAGE_BYTES = 100_000
VIDEO_EXTENSIONS = frozenset({
    "mp4", "mov", "avi", "mkv", "wmv", "flv", "webm", "m4v", "3gp", "mts", "m2ts",
    "vob", "mpg", "mpeg", "ts",
})

ROLE_AUTO = "auto"
ROLE_KEEP = "keep"
ROLE_WEAK = "weak"
ROLE_GONE = "gone"
ROLES = (ROLE_AUTO, ROLE_KEEP, ROLE_WEAK, ROLE_GONE)
ROLE_LABELS = {
    ROLE_AUTO: "Automatic",
    ROLE_KEEP: "Keep",
    ROLE_WEAK: "Weak / retiring",
    ROLE_GONE: "Gone (wiped or lost)",
}

# Backblaze Personal keeps a disconnected drive's files for as long as the
# account's version history: 30 days by default, or 1 year as an upgrade.
BACKBLAZE_HISTORY_CHOICES = {30: "30 days", 365: "1 year"}
DEFAULT_BACKBLAZE_HISTORY_DAYS = 30
BACKBLAZE_WARN_DAYS_BEFORE = 7

# Top-level folders whose files the duplicate cleanup never deletes, besides
# library-style day folders (YYYY/YYYY-MM-DD).
DEFAULT_KEEP_FOLDERS = ("Important",)

_LIGHTROOM_PREFIX = re.compile(r"^\d{8}-")
_BAD_DATE_PREFIXES = ("0000", "1904", "1970")
# Windows Phone Link shows phone photos here; they are not stored on the PC.
_PLACEHOLDER_FOLDERS = re.compile(r"(^|[/\\])CrossDevice[/\\]", re.IGNORECASE)
_SYSTEM_FOLDERS = re.compile(
    r"(^|[/\\])(\$RECYCLE\.BIN|System Volume Information|\.Trash-\d+|\.Trashes"
    r"|\.Spotlight-V100|\.fseventsd|AppData|node_modules|site-packages|\.git|\.cache)([/\\]|$)"
    r"|^(Windows|Program Files|Program Files \(x86\)|ProgramData)/", re.IGNORECASE)
_MAC_RESOURCE_FORK = re.compile(r"(^|[/\\])\._[^/\\]*$")


def media_kind(name):
    """'photo', 'video', or '' for anything else."""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension in PHOTO_EXTENSIONS:
        return "photo"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return ""


def match_name(path):
    """File name used for matching copies: lower case, Lightroom prefix removed."""
    name = re.split(r"[/\\]", path)[-1].lower()
    return _LIGHTROOM_PREFIX.sub("", name)


def usable_date(value):
    """Date taken as 'YYYY:MM:DD HH:MM:SS', or '' when missing or a placeholder date."""
    value = (value or "").strip()[:19]
    if not value or value.startswith(_BAD_DATE_PREFIXES):
        return ""
    return value.replace("-", ":", 2)


def is_placeholder(path, flags=""):
    """True for files whose data is not actually on the drive."""
    return "placeholder" in flags or bool(_PLACEHOLDER_FOLDERS.search(path))


@dataclass
class MediaRow:
    path: str  # as recorded in the list
    relative: str  # relative to the list's root, with "/" separators
    name: str  # match_name()
    size: int
    date: str  # usable_date()
    mtime_ns: int
    kind: str  # 'photo' or 'video'
    placeholder: bool
    in_archive: bool  # a file inside a .zip/.tar; it can't be deleted on its own


def list_root(path):
    """The inventory_root recorded in a list's header, or ''."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.startswith("#"):
                    break
                if line.startswith("# inventory_root="):
                    return line.split("=", 1)[1].rstrip("\r\n")
    except OSError:
        pass
    return ""


def relative_path(path, root):
    if root and path.startswith(root):
        path = path[len(root):]
    return path.replace("\\", "/").lstrip("/")


def _split_rows(handle):
    """Data rows of a list as lists of fields, header row first. Plain tab
    splitting is several times faster than csv; csv is used only for the rare
    line with quoted fields."""
    for line in handle:
        if line.startswith("#"):
            continue
        if '"' in line:
            yield next(csv.reader([line], delimiter="\t"))
        else:
            yield line.rstrip("\r\n").split("\t")


def iter_media(path):
    """Photos and videos in one file list (MediaRow), skipping system and trash folders."""
    for fields in _media_tuples(path):
        yield MediaRow(*fields)


def _media_tuples(path):
    """iter_media() as plain tuples in MediaRow field order; faster for big lists."""
    root = list_root(path)
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        rows = _split_rows(handle)
        header = next(rows, None)
        if not header or "filepath" not in header:
            return
        column = {name: i for i, name in enumerate(header)}
        c_path, c_date = column["filepath"], column.get("date_taken")
        c_size, c_mtime = column.get("size_bytes"), column.get("mtime_ns")
        c_flags = column.get("flags")
        width = max(i for i in (c_path, c_date, c_size, c_mtime) if i is not None) + 1
        for fields in rows:
            if len(fields) < width:
                continue
            filepath = fields[c_path]
            kind = media_kind(filepath)
            if not kind or _MAC_RESOURCE_FORK.search(filepath):
                continue
            relative = relative_path(filepath, root)
            if _SYSTEM_FOLDERS.search(relative):
                continue
            try:
                size = int(fields[c_size])
            except (TypeError, ValueError):
                continue
            if size <= 0 or (size < _SMALL_IMAGE_BYTES and filepath.rsplit(".", 1)[-1].lower()
                                 in _SMALL_IMAGE_EXTENSIONS):
                continue
            try:
                mtime = int(fields[c_mtime])
            except (TypeError, ValueError):
                mtime = 0
            flags = fields[c_flags] if c_flags is not None and c_flags < len(fields) else ""
            yield (filepath, relative, match_name(filepath), size,
                   usable_date(fields[c_date] if c_date is not None else ""), mtime, kind,
                   is_placeholder(filepath, flags), "archive" in flags)


# --- Settings ---------------------------------------------------------------


@dataclass
class DriveSetting:
    role: str = ROLE_AUTO
    backblaze: bool = False
    last_connected: str = ""  # YYYY-MM-DD


@dataclass
class CoverageSettings:
    backblaze_history_days: int = DEFAULT_BACKBLAZE_HISTORY_DAYS
    drives: dict = field(default_factory=dict)  # list file name -> DriveSetting
    keep_folders: list = field(default_factory=lambda: list(DEFAULT_KEEP_FOLDERS))
    keep_library_days: bool = True

    def drive(self, list_name):
        return self.drives.setdefault(list_name, DriveSetting())

    @classmethod
    def from_config(cls, config):
        data = config.get("coverage") or {}
        days = data.get("backblaze_history_days", DEFAULT_BACKBLAZE_HISTORY_DAYS)
        if days not in BACKBLAZE_HISTORY_CHOICES:
            days = DEFAULT_BACKBLAZE_HISTORY_DAYS
        settings = cls(backblaze_history_days=days)
        folders = data.get("keep_folders")
        if isinstance(folders, list):
            settings.keep_folders = [str(f) for f in folders if str(f).strip()]
        settings.keep_library_days = bool(data.get("keep_library_days", True))
        for name, value in (data.get("drives") or {}).items():
            if not isinstance(value, dict):
                continue
            role = value.get("role", ROLE_AUTO)
            settings.drives[name] = DriveSetting(
                role=role if role in ROLES else ROLE_AUTO,
                backblaze=bool(value.get("backblaze", False)),
                last_connected=str(value.get("last_connected", "") or ""),
            )
        return settings

    def to_config(self, config):
        config["coverage"] = {
            "backblaze_history_days": self.backblaze_history_days,
            "keep_folders": list(self.keep_folders),
            "keep_library_days": self.keep_library_days,
            "drives": {
                name: {"role": s.role, "backblaze": s.backblaze,
                       "last_connected": s.last_connected}
                for name, s in sorted(self.drives.items())
            },
        }
        return config


def record_connected(settings, file_lists, today=None):
    """Note today's date for every list whose drive is connected. Returns True
    if anything changed."""
    today = (today or _datetime.date.today()).isoformat()
    changed = False
    for fl in file_lists:
        if fl.drive is None:
            continue
        setting = settings.drive(fl.path.name)
        if setting.last_connected != today:
            setting.last_connected = today
            changed = True
    return changed


# --- Health -----------------------------------------------------------------


def latest_health(fl, health_rows):
    """The newest health-log row for the drive a file list describes."""
    latest = None
    for row in health_rows:
        ids = [i for i in (row.get("volume_ids") or "").split(",") if i]
        names = [n.strip() for n in (row.get("drive") or "").split(",")]
        if (ids and set(ids) & set(fl.volume_ids)) or (
            not ids and fl.label and fl.label in names
        ):
            latest = row
    return latest


def health_is_weak(row):
    """True when a health-log row shows signs the drive itself is failing.

    Cable and USB connection errors alone don't count: they are usually the
    cable, not the drive.
    """
    if not row:
        return False
    if row.get("health") == "FAILING":
        return True
    if row.get("check") == "file read" and row.get("health") == "WARNING":
        return True
    for key in ("pending_sectors", "uncorrectable_sectors", "read_errors", "ssd_media_errors"):
        try:
            if float(row.get(key) or 0) > 0:
                return True
        except ValueError:
            pass
    return False


# --- Coverage ---------------------------------------------------------------


def list_display_name(fl):
    """A file list's drive name: its volume label, or the file name without the ID."""
    if fl.label:
        return fl.label
    return re.sub(r"\s*\([0-9A-Fa-f-]{4,}\)$", "", fl.path.stem)


@dataclass
class Tally:
    files: int = 0
    bytes: int = 0

    def add(self, size):
        self.files += 1
        self.bytes += size


@dataclass
class DriveCoverage:
    list_path: Path
    name: str
    role: str  # ROLE_KEEP or ROLE_WEAK after "automatic" is resolved
    role_reason: str
    connected: bool
    setting: DriveSetting
    total: Tally = field(default_factory=Tally)
    safe: Tally = field(default_factory=Tally)  # a copy is on a drive marked keep
    weak_only: Tally = field(default_factory=Tally)  # copies only on weak drives
    only_copy: Tally = field(default_factory=Tally)  # no copy on any other drive
    placeholders: Tally = field(default_factory=Tally)
    only_copy_folders: Counter = field(default_factory=Counter)  # folder -> bytes
    only_copy_folder_files: Counter = field(default_factory=Counter)
    backblaze_status: str = ""  # '', 'ok', 'soon', 'expired', 'unprotected'
    backblaze_message: str = ""

    @property
    def at_risk(self):
        """Photos and videos lost if this drive failed today."""
        return Tally(self.only_copy.files + self.weak_only.files,
                     self.only_copy.bytes + self.weak_only.bytes)

    @property
    def can_retire(self):
        return self.only_copy.files == 0 and self.weak_only.files == 0


def _folder_of(relative, depth=4):
    parts = relative.split("/")[:-1]
    return "/".join(parts[:depth]) or "(top level)"


def _days_since(day_text, today):
    try:
        day = _datetime.date.fromisoformat(day_text)
    except ValueError:
        return None
    return (today - day).days


def backblaze_status(coverage, history_days, today):
    """Set coverage.backblaze_status/message for a drive."""
    at_risk = coverage.at_risk
    setting = coverage.setting
    if not setting.backblaze:
        if at_risk.files:
            coverage.backblaze_status = "unprotected"
            coverage.backblaze_message = (
                "Not backed up by Backblaze, and it holds photos or videos that are on no "
                "other healthy drive.")
        return
    if coverage.connected:
        coverage.backblaze_status = "ok"
        coverage.backblaze_message = "Connected now, so Backblaze can back it up."
        return
    last = setting.last_connected
    if not last:
        try:
            last = _datetime.date.fromtimestamp(coverage.list_path.stat().st_mtime).isoformat()
        except OSError:
            last = ""
    days = _days_since(last, today) if last else None
    history = BACKBLAZE_HISTORY_CHOICES.get(history_days, f"{history_days} days")
    if days is None:
        coverage.backblaze_status = "soon"
        coverage.backblaze_message = (
            "Not seen connected yet. Connect it so Backblaze can back it up.")
        return
    left = history_days - days
    if left < 0:
        coverage.backblaze_status = "expired"
        coverage.backblaze_message = (
            f"Last connected {days} days ago. With {history} of version history, Backblaze "
            "has probably removed this drive's backup. Connect it and let Backblaze "
            "finish a backup.")
    elif left <= BACKBLAZE_WARN_DAYS_BEFORE:
        coverage.backblaze_status = "soon"
        coverage.backblaze_message = (
            f"Last connected {days} days ago. Connect it within {left} day"
            f"{'s' if left != 1 else ''}, or Backblaze will start removing its backup.")
    else:
        coverage.backblaze_status = "ok"
        coverage.backblaze_message = (
            f"Last connected {days} day{'s' if days != 1 else ''} ago; Backblaze keeps "
            f"its backup for {left} more days.")


def build_coverage(file_lists, settings, health_rows=(), today=None, progress=None):
    """Coverage of every file list. Returns [DriveCoverage], most at risk first.

    *file_lists* are findphotodates FileList objects (``fl.drive`` set when the
    drive is connected). *progress* is called with (step, total, list name).
    """
    today = today or _datetime.date.today()
    lists = list(file_lists)
    drives = build_roles(lists, settings, health_rows)

    keep_mask = gone_mask = 0
    for index, coverage in enumerate(drives):
        if coverage.role == ROLE_KEEP:
            keep_mask |= 1 << index
        elif coverage.role == ROLE_GONE:
            gone_mask |= 1 << index

    # (name, size) -> {date or "": bitmask of lists holding a real copy}
    index_by_key = defaultdict(dict)
    total_steps = len(lists) * 2
    for number, fl in enumerate(lists):
        if progress:
            progress(number + 1, total_steps, drives[number].name)
        bit = 1 << number
        if bit & gone_mask:
            continue
        for _, _, name, size, date, _, _, placeholder, _ in _media_tuples(fl.path):
            if placeholder:
                continue
            dates = index_by_key[(name, size)]
            dates[date] = dates.get(date, 0) | bit

    for number, fl in enumerate(lists):
        coverage = drives[number]
        if progress:
            progress(len(lists) + number + 1, total_steps, coverage.name)
        if coverage.role == ROLE_GONE:
            continue
        bit = 1 << number
        for _, relative, name, size, date, _, _, placeholder, _ in _media_tuples(fl.path):
            coverage.total.add(size)
            if placeholder:
                coverage.placeholders.add(size)
                continue
            dates = index_by_key.get((name, size), {})
            if date:
                holders = dates.get(date, 0) | dates.get("", 0)
            else:
                holders = 0
                for mask in dates.values():
                    holders |= mask
            others = holders & ~bit
            if others & keep_mask:
                coverage.safe.add(size)
            elif others:
                coverage.weak_only.add(size)
            else:
                coverage.only_copy.add(size)
                folder = _folder_of(relative)
                coverage.only_copy_folders[folder] += size
                coverage.only_copy_folder_files[folder] += 1
        backblaze_status(coverage, settings.backblaze_history_days, today)

    drives.sort(key=lambda c: (c.role == ROLE_GONE, -c.at_risk.bytes, c.name.lower()))
    return drives


# --- Text report ------------------------------------------------------------


def human_size(num_bytes):
    value = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            if unit == "bytes":
                return f"{int(value)} bytes"
            return f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


def _tally_text(tally):
    return f"{tally.files:,} ({human_size(tally.bytes)})"


def verdict(coverage):
    """One-line summary of a drive's coverage."""
    if coverage.role == ROLE_GONE:
        return "Gone: its list is kept for reference but doesn't count as a copy"
    if coverage.total.files == 0:
        return "No photos or videos"
    if coverage.role == ROLE_WEAK:
        if coverage.can_retire:
            return "Safe to retire: everything has a copy on a drive you keep"
        return (f"Not safe to retire: {_tally_text(coverage.at_risk)} photos/videos "
                "have no copy on a drive you keep")
    if coverage.only_copy.files:
        return f"Only copy of {_tally_text(coverage.only_copy)} photos/videos"
    if coverage.weak_only.files:
        return f"{_tally_text(coverage.weak_only)} photos/videos also only on weak drives"
    return "Every photo and video here has a copy on another drive"


def format_drive(coverage, folders=8):
    """Plain-text details for one drive."""
    status = "connected" if coverage.connected else "not connected"
    role = ROLE_LABELS[coverage.role]
    lines = [f"{coverage.name}  ({status}; {role}, {coverage.role_reason})",
             f"  {verdict(coverage)}"]
    if coverage.total.files:
        lines.append(f"  Photos and videos: {_tally_text(coverage.total)}")
        lines.append(f"    copy on a drive you keep:  {_tally_text(coverage.safe)}")
        if coverage.weak_only.files:
            lines.append(f"    copies only on weak drives: {_tally_text(coverage.weak_only)}")
        lines.append(f"    only copy anywhere:        {_tally_text(coverage.only_copy)}")
        if coverage.placeholders.files:
            lines.append(f"    placeholders (not really stored here): "
                         f"{_tally_text(coverage.placeholders)}")
    if coverage.backblaze_message:
        marker = "!" if coverage.backblaze_status in ("soon", "expired", "unprotected") else "-"
        lines.append(f"  {marker} Backblaze: {coverage.backblaze_message}")
    if coverage.only_copy_folders:
        lines.append("  Folders holding the only copy:")
        for folder, size in coverage.only_copy_folders.most_common(folders):
            files = coverage.only_copy_folder_files[folder]
            lines.append(f"    {human_size(size):>9}  {files:>7,} files  {folder}")
    return "\n".join(lines)


def format_report(drives, settings, folders_per_drive=8):
    history = BACKBLAZE_HISTORY_CHOICES.get(settings.backblaze_history_days)
    lines = [
        "Coverage report: do your photos and videos have a second copy?",
        f"Backblaze version history: {history}",
        "",
    ]
    for coverage in drives:
        lines.append(format_drive(coverage, folders_per_drive))
        lines.append("")
    return "\n".join(lines)


# --- Duplicate cleanup ------------------------------------------------------

_LIBRARY_DAY = re.compile(r"(^|/)((?:19|20)\d\d)/\2-\d\d-\d\d(/|$)")


def in_keep_folder(relative, keep_folders=DEFAULT_KEEP_FOLDERS, library_days=True):
    """True for files the cleanup never deletes and whose copies count as kept:
    library-style day folders (YYYY/YYYY-MM-DD) and the named top-level folders."""
    top = relative.split("/", 1)[0].lower()
    if any(top == folder.strip().strip("/").lower() for folder in keep_folders if folder.strip()):
        return True
    return library_days and bool(_LIBRARY_DAY.search(relative))


@dataclass
class CleanupItem:
    path: str  # where the duplicate is now
    size: int
    date: str
    kept: list  # [(path of the kept copy, True if its drive is connected)]


@dataclass
class CleanupPlan:
    drive_name: str
    items: list = field(default_factory=list)
    folders: Counter = field(default_factory=Counter)  # folder -> bytes
    folder_files: Counter = field(default_factory=Counter)
    kept_without_copy: int = 0

    @property
    def total_bytes(self):
        return sum(item.size for item in self.items)


def _current_path(fl, relative):
    """Where a file from a list is now: under the drive's current mount, or
    the recorded path when the drive isn't connected."""
    if fl.drive is not None:
        return str(Path(fl.drive.mount).joinpath(*relative.split("/")))
    return None


def plan_cleanup(file_lists, settings, target, keep_folders=DEFAULT_KEEP_FOLDERS,
                 library_days=True, health_rows=(), progress=None):
    """Photos and videos on the *target* list's drive that are outside keep
    folders and have a copy in a keep folder on another drive you keep.

    Same file = same name (Lightroom prefix ignored) and size, and the same date
    taken; when neither has a date taken, the modification time must agree
    within two seconds. The target drive must be connected.
    """
    lists = list(file_lists)
    target_fl = next(fl for fl in lists if fl.path == Path(target))
    if target_fl.drive is None:
        raise ValueError(f"{list_display_name(target_fl)} isn't connected.")
    roles = {d.list_path: d.role for d in
             build_roles(lists, settings, health_rows)}
    kept = defaultdict(list)  # (name, size) -> [(date, mtime, path, connected)]
    others = [fl for fl in lists if fl is not target_fl and roles[fl.path] == ROLE_KEEP]
    for number, fl in enumerate(others, 1):
        if progress:
            progress(number, len(others) + 1, list_display_name(fl))
        for path, relative, name, size, date, mtime, _, placeholder, archived in \
                _media_tuples(fl.path):
            # A copy inside an archive can't be checked on disk, so it doesn't count here.
            if placeholder or archived or not in_keep_folder(relative, keep_folders,
                                                             library_days):
                continue
            now = _current_path(fl, relative)
            kept[(name, size)].append((date, mtime, now or path, now is not None))

    plan = CleanupPlan(drive_name=list_display_name(target_fl))
    if progress:
        progress(len(others) + 1, len(others) + 1, plan.drive_name)
    for _, relative, name, size, date, mtime, _, placeholder, archived in \
            _media_tuples(target_fl.path):
        if placeholder or archived or in_keep_folder(relative, keep_folders, library_days):
            continue
        matches = [(p, connected) for d, m, p, connected in kept.get((name, size), ())
                   if (date and d == date) or (not date and not d and abs(m - mtime) <= 2e9)]
        if not matches:
            plan.kept_without_copy += 1
            continue
        plan.items.append(CleanupItem(_current_path(target_fl, relative), size, date, matches))
        folder = _folder_of(relative, depth=3)
        plan.folders[folder] += size
        plan.folder_files[folder] += 1
    return plan


def build_roles(file_lists, settings, health_rows=()):
    """DriveCoverage objects with roles resolved, without reading the lists."""
    drives = []
    for fl in file_lists:
        setting = settings.drive(fl.path.name)
        if setting.role == ROLE_AUTO:
            health = latest_health(fl, health_rows)
            if health_is_weak(health):
                role = ROLE_WEAK
                reason = f"health check {health.get('date', '')[:10]}: {health.get('health', '')}"
            else:
                role, reason = ROLE_KEEP, "automatic"
        else:
            role, reason = setting.role, "set by you"
        drives.append(DriveCoverage(list_path=fl.path, name=list_display_name(fl), role=role,
                                    role_reason=reason, connected=fl.drive is not None,
                                    setting=setting))
    return drives


CLEANUP_LOG_COLUMNS = ["time", "status", "deleted", "size", "date_taken", "kept copy"]


def _remove(path):
    """Delete a file, clearing a read-only flag on it or its folder if that's
    what stands in the way (common on folders copied from camera cards)."""
    import os
    import stat

    try:
        os.remove(path)
    except PermissionError:
        folder = os.path.dirname(path)
        os.chmod(folder, os.stat(folder).st_mode | stat.S_IWUSR)
        os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
        os.remove(path)


def run_cleanup(plan, log_path, trust_lists=False, progress=None, stop=None):
    """Delete the plan's duplicates. Each kept copy is checked on disk first
    (same size) when its drive is connected; when it isn't, the file is kept
    unless *trust_lists* is True. Every file is logged. Returns a Counter of
    statuses and the bytes freed."""
    import os
    import time

    from findphotodates import is_placeholder_stat

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    new = not log_path.exists()
    counts, freed = Counter(), 0
    with open(log_path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        if new:
            writer.writerow(CLEANUP_LOG_COLUMNS)
        for number, item in enumerate(plan.items, 1):
            if stop is not None and stop.is_set():
                break
            keep = None
            for kept_path, connected in item.kept:
                if connected:
                    try:
                        kept_stat = os.stat(kept_path)
                    except OSError:
                        continue
                    # A copy that's now only in the cloud doesn't count.
                    if kept_stat.st_size == item.size and not is_placeholder_stat(kept_stat):
                        keep = kept_path
                        break
                elif trust_lists:
                    keep = kept_path
                    break
            try:
                current = os.path.getsize(item.path)
            except OSError:
                current = None
            if current is None:
                status = "already gone"
            elif current != item.size:
                status = "kept: size changed since the list was made"
            elif keep is None:
                status = ("kept: other copy not found on disk"
                          if any(c for _, c in item.kept) else
                          "kept: other copy's drive isn't connected")
            else:
                try:
                    _remove(item.path)
                    status = "deleted"
                    freed += item.size
                except OSError as exc:
                    status = f"kept: couldn't delete ({exc.strerror or exc})"
            counts[status] += 1
            writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), status, item.path, item.size,
                             item.date, keep or item.kept[0][0]])
            handle.flush()  # the log is the only record of what was deleted
            if progress and (number % 200 == 0 or number == len(plan.items)):
                progress(number, len(plan.items), freed)
    return counts, freed
