"""Shared helpers for findphotodates and its companion modules: paths,
settings, walking a drive, and terminal output."""

from __future__ import annotations

import csv
import errno
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unicodedata


from pathlib import Path

# A quoted field normally ends on the line it started on; a file name with a
# line break in it spans a few. More than this means a damaged line.
_MAX_LINES_PER_ROW = 20


def split_list_lines(lines):
    """Fields of each row of a file list, header row first, skipping the "#"
    comment lines (and blank lines) before it.

    Plain tab splitting is several times faster than csv, so csv is used only
    for rows with quoted fields: names with tabs or quotes in them, and names
    with a line break, which span several lines. Open the file with
    ``newline=""`` so line breaks inside names are kept as they are.
    """
    lines = iter(lines)
    for line in lines:
        if line.strip() and not line.startswith("#"):
            break
    else:
        return
    while line is not None:
        if '"' in line:
            record, extra = line, 0
            # A complete record has an even number of quote characters
            # (quotes inside a quoted field are doubled).
            while record.count('"') % 2 and extra < _MAX_LINES_PER_ROW:
                more = next(lines, None)
                if more is None:
                    break
                record += more
                extra += 1
            if record.count('"') % 2:
                # Damaged quoting: read the first line on its own and carry on
                # with the lines that were looked ahead at.
                pending = record[len(line):].splitlines(keepends=True)
                yield next(csv.reader([line], delimiter="\t"))
                lines = _chain(pending, lines)
            else:
                yield next(csv.reader([record], delimiter="\t"))
        else:
            yield line.rstrip("\r\n").split("\t")
        line = next(lines, None)


def _chain(first, rest):
    yield from first
    yield from rest



# Optional fast hash (prefer over stdlib when available)
try:
    import blake3

    _BLAKE3_AVAILABLE = True
except ImportError:
    blake3 = None
    _BLAKE3_AVAILABLE = False

# ---------------------------------------------------------------------------
# Cross-platform cache-key normalization (WSL ↔ Windows)
# ---------------------------------------------------------------------------
# WSL mount paths like /mnt/f/Photos and Windows paths like F:\Photos refer
# to the same file.  We normalize both forms to a lowercase-drive forward-slash
# canonical key so that inventories created under one environment get cache
# hits when re-read under the other.
#
# Canonical form:  /mnt/<drive>/<rest-with-forward-slashes>
#   /mnt/f/Documents/file.jpg  →  /mnt/f/Documents/file.jpg   (unchanged)
#   F:\Documents\file.jpg      →  /mnt/f/Documents/file.jpg
#
# Non-drive paths (Linux home dirs, UNC paths, etc.) pass through unchanged
# after backslash → forward-slash conversion.

_IS_TTY = sys.stdout.isatty()


# Alan 5/4/26 - Cosmetic: detect whether the current terminal can render ANSI
# escape sequences. POSIX TTYs always can; Windows is conservative — older
# PowerShell 5 / cmd.exe will print raw "ESC[2K" garbage if we send it
# unconditionally, so we only enable ANSI when the host advertises it
# (Windows Terminal, ConEmu, ANSICON, or a real TERM).
def _detect_ansi_support():
    if not _IS_TTY:
        return False
    if os.name != "nt":
        return True
    if os.environ.get("WT_SESSION"):
        return True  # Windows Terminal
    if os.environ.get("ConEmuANSI", "").upper() == "ON":
        return True
    if "ANSICON" in os.environ:
        return True
    term = os.environ.get("TERM", "")
    if term and term.lower() != "dumb":
        return True
    return False


_ANSI_OK = _detect_ansi_support()


def _clear_progress_line():
    """Clear the carriage-return progress line before printing a summary.

    On ANSI-capable terminals: emit "\\r\\x1b[2K" to wipe the line in place.
    Otherwise: emit a newline so the next print starts cleanly without
    leaking raw escape codes onto the screen.
    """
    if _ANSI_OK:
        print("\r\033[2K", end="")
    else:
        print()


_WSL_MOUNT_RE = re.compile(r"^/mnt/([a-zA-Z])/")
_WIN_DRIVE_RE = re.compile(r"^([a-zA-Z]):[/\\]")

