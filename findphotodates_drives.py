"""Connected drives and the file lists that describe them."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import string

from datetime import datetime
from pathlib import Path

from findphotodates_common import (  # noqa: F401
    _is_wsl,
    _normalize_cache_key,
    _root_cache_prefix,
    _run_quiet,
    _safe_filename,
    _unused_path,
    load_config,
)

# ---------------------------------------------------------------------------
# Interactive mode: file-list folder, drive detection, and menus
# ---------------------------------------------------------------------------
# Alan 9/28/26 - Running with no arguments in a terminal opens a menu that
# finds connected drives and their file lists, so the common case (update a
# drive's list, or make one for a new drive) needs no command-line flags.

INVENTORY_DIR_NAME = "findphotodates"
_LEGACY_LIST_GLOBS = (
    "*:photo.taken.dates.txt",
    "*_photo.taken.dates.txt",
    "*:photo.taken.dates.tsv",
    "*_photo.taken.dates.tsv",
)


def _legacy_list_globs():
    """The patterns that can match here. Windows file names can't contain ":",
    and pathlib on Windows reads "*:..." as a drive and raises an error."""
    if os.name == "nt":
        return tuple(pattern for pattern in _LEGACY_LIST_GLOBS if ":" not in pattern)
    return _LEGACY_LIST_GLOBS


_PSEUDO_FSTYPES = {"tmpfs", "autofs", "overlay", "squashfs", "proc", "sysfs"}


def _linux_documents_dir():
    """The XDG Documents folder (honours ~/.config/user-dirs.dirs)."""
    value = os.environ.get("XDG_DOCUMENTS_DIR")
    if not value:
        config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        try:
            with open(os.path.join(config_home, "user-dirs.dirs"), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("XDG_DOCUMENTS_DIR="):
                        value = line.split("=", 1)[1].strip().strip('"')
                        break
        except OSError:
            pass
    if value:
        path = Path(value.replace("$HOME", str(Path.home())))
        # user-dirs uses "$HOME/" to mean "disabled"; fall back in that case
        if path.is_absolute() and path != Path.home():
            return path
    return Path.home() / "Documents"


def _windows_documents_dir():
    """The real Documents folder, following OneDrive/folder redirection."""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        # CSIDL_PERSONAL (5) is "My Documents"; SHGFP_TYPE_CURRENT (0)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents"


def _wsl_documents_dir():
    """The Windows user's Documents folder as a WSL path, so lists are shared."""
    out = _run_quiet(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Environment]::GetFolderPath('MyDocuments')",
        ]
    )
    if out and out.strip():
        converted = _run_quiet(["wslpath", "-u", out.strip()])
        if converted and converted.strip():
            return Path(converted.strip())
    return None


def _platform_documents_dir():
    system = platform.system()
    if system == "Windows":
        return _windows_documents_dir()
    if system == "Darwin":
        return Path.home() / "Documents"
    if _is_wsl():
        return _wsl_documents_dir() or _linux_documents_dir()
    return _linux_documents_dir()


def default_inventory_dir():
    """Folder where file lists are kept: <Documents>/findphotodates, unless
    the user picked a different folder (saved as "inventory_dir" in the config)."""
    custom = load_config().get("inventory_dir")
    if custom:
        return Path(custom).expanduser()
    return _platform_documents_dir() / INVENTORY_DIR_NAME


class DriveInfo:
    """A mounted drive (volume) that can be scanned."""

    __slots__ = ("mount", "label", "volume_id", "fstype", "size_bytes")

    def __init__(self, mount, label="", volume_id="", fstype="", size_bytes=0):
        self.mount = mount
        self.label = (label or "").strip()
        self.volume_id = _normalize_volume_id(volume_id)
        self.fstype = fstype or ""
        self.size_bytes = int(size_bytes or 0)

    @property
    def display_name(self):
        if self.label:
            return self.label
        m = re.fullmatch(r"([A-Za-z]):\\?", self.mount) or re.fullmatch(
            r"/mnt/([A-Za-z])", self.mount
        )
        if m:
            return f"Drive {m.group(1).upper()}"
        return os.path.basename(self.mount.rstrip("/\\")) or self.mount


def _normalize_volume_id(raw):
    """Put a volume serial/UUID into one form shared by every OS.

    exFAT/FAT serials look like "1A2B-3C4D" everywhere.  Linux shows an NTFS
    serial as 16 hex digits; Windows shows only the low 32 bits, so keep those.
    """
    if not raw:
        return ""
    text = str(raw).strip().upper()
    compact = text.replace("-", "")
    if re.fullmatch(r"[0-9A-F]{16}", compact):
        compact = compact[-8:]
    if re.fullmatch(r"[0-9A-F]{8}", compact):
        return f"{compact[:4]}-{compact[4:]}"
    return text