_QUOTE_TABLE = str.maketrans(
    {
        "\u2018": "'",  # Left single quotation mark
        "\u2019": "'",  # Right single quotation mark
        "\u2032": "'",  # Prime
        "\u201a": "'",  # Single low-9 quote
        "\u0060": "'",  # Backtick
        "\uf027": "'",  # WSL private-use single quote
        "\u201c": '"',  # Left double quotation mark
        "\u201d": '"',  # Right double quotation mark
        "\u2033": '"',  # Double prime
        "\u201e": '"',  # Double low-9 quote
        "\u00ab": '"',  # Guillemet left
        "\u00bb": '"',  # Guillemet right
        "\uf022": '"',  # WSL private-use double quote mapping
    }
)
# Fallback sweep: strip any other codepoints in the Private Use Area range U+F000 to U+F0FF
for _cp in range(0xF000, 0xF100):
    if _cp not in _QUOTE_TABLE:
        _QUOTE_TABLE[_cp] = None


def _normalize_cache_key(path):
    """Normalize a filepath into a cross-platform canonical cache key.

    Maps Windows drive paths (F:\\...) and WSL mount paths (/mnt/f/...) to a
    shared canonical form so the same physical file gets the same key regardless
    of whether the inventory was created under WSL or native Windows.
    """
    # 1. Normalize Unicode composition
    p = unicodedata.normalize("NFC", path)

    # 2. Map known problematic quote variants and strip undefined GUI/PUA glyphs
    p = p.translate(_QUOTE_TABLE)

    # 3. Normalize backslashes to forward slashes first
    p = p.replace("\\", "/")

    # Windows drive letter  →  /mnt/<lower-drive>/rest
    m = _WIN_DRIVE_RE.match(p)
    if m:
        drive = m.group(1).lower()
        rest = p[3:]  # skip  X:/
        return f"/mnt/{drive}/{rest}"

    # WSL mount  →  ensure lowercase drive letter
    m = _WSL_MOUNT_RE.match(p)
    if m:
        drive = m.group(1).lower()
        return f"/mnt/{drive}/{p[len(m.group(0)) :]}"

    return p


def format_path_style(path, style):
    """Convert paths to the specified style (linux or windows)."""
    if style == "linux":
        path = path.replace("\\", "/")
        m = _WIN_DRIVE_RE.match(path)
        if m:
            drive = m.group(1).lower()
            rest = path[3:]
            path = f"/mnt/{drive}" + (f"/{rest}" if rest else "")
    elif style == "windows":
        m = _WSL_MOUNT_RE.match(path)
        if m:
            drive = m.group(1).upper()
            rest = path[len(m.group(0)) :]
            path = f"{drive}:\\" + rest if rest else f"{drive}:\\"
        elif (
            path.startswith("/mnt/")
            and len(path) == 6
            and path[5].isalpha()
            and path[4] == "/"
        ):
            path = f"{path[5].upper()}:\\"
        path = path.replace("/", "\\")
    return path


def _host_path_style():
    """Return the current platform's natural path style."""
    if platform.system() == "Windows":
        return "windows"
    return "linux"


def _resolve_local_inventory_path(path):
    """Convert a stored inventory filepath into a host-resolvable local path."""
    if not path:
        return path
    return os.path.abspath(format_path_style(path, _host_path_style()))


def compute_samplehash_v1(path, chunk_bytes, num_chunks, algo="blake3"):
    """Compute a fast sample fingerprint from path (size + \0 + chunks).

    For small files (size <= num_chunks * chunk_bytes), hashes the whole file.
    Supports blake3, blake2b(digest_size=16), or sha256.
    Returns hex digest string or None on error.
    """
    try:
        st = os.stat(path)
        size = st.st_size
    except OSError:
        return None

    if algo == "blake3" and _BLAKE3_AVAILABLE:
        h = blake3.blake3()
    elif algo == "sha256":
        h = hashlib.sha256()
    else:
        h = hashlib.blake2b(digest_size=16)

    h.update(str(size).encode("ascii"))
    h.update(b"\0")

    total_sample_bytes = num_chunks * chunk_bytes
    small_file = size <= total_sample_bytes or size == 0

    try:
        with open(path, "rb") as f:
            if small_file:
                # Stream the entire file in chunks to avoid memory blow-up
                for data in iter(lambda: f.read(65536), b""):
                    h.update(data)
            else:
                # Distribute offsets: first 0, last size-chunk_bytes, middle ones spread evenly
                chunks_to_read = []
                end_offset = max(0, size - chunk_bytes)
                for i in range(num_chunks):
                    if i == 0:
                        offset = 0
                    elif i == num_chunks - 1:
                        offset = end_offset
                    else:
                        offset = (
                            (i * end_offset) // (num_chunks - 1)
                            if num_chunks > 1
                            else 0
                        )
                    offset = max(0, min(offset, end_offset))
                    chunks_to_read.append(offset)

                # Deduplicate and sort offsets
                for offset in sorted(set(chunks_to_read)):
                    f.seek(offset)
                    data = f.read(chunk_bytes)
                    if data:
                        h.update(data)
        return h.hexdigest()
    except OSError:
        return None


def compute_fullhash(path, algo="blake3"):
    """Compute full-file hash. algo is 'blake3', 'blake2b', or 'sha256'. Returns hex digest or None."""
    try:
        if algo == "blake3" and _BLAKE3_AVAILABLE:
            h = blake3.blake3()
        elif algo == "blake2b":
            h = hashlib.blake2b()
        elif algo in ("sha256", "blake3"):
            # blake3 requested but not available — fall back to sha256
            h = hashlib.sha256()
        else:
            print(
                f"WARNING: Unknown full-hash algo {algo!r}, falling back to sha256",
                file=sys.stderr,
            )
            h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def is_supported_file(filename, extensions):
    """Check if a file has a supported extension. If extensions is None, all files match."""
    if extensions is None:
        return True
    return any(filename.lower().endswith(f".{ext}") for ext in extensions)


_IO_ERRNOS = {errno.EIO, errno.ENXIO, errno.ETIMEDOUT, errno.ENODEV}
# Windows: CRC error, not ready, hardware error, I/O device error, device not connected.
_IO_WINERRORS = {21, 23, 483, 1117, 1167}


def _is_io_error(exc):
    """True when an OSError means the drive couldn't read the data (a failing
    or disconnected drive), as opposed to a missing file or a permission problem."""
    return exc.errno in _IO_ERRNOS or getattr(exc, "winerror", None) in _IO_WINERRORS


def _is_windows_reparse_point_dir(entry):
    """Return True if this DirEntry is a Windows directory reparse point
    (junction, directory symlink, or other directory-like reparse target).

    Callers should already have confirmed the entry is a directory
    (entry.is_dir(follow_symlinks=False) == True) before invoking this — we
    only need to decide whether that directory is a reparse point we must
    refuse to recurse into.

    Always False on non-Windows platforms, where st_file_attributes is absent.
    """
    try:
        st = entry.stat(follow_symlinks=False)
    except (PermissionError, OSError):
        return False
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is None:
        return False
    reparse_bit = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attrs & reparse_bit)


# Windows file attributes of cloud files whose data isn't stored locally
# (OneDrive "online-only", Phone Link): RECALL_ON_DATA_ACCESS, RECALL_ON_OPEN, OFFLINE.
_WINDOWS_PLACEHOLDER_ATTRIBUTES = 0x400000 | 0x40000 | 0x1000
_MACOS_SF_DATALESS = 0x40000000  # iCloud files evicted from the Mac
_PLACEHOLDER_MIN_BYTES = 4096  # tiny files can live inside the directory entry


def is_placeholder_stat(st):
    """True when a file's data isn't actually stored on the drive: a cloud
    placeholder (OneDrive, Phone Link, iCloud) or a file with no data blocks."""
    attributes = getattr(st, "st_file_attributes", None)
    if attributes is not None:
        return bool(attributes & _WINDOWS_PLACEHOLDER_ATTRIBUTES)
    if getattr(st, "st_flags", 0) & _MACOS_SF_DATALESS:
        return True
    blocks = getattr(st, "st_blocks", None)
    return blocks == 0 and st.st_size > _PLACEHOLDER_MIN_BYTES


# Files with no data blocks count as cloud placeholders only on a drive that
# reports data blocks for other files: some network and FUSE drives report
# none for any file. Up to this many such files are held back while waiting
# for a file that shows the drive reports blocks; past that, the drive is taken
# not to report them, and none of its files are placeholders.
_PLACEHOLDER_HOLD_LIMIT = 1000