def _disk_total(path):
    try:
        return shutil.disk_usage(path).total
    except OSError:
        return 0


def _unescape_mount_path(text):
    """Decode the octal escapes (\\040 for space etc.) used in /proc/self/mounts."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def _linux_drives(wsl=False):
    mounts = []
    seen = set()
    try:
        with open("/proc/self/mounts", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount = _unescape_mount_path(parts[1])
                if wsl:
                    if not re.fullmatch(r"/mnt/[a-zA-Z]", mount):
                        continue
                elif not mount.startswith(("/run/media/", "/media/", "/mnt/")):
                    continue
                if parts[2] in _PSEUDO_FSTYPES or mount in seen:
                    continue
                seen.add(mount)
                mounts.append((mount, parts[2]))
    except OSError:
        return []

    details = _wsl_windows_volumes() if wsl else _lsblk_by_mountpoint()
    drives = []
    for mount, fstype in mounts:
        if wsl:
            info = details.get(mount[-1].lower(), {})
            drives.append(
                DriveInfo(
                    mount,
                    info.get("VolumeName") or "",
                    info.get("VolumeSerialNumber") or "",
                    info.get("FileSystem") or fstype,
                    info.get("Size") or _disk_total(mount),
                )
            )
        else:
            info = details.get(mount, {})
            drives.append(
                DriveInfo(
                    mount,
                    info.get("label") or "",
                    info.get("uuid") or "",
                    info.get("fstype") or fstype,
                    info.get("size") or _disk_total(mount),
                )
            )
    return drives


def _lsblk_by_mountpoint():
    out = _run_quiet(["lsblk", "-J", "-b", "-o", "UUID,LABEL,FSTYPE,SIZE,MOUNTPOINT"])
    if not out:
        return {}
    try:
        stack = list(json.loads(out).get("blockdevices", []))
    except ValueError:
        return {}
    by_mount = {}
    while stack:
        dev = stack.pop()
        stack.extend(dev.get("children") or [])
        if dev.get("mountpoint"):
            by_mount[dev["mountpoint"]] = dev
    return by_mount


def _wsl_windows_volumes():
    out = _run_quiet(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,VolumeName,"
            "VolumeSerialNumber,FileSystem,Size | ConvertTo-Json -Compress",
        ]
    )
    if not out or not out.strip():
        return {}
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    if isinstance(data, dict):
        data = [data]
    return {
        d["DeviceID"][0].lower(): d
        for d in data
        if isinstance(d, dict) and d.get("DeviceID")
    }


def _windows_drives():
    try:
        import ctypes
    except ImportError:
        return []
    kernel32 = ctypes.windll.kernel32
    # Don't pop up "insert a disk" dialogs for empty card readers.
    kernel32.SetErrorMode(1)  # SEM_FAILCRITICALERRORS
    mask = kernel32.GetLogicalDrives()
    drives = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        # 2 = removable, 3 = fixed, 4 = network
        if kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) not in (2, 3, 4):
            continue
        label = ctypes.create_unicode_buffer(261)
        fs_name = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_uint32(0)
        ok = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            label,
            261,
            ctypes.byref(serial),
            None,
            None,
            fs_name,
            261,
        )
        if not ok:
            continue
        drives.append(
            DriveInfo(root, label.value, f"{serial.value:08X}", fs_name.value, _disk_total(root))
        )
    return drives


def _macos_drives():
    import plistlib

    try:
        root_dev = os.stat("/").st_dev
        entries = list(os.scandir("/Volumes"))
    except OSError:
        return []
    drives = []
    for entry in entries:
        try:
            # Skip the startup disk ("Macintosh HD" is a link to /)
            if os.stat(entry.path).st_dev == root_dev:
                continue
        except OSError:
            continue
        info = {}
        out = _run_quiet(["diskutil", "info", "-plist", entry.path])
        if out:
            try:
                info = plistlib.loads(out.encode("utf-8"))
            except Exception:
                info = {}
        drives.append(
            DriveInfo(
                entry.path,
                info.get("VolumeName") or entry.name,
                info.get("VolumeUUID") or info.get("DiskUUID") or "",
                info.get("FilesystemType") or info.get("FilesystemName") or "",
                info.get("TotalSize") or _disk_total(entry.path),
            )
        )
    return drives


def detect_drives():
    """Return the mounted drives a person would want to scan, sorted by mount."""
    system = platform.system()
    if system == "Windows":
        drives = _windows_drives()
    elif system == "Darwin":
        drives = _macos_drives()
    elif system == "Linux":
        drives = _linux_drives(wsl=_is_wsl())
    else:
        drives = []
    return sorted(drives, key=lambda d: d.mount.lower())


def identify_drive(directory):
    """Return the DriveInfo whose mount point is *directory*, else None."""
    want = _root_cache_prefix(os.path.abspath(directory))
    for drive in detect_drives():
        if _root_cache_prefix(drive.mount) == want:
            return drive
    return None


def read_inventory_header(path, max_lines=64):
    """Return ({key: value} from "# key=value" header lines, is_tsv_inventory)."""
    info = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(max_lines):
                line = f.readline()
                if not line:
                    break
                line = line.rstrip("\r\n")
                if line.startswith("#"):
                    key, sep, value = line.lstrip("# ").partition("=")
                    if sep and key and " " not in key:
                        info[key] = value
                    continue
                return info, line.startswith("filepath\t")
    except OSError:
        pass
    return info, False


def _volume_header_lines(output, directory, volume_info=None):
    """Header lines naming the drive a whole-drive inventory belongs to.

    Only whole-drive scans get them; a list of one folder must not be offered
    as the list for its entire drive.
    """
    if volume_info is None:
        try:
            if not os.path.ismount(directory):
                return []
        except OSError:
            return []
        volume_info = identify_drive(directory)
        if volume_info is None:
            return []
    existing = read_inventory_header(output)[0] if os.path.exists(output) else {}
    ids = [i for i in existing.get("volume_ids", "").split(",") if i]
    same_drive = (
        not ids
        or volume_info.volume_id in ids
        or (volume_info.label and existing.get("volume_label") == volume_info.label)
    )
    if not same_drive:
        ids = []
    # Keep IDs seen on other systems (macOS reports exFAT IDs differently).
    if volume_info.volume_id and volume_info.volume_id not in ids:
        ids.insert(0, volume_info.volume_id)
    lines = []
    if volume_info.label:
        lines.append(f"volume_label={volume_info.label}")
    if ids:
        lines.append(f"volume_ids={','.join(ids)}")
    return lines


class FileList:
    """An inventory file plus what is known about which drive it describes."""

    __slots__ = ("path", "root", "label", "volume_ids", "drive", "matched_by")

    def __init__(self, path, header):
        self.path = Path(path)
        self.root = header.get("inventory_root", "")
        self.label = header.get("volume_label", "")
        self.volume_ids = [i for i in header.get("volume_ids", "").split(",") if i]
        self.drive = None
        self.matched_by = ""


def find_file_lists(folder):
    """All findphotodates TSV inventories directly inside *folder*."""
    lists = []
    try:
        candidates = sorted(Path(folder).glob("*.tsv"), key=lambda p: p.name.lower())
    except OSError:
        return lists
    for path in candidates:
        header, is_inventory = read_inventory_header(path)
        if is_inventory:
            lists.append(FileList(path, header))
    return lists


def _sample_inventory_rows(path, samples=40):
    """(filepath, size) pairs spread evenly through an inventory, read by seeking."""
    rows = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            for i in range(samples):
                f.seek(int(size * (i + 0.5) / samples))
                f.readline()  # skip the partial line we landed in
                parts = f.readline().decode("utf-8", "replace").rstrip("\r\n").split("\t")
                if len(parts) < 4 or parts[0].startswith(("#", '"')) or parts[0] == "filepath":
                    continue
                try:
                    rows.append((parts[0], int(parts[2])))
                except ValueError:
                    continue
    except OSError:
        pass
    # Small lists land on the same row repeatedly; count each file once.
    return list(dict.fromkeys(rows))


def _content_match_score(rows, recorded_root, mount):
    """How many sampled files exist on *mount* at the same place with the same size."""
    old_prefix = _root_cache_prefix(recorded_root)
    hits = checked = 0
    for filepath, size in rows:
        key = _normalize_cache_key(filepath)
        if not key.startswith(old_prefix):
            continue
        checked += 1
        try:
            if os.stat(os.path.join(mount, *key[len(old_prefix):].split("/"))).st_size == size:
                hits += 1
        except (OSError, ValueError):
            pass
        if checked >= 10 and hits == 0:
            break  # clearly a different drive; don't keep seeking a slow disk
    return hits, checked


def _id_shape(volume_id):
    """Rough format of a volume ID, e.g. "XXXX-XXXX" serials vs 36-char UUIDs."""
    return re.sub(r"[0-9A-Fa-f]", "X", volume_id)


def list_belongs_to_other_drive(volume_ids, drive):
    """True if a list's recorded volume IDs show it belongs to a different drive.

    Only IDs in the same format are compared: macOS reports exFAT volumes with
    a UUID where Linux and Windows use a serial, so a list recorded elsewhere
    may legitimately carry an ID of another shape.
    """
    if not volume_ids or not drive.volume_id or drive.volume_id in volume_ids:
        return False
    shape = _id_shape(drive.volume_id)
    return any(_id_shape(v) == shape for v in volume_ids)


def match_lists_to_drives(lists, drives):
    """Pair file lists with connected drives, most reliable evidence first:
    recorded volume serial/UUID, then an unchanged mount path, then whether
    files from the list are actually present on the drive."""
    taken = set()

    for fl in lists:
        for vid in fl.volume_ids:
            candidates = [d for d in drives if d.volume_id == vid and d.mount not in taken]
            if len(candidates) == 1:
                fl.drive, fl.matched_by = candidates[0], "drive ID"
                taken.add(candidates[0].mount)
                break

    for fl in lists:
        if fl.drive or fl.volume_ids or not fl.root:
            continue
        want = _root_cache_prefix(fl.root)
        for d in drives:
            if d.mount not in taken and _root_cache_prefix(d.mount) == want:
                fl.drive, fl.matched_by = d, "location"
                taken.add(d.mount)
                break

    candidates = []
    for fl in lists:
        if fl.drive or not fl.root:
            continue
        # Alan 9/28/26 - A drive holding copies of another drive's folders
        # must not take over that drive's list; recorded IDs rule it out.
        free = [d for d in drives if d.mount not in taken
                and not list_belongs_to_other_drive(fl.volume_ids, d)]
        if not free:
            continue
        rows = _sample_inventory_rows(fl.path)
        for d in free:
            hits, checked = _content_match_score(rows, fl.root, d.mount)
            if hits >= 5 and hits * 2 >= checked:
                candidates.append((hits / checked, hits, fl, d))
    for _, _, fl, d in sorted(candidates, key=lambda c: (c[0], c[1]), reverse=True):
        if fl.drive is None and d.mount not in taken:
            fl.drive, fl.matched_by = d, "contents"
            taken.add(d.mount)


def canonical_list_name(drive):
    """File name for a drive's list, e.g. "Sierra Club (1A2B-3C4D).tsv"."""
    name = _safe_filename(drive.display_name)
    vid = drive.volume_id
    if vid:
        if len(vid) > 9:  # long UUIDs (ext4, APFS): a short prefix is enough
            vid = vid.replace("-", "")[:8]
        name += f" ({_safe_filename(vid)})"
    return name + ".tsv"