def _placeholder_verdict(st):
    """True or False, or "no-blocks" when only the drive's other files can tell."""
    attributes = getattr(st, "st_file_attributes", None)
    if attributes is not None:
        return bool(attributes & _WINDOWS_PLACEHOLDER_ATTRIBUTES)
    if getattr(st, "st_flags", 0) & _MACOS_SF_DATALESS:
        return True
    if getattr(st, "st_blocks", None) == 0 and st.st_size > _PLACEHOLDER_MIN_BYTES:
        return "no-blocks"
    return False


def _resolve_placeholders(items, placeholders, hold_limit=_PLACEHOLDER_HOLD_LIMIT):
    """Yield (path, is_symlink, size, mtime) from (path, is_symlink, size, mtime,
    stat or None), adding cloud placeholders to *placeholders* before each one
    is yielded (see _PLACEHOLDER_HOLD_LIMIT)."""
    reports_blocks, no_blocks = set(), set()  # st_dev values
    held = {}  # st_dev -> [items waiting to learn whether the drive reports blocks]
    for path, is_link, size, mtime, st in items:
        if st is None:
            yield path, is_link, size, mtime
            continue
        verdict = _placeholder_verdict(st)
        device = st.st_dev
        if verdict == "no-blocks":
            if device in reports_blocks:
                placeholders.add(path)
            elif device not in no_blocks:
                waiting = held.setdefault(device, [])
                waiting.append((path, is_link, size, mtime))
                if len(waiting) > hold_limit:
                    no_blocks.add(device)
                    yield from held.pop(device)
                continue
        else:
            if verdict:
                placeholders.add(path)
            if getattr(st, "st_blocks", 0) and device not in reports_blocks:
                reports_blocks.add(device)
                for item in held.pop(device, ()):
                    placeholders.add(item[0])
                    yield item
        yield path, is_link, size, mtime
    for waiting in held.values():  # the drive never showed it reports blocks
        yield from waiting


def find_files(directory, extensions, debug=False, errors=None, placeholders=None):
    """Recursively find all files with the specified extensions using os.scandir().

    See _find_files; if *placeholders* is a set, paths of files whose data isn't
    on the drive (cloud placeholders) are added to it before they are yielded.
    """
    walk = _find_files(directory, extensions, debug, errors, placeholders is not None)
    if placeholders is None:
        yield from walk
    else:
        yield from _resolve_placeholders(walk, placeholders)


def _find_files(directory, extensions, debug=False, errors=None, with_stat=False):
    """Recursively find all files with the specified extensions using os.scandir().

    Yields (filepath, is_symlink, size_bytes, mtime_ns) tuples.
    For non-symlink files, size_bytes and mtime_ns come from the DirEntry stat
    cache (no extra syscall on Linux).  For symlinks, they are None so the
    consumer stats the resolved target itself.

    Symlinked directories are NOT recursed into (avoids duplicate traversal and
    infinite loops from symlink cycles).  Symlinked files are still yielded.
    Folders and files that can't be read are skipped; if *errors* is a list,
    (kind, path, is_io_error, message) tuples are appended to it for each one.
    With *with_stat*, each tuple also has the file's stat result (None for
    symlinks).
    """
    try:
        scan_it = os.scandir(directory)
    except (PermissionError, OSError) as e:
        if errors is not None:
            errors.append(("folder", directory, _is_io_error(e), e.strerror or str(e)))
        return
    with scan_it:
        for entry in scan_it:
            try:
                is_link = entry.is_symlink()
                is_dir_no_follow = entry.is_dir(follow_symlinks=False)
                if is_dir_no_follow:
                    # Alan 5/3/26 - Skip Windows reparse-point *directories*
                    # (NTFS junctions and directory symlinks) to avoid
                    # traversal loops such as
                    # C:\ProgramData\Application Data -> C:\ProgramData.
                    # Only checked for directories so ordinary reparse-point
                    # files (e.g. OneDrive cloud placeholders, file symlinks)
                    # are still indexed normally.
                    if _is_windows_reparse_point_dir(entry):
                        if debug:
                            print(
                                f"Debug: Skipping reparse-point directory: {entry.path}",
                                file=sys.stderr,
                            )
                        continue
                    # Real directory — recurse
                    yield from _find_files(entry.path, extensions, debug, errors, with_stat)
                elif is_link and entry.is_dir(follow_symlinks=True):
                    # Symlinked directory — skip recursion to avoid
                    # duplicate traversal and symlink loops
                    pass
                elif entry.is_file(follow_symlinks=True):
                    if is_supported_file(entry.name, extensions):
                        if is_link:
                            yield (entry.path, True, None, None) + ((None,) if with_stat else ())
                        else:
                            st = entry.stat(follow_symlinks=True)
                            yield (entry.path, False, st.st_size, st.st_mtime_ns) + (
                                (st,) if with_stat else ())
            except (PermissionError, OSError) as e:
                if errors is not None:
                    errors.append(("file", entry.path, _is_io_error(e), e.strerror or str(e)))
                continue