def _legacy_list_name(fl):
    """Name for an old list whose drive isn't connected: "Drive F.tsv" etc."""
    root = (fl.root or "").rstrip("/\\")
    m = re.fullmatch(r"([A-Za-z]):", root) or re.fullmatch(r"/mnt/([A-Za-z])", root)
    if m:
        return f"Drive {m.group(1).upper()}.tsv"
    if root:
        return _safe_filename(os.path.basename(root)) + ".tsv"
    return _safe_filename(fl.path.stem) + ".tsv"


def find_legacy_lists(folder=None):
    """Lists made by older versions (named like ~/f:photo.taken.dates.txt)."""
    folder = Path(folder) if folder else Path.home()
    found = []
    for pattern in _legacy_list_globs():
        for path in sorted(folder.glob(pattern)):
            if path not in found and read_inventory_header(path)[1]:
                found.append(path)
    return found


def import_file_lists(paths, list_dir, drives, move=True):
    """Move (or copy) lists into *list_dir*, named after their drive when known.

    Returns [(old_path, new_path)].
    """
    list_dir = Path(list_dir)
    list_dir.mkdir(parents=True, exist_ok=True)
    lists = [FileList(p, read_inventory_header(p)[0]) for p in paths]
    match_lists_to_drives(lists, drives)
    moved = []
    for fl in lists:
        name = canonical_list_name(fl.drive) if fl.drive else _legacy_list_name(fl)
        dest = _unused_path(list_dir / name)
        if move:
            shutil.move(str(fl.path), str(dest))
        else:
            shutil.copy2(str(fl.path), str(dest))
        moved.append((fl.path, dest))
    return moved


def _count_rows(path):
    """Number of file rows in an inventory (fast newline count)."""
    lines = 0
    comments = 0
    try:
        with open(path, "rb") as f:
            for line in iter(f.readline, b""):
                if not line.startswith(b"#"):
                    break
                comments += 1
            f.seek(0)
            for chunk in iter(lambda: f.read(1 << 20), b""):
                lines += chunk.count(b"\n")
    except OSError:
        return 0
    return max(0, lines - comments - 1)  # minus comments and the column header


def _last_updated_label(timestamp, now=None):
    """A friendly local-time label shared by the menu and desktop GUI."""
    updated = datetime.fromtimestamp(timestamp)
    current = now or datetime.now()
    if updated.date() == current.date():
        return updated.strftime("Today %H:%M")
    return updated.strftime("%Y-%m-%d")