def _root_cache_prefix(root):
    """Normalized cache-key prefix (with trailing slash) for an inventory root."""
    return _normalize_cache_key(root.rstrip("/\\") + "/")


# Path expansion helper
def _expand_path(p: str) -> str:
    """Expand user home directory and convert to absolute path."""
    return os.path.abspath(os.path.expanduser(p))


# Config file handling
CONFIG_FILE = Path.home() / ".findphotodates.config.json"


def load_config():
    """Load configuration from JSON file."""
    if not CONFIG_FILE.exists():
        return {
            "_comment": "Saved scan configurations for findphotodates.py. Edit cautiously; JSON comments are stored here.",
            "saved_scans": [],
        }

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        # Ensure required structure
        if "saved_scans" not in config:
            config["saved_scans"] = []
        return config
    except json.JSONDecodeError as e:
        # SystemExit with a message: printed and exit code 1 on the command line,
        # shown in a dialog by the GUI. (The builtin exit() is missing in frozen apps.)
        raise SystemExit(f"Error: Invalid JSON in config file {CONFIG_FILE}: {e}\n"
                         "Please fix or remove the config file and try again.") from None
    except Exception as e:
        raise SystemExit(f"Error: Failed to read config file {CONFIG_FILE}: {e}") from None


def save_config(config):
    """Save configuration to JSON file atomically."""
    try:
        # Ensure directory exists
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Create temp file in same directory
        fd, temp_file = tempfile.mkstemp(
            dir=CONFIG_FILE.parent, suffix=".json", prefix=".findphotodates_"
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False, sort_keys=True)

            # Atomically replace
            os.replace(temp_file, CONFIG_FILE)
        except Exception:
            try:
                os.unlink(temp_file)
            except Exception:
                pass
            raise
    except Exception as e:
        raise SystemExit(f"Error: Failed to write config file {CONFIG_FILE}: {e}") from None


# Settings can be changed from more than one thread (the GUI's background drive
# refresh records when drives were connected while you change other settings).
_CONFIG_LOCK = threading.RLock()


def update_config(change):
    """Load the config, apply change(config) and save it, so a change made at the
    same time by another thread isn't overwritten. If change() returns False,
    nothing is saved. Returns the config."""
    with _CONFIG_LOCK:
        config = load_config()
        if change(config) is not False:
            save_config(config)
        return config


def _format_duration(seconds):
    """Format seconds into a human-readable duration string."""
    if seconds > 3600:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"
    elif seconds > 60:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        return f"{seconds:.1f}s"
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _is_wsl():
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/sys/kernel/osrelease", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _run_quiet(cmd, timeout=30):
    """Run a helper command and return its stdout, or None on any failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _human_size(num_bytes):
    if not num_bytes:
        return ""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if value >= 100 or unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return ""


def _safe_filename(text):
    return _INVALID_FILENAME_CHARS.sub("_", text).strip(" .") or "Drive"


def _unused_path(path, taken=()):
    """*path*, or "name (2).ext" and so on if it exists or is in *taken*."""
    path = Path(path)
    if not path.exists() and path not in taken:
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists() and candidate not in taken:
            return candidate
    raise FileExistsError(path)


def _wrap(text, indent="     "):
    import textwrap

    width = max(40, min(shutil.get_terminal_size((88, 20)).columns, 100) - 2)
    return "\n".join(
        textwrap.fill(p, width=width, initial_indent=indent, subsequent_indent=indent)
        for p in text.split("\n")
    )


def _ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        raise KeyboardInterrupt from None


def _confirm(prompt, default=True):
    answer = _ask(prompt + (" [Y/n] " if default else " [y/N] ")).lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _print_status_line(message):
    if _IS_TTY and _ANSI_OK:
        print(f"\r\033[2K{message}", end="", flush=True)
    else:
        print(message, flush=True)


def _mount_root(path):
    """The mount point containing *path*."""
    path = os.path.abspath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path
