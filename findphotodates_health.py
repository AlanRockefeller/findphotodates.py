"""Drive health checks: SMART data, USB connection advice, the health log,
self-tests and reading every file."""

from __future__ import annotations

import csv
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

from datetime import datetime
from pathlib import Path

from findphotodates_common import (  # noqa: F401
    _clear_progress_line,
    _confirm,
    _format_duration,
    _human_size,
    _is_wsl,
    _mount_root,
    _print_status_line,
    _root_cache_prefix,
    _run_quiet,
    _safe_filename,
    _unused_path,
    _wrap,
    find_files,
)

from findphotodates_drives import (  # noqa: F401
    _normalize_volume_id,
    default_inventory_dir,
    detect_drives,
    identify_drive,
)

def _troubling_health_record(directory, volume_info=None):
    """The latest health-log entry for the drive holding *directory*, if that
    check found signs of failure (bad sectors, read errors or FAILING)."""
    rows = read_health_log()
    if not rows:
        return None
    drive = volume_info
    if drive is None:
        try:
            drive = identify_drive(_mount_root(directory))
        except Exception:
            drive = None
    if drive is None:
        return None
    latest = None
    for row in rows:
        ids = [i for i in (row.get("volume_ids") or "").split(",") if i]
        names = [n.strip() for n in (row.get("drive") or "").split(",")]
        if (drive.volume_id and drive.volume_id in ids) or (
            not ids and drive.label and drive.label in names
        ):
            latest = row
    if latest is None:
        return None
    if latest.get("health") == "FAILING":
        return latest
    for key in ("pending_sectors", "uncorrectable_sectors", "read_errors", "ssd_media_errors"):
        value = _as_number(latest.get(key))
        if value:
            return latest
    if latest.get("check") == "file read" and latest.get("health") == "WARNING":
        return latest
    return None


def _print_health_warning(row):
    notes = row.get("notes") or ""
    print()
    print(_wrap(
        f"Warning: in its last health check ({row.get('date', '')[:10]}), this drive was rated "
        f"{row.get('health', 'WARNING')}" + (f": {notes}." if notes and notes != "No warning signs" else ".")
        + " Reading every file on a failing drive adds wear and can be very slow. If it holds "
        "the only copy of anything, copy your files off it first. If you scan it anyway, "
        "files will be read one at a time to go easier on it.", indent=""))


# ---------------------------------------------------------------------------
# Drive health checks
# ---------------------------------------------------------------------------
# Alan 9/28/26 - Quick check: read the drive's own SMART health data via
# smartctl (smartmontools).  Extended check: start the drive's full-surface
# self-test, or, for drives without SMART (SD cards, many USB sticks), read
# every file and report any that can't be read.

HEALTH_GOOD = "good"
HEALTH_WARNING = "warning"
HEALTH_FAILING = "failing"
HEALTH_UNKNOWN = "unknown"
HEALTH_NOTE = "note"  # worth mentioning, but not a reason for concern

# ATA SMART attributes worth explaining when their raw value is above zero.
# Health-log column for each ATA attribute we record.
_ATA_ATTRIBUTE_METRICS = {
    5: "reallocated_sectors",
    197: "pending_sectors",
    198: "uncorrectable_sectors",
    187: "read_errors",
    199: "connection_errors",
}

_ATA_WARNING_ATTRIBUTES = {
    5: ("{n:,} bad sector(s) have been replaced with spares",
        "The drive found spots it couldn't use reliably and swapped in spare space. "
        "A handful is common on old drives; a number that keeps growing means the "
        "drive is wearing out."),
    196: ("{n:,} sector replacement event(s)",
          "The drive has had to move data away from damaged spots."),
    197: ("{n:,} sector(s) are unreadable and waiting to be replaced",
          "Data in these spots can't currently be read. Files stored there may be "
          "damaged. Copy anything important off this drive soon."),
    198: ("{n:,} sector(s) failed the drive's own surface scan",
          "These spots couldn't be read during the drive's background check. Files "
          "stored there may be damaged. Copy anything important off this drive soon."),
    187: ("{n:,} read error(s) the drive couldn't correct",
          "The drive has returned data it couldn't fix. This usually goes with bad sectors."),
    10: ("the motor needed extra tries to spin up {n:,} time(s)",
         "This can mean a failing motor or not enough power (common with USB hubs)."),
    199: ("{n:,} data error(s) on the cable or USB connection",
          "This is usually the cable, USB port or enclosure rather than the disk itself. "
          "If the number grows, try a different cable or port."),
}


class HealthTarget:
    """A physical drive to check (Linux) or a volume on it (Windows/macOS/WSL)."""

    __slots__ = ("device", "name", "model", "size_bytes", "transport", "rotational", "mounts",
                 "volume_ids")

    def __init__(self, device, name, model="", size_bytes=0, transport="", rotational=None, mounts=(),
                 volume_ids=()):
        self.device = device
        self.name = name
        self.model = (model or "").strip()
        self.size_bytes = int(size_bytes or 0)
        self.transport = (transport or "").lower()
        self.rotational = rotational
        self.mounts = list(mounts)
        self.volume_ids = [v for v in (_normalize_volume_id(i) for i in volume_ids) if v]

    @property
    def kind(self):
        if self.transport == "mmc" or os.path.basename(self.device).startswith("mmcblk"):
            return "SD card"
        if self.transport == "nvme" or "nvme" in self.device:
            return "NVMe SSD"
        medium = {True: "hard drive", False: "SSD or flash drive"}.get(self.rotational, "drive")
        if self.transport == "usb":
            return f"USB {medium}"
        return medium[0].upper() + medium[1:]

    def description(self):
        model = self.model if self.model != self.name else ""
        details = ", ".join(x for x in (model, self.kind, _human_size(self.size_bytes)) if x)
        return f"{self.name} ({details})" if details else self.name


class HealthReport:
    """The result of a quick health check on one drive."""

    def __init__(self, target):
        self.target = target
        self.verdict = HEALTH_UNKNOWN
        self.findings = []  # (level, headline, explanation)
        self.facts = []  # plain "label: value" lines
        self.self_test_minutes = None  # {"short": n, "extended": n} when known
        self.self_test_running = None  # percent remaining, if a self-test is running
        self.smart_available = False
        self.device_type = None  # smartctl -d value that worked, if any
        self.problem = ""  # why health data couldn't be read
        self.serial = ""
        self.metrics = {}  # numbers recorded in the health log (see _HEALTH_LOG_COLUMNS)

    def add(self, level, headline, explanation=""):
        self.findings.append((level, headline, explanation))
        order = [HEALTH_UNKNOWN, HEALTH_GOOD, HEALTH_WARNING, HEALTH_FAILING]
        if level in order and order.index(level) > order.index(self.verdict):
            self.verdict = level


def _package_manager_install(package_names):
    """An install command for this system's package manager."""
    system = platform.system()
    if system == "Darwin":
        return "brew install " + " ".join(package_names)
    if system == "Windows":
        return "winget install smartmontools"
    for tool, command in (
        ("pacman", "sudo pacman -S"),
        ("apt", "sudo apt install"),
        ("dnf", "sudo dnf install"),
        ("zypper", "sudo zypper install"),
    ):
        if shutil.which(tool):
            return f"{command} {' '.join(package_names)}"
    return "install " + " ".join(package_names) + " with your package manager"


def health_tool_advice():
    """Lines recommending tools that would make health checks more complete."""
    advice = []
    if _is_wsl():
        advice.append(
            "WSL can't see the physical drives. For full health details, install "
            "smartmontools on Windows (winget install smartmontools) and run this "
            "program from Windows in a terminal opened with 'Run as administrator'."
        )
        return advice
    if not shutil.which("smartctl"):
        advice.append(
            "smartctl (from smartmontools) reads the health data drives keep about "
            "themselves and runs their built-in self-tests. Without it, only basic "
            "checks are possible. Install it with:  "
            + _package_manager_install(["smartmontools"])
        )
    if platform.system() == "Darwin" and shutil.which("smartctl"):
        advice.append(
            "On a Mac, USB drives often hide their health data unless a SAT SMART "
            "driver is installed; internal drives work without it."
        )
    return advice


def _linux_health_targets():
    out = _run_quiet(["lsblk", "-J", "-b", "-o", "PATH,NAME,TYPE,TRAN,MODEL,SIZE,ROTA,LABEL,UUID,MOUNTPOINT"])
    if not out:
        return []
    try:
        devices = json.loads(out).get("blockdevices", [])
    except ValueError:
        return []
    targets = []
    for dev in devices:
        name = dev.get("name") or ""
        if dev.get("type") != "disk" or name.startswith(("zram", "loop", "ram", "sr", "fd")):
            continue
        labels, mounts, uuids = [], [], []
        # The disk itself too: a drive formatted without partitions is mounted
        # straight from /dev/sdX.
        stack = [dev]
        while stack:
            child = stack.pop(0)
            stack.extend(child.get("children") or [])
            if child.get("label"):
                labels.append(child["label"])
            if child.get("mountpoint"):
                mounts.append(child["mountpoint"])
            if child.get("uuid"):
                uuids.append(child["uuid"])
        path = dev.get("path") or f"/dev/{name}"
        rota = dev.get("rota")
        targets.append(
            HealthTarget(
                path,
                ", ".join(dict.fromkeys(labels)) or (dev.get("model") or "").strip() or path,
                dev.get("model"),
                dev.get("size"),
                dev.get("tran"),
                None if rota is None else bool(int(rota) if not isinstance(rota, bool) else rota),
                mounts,
                uuids,
            )
        )
    return targets


def _macos_whole_disk(mount):
    out = _run_quiet(["diskutil", "info", "-plist", mount])
    if not out:
        return {}
    import plistlib

    try:
        return plistlib.loads(out.encode("utf-8"))
    except Exception:
        return {}


def detect_health_targets():
    """Drives whose health can be checked, ordered like the main menu (by mount point)."""
    return sorted(_detect_health_targets(),
                  key=lambda t: (not t.mounts, min((m.lower() for m in t.mounts), default="")))


def _detect_health_targets():
    system = platform.system()
    if system == "Linux" and not _is_wsl():
        return _linux_health_targets()
    targets = []
    for drive in detect_drives():
        if system == "Darwin":
            info = _macos_whole_disk(drive.mount)
            device = "/dev/" + info["ParentWholeDisk"] if info.get("ParentWholeDisk") else drive.mount
            targets.append(HealthTarget(device, drive.display_name, info.get("MediaName", ""),
                                        drive.size_bytes, info.get("BusProtocol", ""),
                                        None if "SolidState" not in info else not info["SolidState"],
                                        [drive.mount], [drive.volume_id]))
        else:  # Windows or WSL: smartctl and PowerShell both accept a drive letter
            letter = drive.mount[0] if system == "Windows" else drive.mount[-1]
            targets.append(HealthTarget(f"{letter.upper()}:", drive.display_name, "",
                                        drive.size_bytes, "", None, [drive.mount], [drive.volume_id]))
    return targets


def _is_admin():
    if platform.system() == "Windows":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def ensure_sudo_for_health(interactive=True):
    """On Linux, get sudo ready so smartctl can read the drives. Returns True if
    smartctl should be run through sudo."""
    if platform.system() != "Linux" or _is_wsl() or _is_admin() or not shutil.which("sudo"):
        return False
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return True
    if not interactive and not sys.stdin.isatty():
        return False
    print(_wrap(
        "Reading drive health needs administrator (root) access, so sudo will ask for "
        "your password. It goes to sudo, not to this program. Press Ctrl-C to skip.",
        indent=""))
    try:
        return subprocess.run(["sudo", "-v"]).returncode == 0
    except KeyboardInterrupt:
        print()
        return False


def _run_smartctl(args, device, use_sudo, device_type=None, timeout=120):
    cmd = (["sudo", "-n"] if use_sudo else []) + ["smartctl", "-j"]
    if device_type:
        cmd += ["-d", device_type]
    cmd += list(args) + [device]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return {"_error": str(e)}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {"_error": (result.stderr or result.stdout).strip()[:300]}


def _smartctl_messages(data):
    return " ".join(m.get("string", "") for m in data.get("smartctl", {}).get("messages", []))


def _read_smart(target, use_sudo):
    """Run smartctl -a, retrying with -d sat for USB bridges that need it."""
    data = _run_smartctl(["-a"], target.device, use_sudo)
    used_type = None
    messages = _smartctl_messages(data).lower()
    if "unknown usb bridge" in messages or "specify device type" in messages or (
        target.transport == "usb" and not data.get("smart_status") and "_error" not in data
    ):
        retry = _run_smartctl(["-a"], target.device, use_sudo, "sat")
        if retry.get("smart_status") or retry.get("ata_smart_attributes"):
            data, used_type = retry, "sat"
    return data, used_type


def _format_hours(hours):
    years = hours / 8766
    return f"{hours:,} hours (about {years:.1f} years)" if years >= 1 else f"{hours:,} hours"


def interpret_smart(target, data):
    """Turn smartctl -j -a output into a HealthReport with plain-language findings."""
    report = HealthReport(target)
    messages = _smartctl_messages(data)
    if "_error" in data or not (data.get("smart_status") or data.get("ata_smart_attributes")
                                or data.get("nvme_smart_health_information_log")):
        low = (messages + " " + data.get("_error", "")).lower()
        if "permission denied" in low or "operation not permitted" in low or "access is denied" in low:
            if platform.system() == "Windows":
                how = "open the terminal with 'Run as administrator' and try again"
            else:
                how = "run this in a terminal so sudo can ask for your password"
            report.problem = f"reading health data needs administrator access; {how}"
        elif "unsupported" in low or "unknown usb bridge" in low or "not supported" in low:
            report.problem = "this drive, or its USB enclosure, doesn't report health data"
        else:
            report.problem = (messages or data.get("_error") or "no health data returned").strip()
        return report

    report.smart_available = True
    report.verdict = HEALTH_GOOD
    if data.get("model_name"):
        target.model = target.model or data["model_name"]
    report.serial = str(data.get("serial_number") or "")

    status = data.get("smart_status", {})
    if status.get("passed") is False:
        report.add(HEALTH_FAILING, "The drive reports that it is FAILING",
                   "Its own health check says failure is likely soon. Copy everything "
                   "important off it now and replace it.")

    hours = data.get("power_on_time", {}).get("hours")
    if hours is not None:
        report.facts.append(f"Powered on for {_format_hours(hours)}")
        report.metrics["power_on_hours"] = hours
    temperature = data.get("temperature", {}).get("current")

    for attr in data.get("ata_smart_attributes", {}).get("table", []):
        attr_id = attr.get("id")
        raw = attr.get("raw", {}).get("value", 0) or 0
        if attr.get("when_failed") == "now":
            report.add(HEALTH_FAILING, f"'{attr.get('name', attr_id)}' is below the manufacturer's failure threshold",
                       "The drive considers this measurement a sign of failure. Copy "
                       "important files off it and replace it.")
        # Seagate drives pack extra counters into raw values of 187/199; the
        # low 16 bits hold the real count.
        count = raw & 0xFFFF if raw > 0xFFFF else raw
        if attr_id in _ATA_ATTRIBUTE_METRICS:
            # Record zeros too, so a later rise shows up in the health log.
            report.metrics[_ATA_ATTRIBUTE_METRICS[attr_id]] = count
        if attr_id in _ATA_WARNING_ATTRIBUTES and count > 0:
            headline, explanation = _ATA_WARNING_ATTRIBUTES[attr_id]
            level = HEALTH_WARNING
            if attr_id == 5 and count < 10:
                level = HEALTH_NOTE  # a few remapped sectors on an old drive is normal
            report.add(level, headline.format(n=count), explanation)

    nvme = data.get("nvme_smart_health_information_log")
    if nvme:
        if temperature is None:
            temperature = nvme.get("temperature")
        if hours is None and nvme.get("power_on_hours") is not None:
            report.facts.append(f"Powered on for {_format_hours(nvme['power_on_hours'])}")
            report.metrics["power_on_hours"] = nvme["power_on_hours"]
        report.metrics["ssd_media_errors"] = nvme.get("media_errors", 0)
        if nvme.get("critical_warning"):
            report.add(HEALTH_FAILING, "The SSD has raised a critical warning",
                       "It reports a serious problem (such as running out of spare space "
                       "or becoming read-only). Copy important files off it now.")
        spare, spare_min = nvme.get("available_spare"), nvme.get("available_spare_threshold")
        if spare is not None and spare_min is not None and spare < spare_min:
            report.add(HEALTH_FAILING, f"Spare space is down to {spare}%",
                       "The SSD is running out of replacement space for worn-out cells.")
        used = nvme.get("percentage_used")
        if used is not None:
            report.metrics["ssd_wear_percent"] = used
            report.facts.append(f"Wear: {used}% of its rated life used")
            if used >= 90:
                report.add(HEALTH_WARNING, f"The SSD has used {used}% of its rated life",
                           "It may keep working for a while, but plan to replace it.")
        if nvme.get("media_errors"):
            report.add(HEALTH_WARNING, f"{nvme['media_errors']:,} data error(s) the SSD couldn't correct",
                       "Some data could not be read back correctly. Check important files "
                       "and consider replacing the SSD.")

    if temperature is not None:
        report.metrics["temperature_c"] = temperature
        report.facts.append(f"Temperature now: {temperature}°C")
        limit = 70 if nvme else 55
        if temperature >= limit:
            report.add(HEALTH_WARNING, f"The drive is hot ({temperature}°C)",
                       "Heat shortens a drive's life. Give it more airflow.")

    # Self-test results and capability
    ata_self_test = data.get("ata_smart_data", {}).get("self_test", {})
    polling = ata_self_test.get("polling_minutes") or {}
    if polling:
        report.self_test_minutes = {k: polling[k] for k in ("short", "extended") if k in polling}
    elif data.get("nvme_self_test_log") is not None or data.get("nvme_optional_admin_commands", {}).get("self_test"):
        report.self_test_minutes = {}
    running = ata_self_test.get("status", {})
    if running.get("remaining_percent") is not None and running.get("value", 0) >> 4 == 15:
        report.self_test_running = running["remaining_percent"]
    nvme_current = data.get("nvme_self_test_log", {}).get("current_self_test_operation", {})
    if nvme_current.get("value"):
        done = data["nvme_self_test_log"].get("current_self_test_completion_percent", 0)
        report.self_test_running = 100 - done

    last = None
    table = data.get("ata_smart_self_test_log", {}).get("standard", {}).get("table") or []
    if table:
        entry = table[0]
        last = (entry.get("type", {}).get("string", "Self-test"), entry.get("status", {}).get("string", ""),
                entry.get("status", {}).get("passed"), entry.get("lifetime_hours"))
    nvme_table = data.get("nvme_self_test_log", {}).get("table") or []
    if nvme_table and last is None:
        entry = nvme_table[0]
        result = entry.get("self_test_result", {})
        last = (entry.get("self_test_code", {}).get("string", "Self-test"), result.get("string", ""),
                result.get("value") == 0, entry.get("power_on_hours"))
    if last:
        kind, outcome, passed, at_hours = last
        when = f" at {at_hours:,} hours" if isinstance(at_hours, int) else ""
        report.facts.append(f"Last self-test: {kind.lower()}, {outcome.lower()}{when}")
        report.metrics["last_self_test"] = f"{kind.lower()}, {outcome.lower()}{when}"
        if passed is False and "abort" not in outcome.lower() and "interrupt" not in outcome.lower():
            report.add(HEALTH_WARNING, "The drive's last self-test found a problem",
                       "Part of the disk couldn't be read during the test. Copy important "
                       "files off it and consider replacing it.")
    elif report.self_test_minutes is not None:
        report.facts.append("No self-test has been run on this drive yet")

    if not any(level in (HEALTH_WARNING, HEALTH_FAILING) for level, _, _ in report.findings):
        report.findings.insert(0, (HEALTH_GOOD, "No warning signs", ""))
    return report


def _basic_health(target):
    """Health without smartctl: what the OS itself reports."""
    report = HealthReport(target)
    system = platform.system()
    if system == "Darwin":
        info = _macos_whole_disk(target.mounts[0]) if target.mounts else {}
        status = info.get("SMARTStatus", "")
        if status == "Verified":
            report.verdict = HEALTH_GOOD
            report.findings.append((HEALTH_GOOD, "macOS reports the drive's health as Verified", ""))
        elif status == "Failing":
            report.add(HEALTH_FAILING, "macOS reports the drive is FAILING",
                       "Copy everything important off it now.")
        else:
            report.problem = "macOS doesn't have health data for this drive"
        return report
    if system == "Windows" or _is_wsl():
        letter = target.device[0]
        script = (
            f"$p = Get-Partition -DriveLetter {letter} -ErrorAction Stop | Get-Disk | "
            "ForEach-Object { $n = $_.Number; Get-PhysicalDisk | Where-Object DeviceId -eq \"$n\" }; "
            "$r = $p | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
            "[pscustomobject]@{Health=\"$($p.HealthStatus)\"; Model=$p.FriendlyName; "
            "Media=\"$($p.MediaType)\"; Temp=$r.Temperature; Wear=$r.Wear; "
            "ReadErrors=$r.ReadErrorsUncorrected; Hours=$r.PowerOnHours} | ConvertTo-Json -Compress"
        )
        exe = "powershell.exe" if _is_wsl() else "powershell"
        out = _run_quiet([exe, "-NoProfile", "-NonInteractive", "-Command", script], timeout=60)
        try:
            info = json.loads(out) if out else {}
        except ValueError:
            info = {}
        if not info:
            report.problem = "Windows didn't return health data for this drive"
            return report
        target.model = target.model or (info.get("Model") or "")
        health = (info.get("Health") or "").lower()
        if health == "healthy":
            report.verdict = HEALTH_GOOD
            report.findings.append((HEALTH_GOOD, "Windows reports the drive as Healthy", ""))
        elif health in ("warning", "unhealthy"):
            report.add(HEALTH_FAILING if health == "unhealthy" else HEALTH_WARNING,
                       f"Windows reports the drive's health as {info.get('Health')}",
                       "Copy important files off it and check it with smartctl for details.")
        else:
            report.problem = "Windows doesn't know this drive's health"
        if info.get("Hours"):
            report.metrics["power_on_hours"] = int(info["Hours"])
            report.facts.append(f"Powered on for {_format_hours(int(info['Hours']))}")
        if info.get("Temp"):
            report.metrics["temperature_c"] = info["Temp"]
            report.facts.append(f"Temperature now: {info['Temp']}°C")
        if info.get("Wear") not in (None, 0):
            report.metrics["ssd_wear_percent"] = info["Wear"]
            report.facts.append(f"Wear: {info['Wear']}% of its rated life used")
        if info.get("ReadErrors") is not None:
            report.metrics["read_errors"] = info["ReadErrors"]
        if info.get("ReadErrors"):
            report.add(HEALTH_WARNING, f"{info['ReadErrors']:,} read error(s) the drive couldn't correct",
                       "Some data could not be read back correctly.")
        return report
    report.problem = "smartctl isn't installed, so this drive's health data can't be read"
    return report


# Alan 11/1/26 - Some USB enclosures handle Linux's fast "UAS" protocol badly:
# commands time out, the connection resets, and it looks like a failing drive.
# Telling Linux to use plain usb-storage for that one device (the "u" quirk)
# usually fixes it. Also spot USB 3 drives stuck at USB 2 speed (bad cable).
_UAS_ERROR_RE = re.compile(r"uas_eh_|uas-tag|uas_zap|uas_.*abort", re.IGNORECASE)


def _read_sys(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _usb3_capable(usb_device):
    """True when a USB device can run at USB 3 speed. A USB 3 device plugged in
    over USB 2 reports version 2.10, so its SuperSpeed capability is read from
    the BOS descriptor; the version is the fallback when that isn't available."""
    try:
        bos = Path(usb_device, "bos_descriptors").read_bytes()
    except OSError:
        bos = b""
    position = bos[0] if len(bos) >= 5 and bos[1] == 0x0F else 0
    while position and position + 3 <= len(bos):
        length = bos[position]
        if length < 3:
            break
        if bos[position + 1] == 0x10 and bos[position + 2] in (0x03, 0x0A):
            return True  # SuperSpeed or SuperSpeedPlus device capability
        position += length
    version = _read_sys(Path(usb_device, "version"))
    try:
        return float(version.split()[0]) >= 3 if version else False
    except ValueError:
        return False


def _kernel_log():
    """This boot's kernel messages, or '' if they can't be read."""
    out = _run_quiet(["journalctl", "-k", "-b", "--no-pager", "-o", "cat"], timeout=30)
    return out or _run_quiet(["dmesg"], timeout=30) or ""


def usb_connection_findings(device, sys_root="/sys", kernel_log=None):
    """[(level, headline, explanation)] about how a Linux USB drive is connected."""
    name = os.path.basename(device)
    try:
        scsi = Path(os.path.realpath(f"{sys_root}/block/{name}/device"))
    except OSError:
        return []
    interface = usb_device = None
    for parent in scsi.parents:
        if interface is None and (parent / "bInterfaceNumber").exists():
            interface = parent
        if (parent / "idVendor").exists():
            usb_device = parent
            break
    if interface is None or usb_device is None:
        return []
    driver = os.path.basename(os.path.realpath(interface / "driver"))
    vid, pid = _read_sys(usb_device / "idVendor"), _read_sys(usb_device / "idProduct")
    findings = []

    if driver == "uas" and vid and pid:
        log = _kernel_log() if kernel_log is None else kernel_log
        port = usb_device.name
        uas_errors = sum(1 for line in log.splitlines()
                         if f"[{name}]" in line and _UAS_ERROR_RE.search(line))
        resets = sum(1 for line in log.splitlines()
                     if f"usb {port}: reset" in line)
        if uas_errors or resets >= 2:
            quirk = f"{vid}:{pid}:u"
            existing = _read_sys(f"{sys_root}/module/usb_storage/parameters/quirks")
            # Linux uses the first entry for a device, so add "u" to any
            # flags this device already has rather than a second entry.
            # IDs are compared as numbers: "bc2" and "0bc2" are the same.
            def ids(entry):
                parts = entry.split(":")
                try:
                    return int(parts[0], 16), int(parts[1], 16)
                except (IndexError, ValueError):
                    return None

            device = ids(quirk)
            entries, merged = [], False
            for entry in (q.strip() for q in existing.split(",")):
                if not entry:
                    continue
                if ids(entry) == device:
                    if merged:
                        continue  # a later entry for this device would be ignored anyway
                    flags = entry.split(":")[2] if entry.count(":") == 2 else ""
                    entry = f"{vid}:{pid}:{flags if 'u' in flags else flags + 'u'}"
                    merged = True
                entries.append(entry)
            value = ",".join(entries if merged else entries + [quirk])
            seen = ", ".join(x for x in (
                f"{uas_errors} UAS command error(s)" if uas_errors else "",
                f"{resets} connection reset(s)" if resets else "") if x)
            findings.append((
                HEALTH_WARNING,
                f"The USB connection is resetting ({seen} since this computer started)",
                "Linux talks to this drive with the fast UAS method, which some USB "
                "enclosures handle badly: commands time out and the connection resets. It "
                "looks like a failing drive and can cause read errors. Telling Linux to use "
                "the older usb-storage method for this one drive usually fixes it:\n"
                f"  echo {value} | sudo tee /sys/module/usb_storage/parameters/quirks\n"
                "then unplug the drive and plug it back in. To keep the setting after a "
                "restart, put this line in /etc/modprobe.d/usb-storage-quirks.conf:\n"
                f"  options usb-storage quirks={value}\n"
                "(and rebuild the initramfs if your system loads usb-storage early), or add "
                f"usb-storage.quirks={value} to the kernel command line.",
            ))

    if _read_sys(usb_device / "speed") == "480" and _usb3_capable(usb_device):
        findings.append((
            HEALTH_NOTE,
            "Connected at USB 2 speed, although the drive supports USB 3",
            "Reading and copying will be about ten times slower than they could be. Try "
            "the drive's own USB 3 cable and a USB 3 port (often blue, or marked SS).",
        ))
    return findings


def quick_health_check(target, use_sudo=False):
    """Read a drive's health. Uses smartctl when installed, else what the OS reports."""
    if target.kind == "SD card":
        report = HealthReport(target)
        report.problem = ("SD cards don't report health data. The extended check can "
                          "still read every file to find any that are damaged.")
        return report
    if shutil.which("smartctl") and not _is_wsl():
        data, used_type = _read_smart(target, use_sudo)
        report = interpret_smart(target, data)
        report.device_type = used_type
        if report.smart_available or platform.system() == "Linux":
            _add_usb_findings(report)
            return report
    report = _basic_health(target)
    _add_usb_findings(report)
    return report


def _add_usb_findings(report):
    target = report.target
    if platform.system() != "Linux" or _is_wsl() or target.transport != "usb":
        return
    try:
        findings = usb_connection_findings(target.device)
    except Exception:
        return  # advice about the connection is never worth failing a health check
    for level, headline, explanation in findings:
        report.add(level, headline, explanation)


# Alan 9/28/26 - Every health check is appended to a log kept with the file
# lists, so the health of unplugged drives can be seen and trends tracked.
HEALTH_LOG_NAME = "Drive health log.tsv"
_HEALTH_LOG_COLUMNS = [
    "date", "drive", "model", "serial", "check", "health", "power_on_hours",
    "temperature_c", "reallocated_sectors", "pending_sectors", "uncorrectable_sectors",
    "read_errors", "connection_errors", "ssd_wear_percent", "ssd_media_errors",
    "last_self_test", "notes", "volume_ids",
]
# Counters where any increase since the last check is worth pointing out.
_HEALTH_TREND_METRICS = {
    "reallocated_sectors": "Replaced bad sectors",
    "pending_sectors": "Unreadable sectors waiting to be replaced",
    "uncorrectable_sectors": "Sectors that failed the drive's surface scan",
    "read_errors": "Uncorrectable read errors",
    "connection_errors": "Cable/USB connection errors",
    "ssd_media_errors": "SSD data errors",
}


def health_log_path(list_dir=None):
    return Path(list_dir or default_inventory_dir()) / HEALTH_LOG_NAME


def _health_drive_key(row):
    """Identify a drive across log entries: serial number, else name + model."""
    if row.get("serial"):
        return ("serial", row["serial"])
    return ("name", row.get("drive", ""), row.get("model", ""))


def read_health_log(list_dir=None):
    path = health_log_path(list_dir)
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f, delimiter="\t"))
    except OSError:
        return []


def append_health_log(report, check="quick", notes=None, list_dir=None, when=None):
    """Add one health check to the log. Returns the row written, or None."""
    if report.verdict == HEALTH_UNKNOWN and not report.metrics and notes is None:
        return None  # nothing was learned about this drive
    if notes is None:
        worrying = [h for level, h, _ in report.findings if level in (HEALTH_WARNING, HEALTH_FAILING)]
        notes = "; ".join(worrying) or "No warning signs"
    target = report.target
    row = {
        "date": (when or datetime.now()).strftime("%Y-%m-%d %H:%M"),
        "drive": target.name,
        "model": target.model,
        "serial": report.serial,
        "check": check,
        "health": _VERDICT_TEXT[report.verdict],
        "notes": notes,
        "volume_ids": ",".join(target.volume_ids),
    }
    for key, value in report.metrics.items():
        row[key] = value
    path = health_log_path(list_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _upgrade_health_log_columns(path)
        new_file = not path.exists() or path.stat().st_size == 0
        with open(path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_HEALTH_LOG_COLUMNS, delimiter="\t",
                                    extrasaction="ignore", lineterminator="\n")
            if new_file:
                writer.writeheader()
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    except OSError as e:
        print(f"  (Couldn't write the health log {path}: {e})")
        return None
    return row


def _upgrade_health_log_columns(path):
    """Rewrite a log made by an earlier version so it has the current columns."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if reader.fieldnames is None or reader.fieldnames == _HEALTH_LOG_COLUMNS:
                return
            rows = list(reader)
    except OSError:
        return
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_HEALTH_LOG_COLUMNS, delimiter="\t",
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _as_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def health_changes(previous, current):
    """Plain-language list of counters that went up between two log rows."""
    changes = []
    for key, label in _HEALTH_TREND_METRICS.items():
        before, after = _as_number(previous.get(key)), _as_number(current.get(key))
        if before is not None and after is not None and after > before:
            changes.append(f"{label} rose from {before:g} to {after:g}")
    if previous.get("health") and current.get("health") and previous["health"] != current["health"]:
        changes.append(f"Health changed from {previous['health']} to {current['health']}")
    return changes


def _previous_log_row(rows, key):
    for row in reversed(rows):
        if _health_drive_key(row) == key and row.get("check") != "file read":
            return row
    return None


def log_and_compare(report, check="quick", list_dir=None):
    """Write the report to the health log and print what changed since the last check."""
    rows = read_health_log(list_dir)
    row = append_health_log(report, check=check, list_dir=list_dir)
    if row is None:
        return
    previous = _previous_log_row(rows, _health_drive_key(row))
    if previous is None:
        print("  First check of this drive in the health log.")
        return
    changes = health_changes(previous, row)
    since = previous.get("date", "")[:10]
    if changes:
        print(f"  Since the last check on {since}:")
        for change in changes:
            print(f"    ! {change}")
    else:
        print(f"  No change since the last check on {since}.")


def latest_health_by_drive(rows):
    """Most recent log row for each drive, oldest drive first."""
    latest = {}
    for row in rows:
        latest[_health_drive_key(row)] = row
    return latest


def print_health_history(list_dir=None, max_rows_per_drive=10):
    rows = read_health_log(list_dir)
    if not rows:
        print("\nNo health checks have been logged yet.")
        return
    by_drive = {}
    for row in rows:
        by_drive.setdefault(_health_drive_key(row), []).append(row)
    print(f"\nDrive health history (from {health_log_path(list_dir)}):")
    for entries in by_drive.values():
        last = entries[-1]
        ident = ", ".join(x for x in (last.get("model"), f"serial {last['serial']}" if last.get("serial") else "") if x)
        print(f"\n{last.get('drive', '?')}" + (f" ({ident})" if ident else ""))
        for row in entries[-max_rows_per_drive:]:
            numbers = []
            for key, label in (("power_on_hours", "h on"), ("temperature_c", "°C"),
                               ("reallocated_sectors", "replaced"), ("pending_sectors", "pending"),
                               ("uncorrectable_sectors", "uncorrectable"), ("ssd_wear_percent", "% worn")):
                if row.get(key) not in (None, ""):
                    numbers.append(f"{row[key]} {label}" if label != "°C" else f"{row[key]}°C")
            detail = ", ".join(numbers)
            print(f"  {row.get('date', ''):<16}  {row.get('check', ''):<9} {row.get('health', ''):<8} {detail}")
            if row.get("notes") and row["notes"] != "No warning signs":
                print(f"{'':30}{row['notes']}")
        if len(entries) >= 2:
            first_trend = health_changes(entries[0], entries[-1])
            if first_trend:
                print(f"  Since {entries[0].get('date', '')[:10]}: " + "; ".join(first_trend))


def print_unconnected_health(targets, list_dir=None):
    """Last logged health of drives that aren't connected now."""
    rows = read_health_log(list_dir)
    if not rows:
        return
    connected_names = {t.name for t in targets}
    others = [row for key, row in latest_health_by_drive(rows).items()
              if row.get("drive") not in connected_names]
    if not others:
        return
    print("\nOther drives in the health log (not connected now):")
    for row in others:
        print(f"  {row.get('drive', '?'):<28} {row.get('health', ''):<8} last checked {row.get('date', '')[:10]}"
              + (f"  ({row['notes']})" if row.get("notes") and row["notes"] != "No warning signs" else ""))



_VERDICT_TEXT = {
    HEALTH_GOOD: "GOOD",
    HEALTH_WARNING: "WARNING",
    HEALTH_FAILING: "FAILING",
    HEALTH_UNKNOWN: "UNKNOWN",
}


def print_health_report(report):
    target = report.target
    print(f"\n{target.description()}")
    where = ", ".join(target.mounts) if target.mounts else "not mounted"
    print(f"  Device: {target.device}   Mounted at: {where}")
    if report.problem and not report.smart_available:
        # No health data, but USB connection findings may still follow.
        problem = report.problem.rstrip(".")
        print(f"  Health: {_VERDICT_TEXT[report.verdict]}. {problem[0].upper()}{problem[1:]}.")
    else:
        print(f"  Health: {_VERDICT_TEXT[report.verdict]}")
    for fact in report.facts:
        print(f"  {fact}")
    if report.self_test_running is not None:
        print(f"  A self-test is running now: {report.self_test_running}% left")
    for level, headline, explanation in report.findings:
        marker = {HEALTH_GOOD: "  ok", HEALTH_NOTE: "  - ", HEALTH_WARNING: "  ! ",
                  HEALTH_FAILING: "  !!"}.get(level, "    ")
        print(f"{marker} {headline}")
        if explanation:
            print(_wrap(explanation, indent="       "))


def start_extended_self_test(report, use_sudo=False):
    """Start the drive's extended (full-surface) self-test. Returns (ok, message)."""
    data = _run_smartctl(["-t", "long"], report.target.device, use_sudo, report.device_type)
    messages = _smartctl_messages(data)
    if "_error" in data:
        return False, data["_error"]
    exit_status = data.get("smartctl", {}).get("exit_status", 0)
    if exit_status & 0b111 and "has begun" not in messages.lower():
        return False, messages or "the drive refused to start a self-test"
    return True, messages


def _self_test_progress(report, use_sudo=False):
    """Percent remaining of a running self-test, 0 when finished, None if unknown."""
    data = _run_smartctl(["-c", "-l", "selftest"], report.target.device, use_sudo, report.device_type)
    status = data.get("ata_smart_data", {}).get("self_test", {}).get("status", {})
    if status:
        return status.get("remaining_percent", 0) if status.get("value", 0) >> 4 == 15 else 0
    nvme = data.get("nvme_self_test_log", {})
    if nvme:
        if nvme.get("current_self_test_operation", {}).get("value"):
            return 100 - nvme.get("current_self_test_completion_percent", 0)
        return 0
    return None


def watch_self_tests(reports, use_sudo=False, poll_seconds=60):
    """Show progress of running self-tests until they finish or Ctrl-C."""
    print("\nWatching the self-test(s). Press Ctrl-C to stop watching; the drives keep "
          "testing on their own and you can see the result later with a quick check.")
    pending = list(reports)
    try:
        while pending:
            parts = []
            if use_sudo:
                # Renew sudo's timestamp so hours-long tests can still be polled.
                subprocess.run(["sudo", "-n", "-v"], capture_output=True)
            for report in list(pending):
                remaining = _self_test_progress(report, use_sudo)
                if remaining == 0:
                    pending.remove(report)
                    result = quick_health_check(report.target, use_sudo)
                    _clear_progress_line()
                    print(f"\n{report.target.name}: self-test finished.")
                    print_health_report(result)
                    log_and_compare(result, check="self-test")
                else:
                    label = "?" if remaining is None else f"{100 - remaining}%"
                    parts.append(f"{report.target.name}: {label} done")
            if pending:
                _print_status_line("Self-test running. " + "; ".join(parts) + f"  [{datetime.now():%H:%M}]")
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        _clear_progress_line()
        print("\nStopped watching. The self-test continues inside the drive; don't unplug it "
              "until it's done. Run a quick check later to see the result.")


def read_every_file(mount, report_path=None):
    """Read every file under *mount* to find any that can't be read.

    Returns (files_read, bytes_read, errors [(path, message)], finished).
    """
    try:
        expected = shutil.disk_usage(mount).used
    except OSError:
        expected = 0
    buf = bytearray(8 << 20)
    errors = []
    walk_errors = []  # folders (and files) the directory walk itself couldn't open
    placeholders = set()  # cloud-only files: reading one would download it
    files = 0
    done_bytes = 0
    start = last = time.time()
    finished = False
    try:
        for path, is_link, _size, _mtime in find_files(mount, None, errors=walk_errors,
                                                       placeholders=placeholders):
            if is_link or path in placeholders:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
                try:
                    if hasattr(os, "posix_fadvise"):
                        # Drop cached copies so the disk itself is read.
                        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                    with os.fdopen(fd, "rb", buffering=0, closefd=False) as f:
                        while True:
                            n = f.readinto(buf)
                            if not n:
                                break
                            done_bytes += n
                finally:
                    os.close(fd)
                files += 1
            except OSError as e:
                errors.append((path, e.strerror or str(e)))
            now = time.time()
            if now - last >= 2:
                last = now
                rate = done_bytes / max(now - start, 1e-6)
                pct = f" of {_human_size(expected)} ({done_bytes / expected * 100:.0f}%)" if expected else ""
                eta = ""
                if expected and rate > 0 and done_bytes < expected:
                    eta = f", about {_format_duration((expected - done_bytes) / rate)} left"
                _print_status_line(
                    f"Read {files:,} files, {_human_size(done_bytes) or '0 B'}{pct} at "
                    f"{_human_size(rate) or '0 B'}/s{eta}; {len(errors):,} unreadable"
                )
        finished = True
    except KeyboardInterrupt:
        pass
    # A folder that can't be opened is as telling as a file that can't be read.
    errors = [(path, message) for _kind, path, _io, message in walk_errors] + errors
    _clear_progress_line()
    if errors and report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            for path, message in errors:
                f.write(f"{path}\t{message}\n")
    return files, done_bytes, errors, finished


def _health_report_path(target):
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    return _unused_path(default_inventory_dir() / "Health checks" /
                        _safe_filename(f"{stamp} {target.name} unreadable files.txt"))


def _print_read_check_result(target, files, done_bytes, errors, finished, report_path):
    state = "Finished" if finished else "Stopped early"
    print(f"\n{target.name}: {state}. Read {files:,} files ({_human_size(done_bytes) or '0 B'}).")
    if not errors:
        print("  Every file could be read.")
        return
    print(f"  {len(errors):,} file(s) could not be read. These may be damaged:")
    for path, message in errors[:20]:
        print(f"    {path}  ({message})")
    if len(errors) > 20:
        print(f"    ... and {len(errors) - 20:,} more")
    print(f"  The full list is in: {report_path}")


def run_health_check(targets, extended=False, interactive=True):
    """Check each target's health; with extended=True also run the long check.
    Returns False if any drive showed warning signs or is failing."""
    for line in health_tool_advice():
        print()
        print(_wrap(line, indent=""))
    use_sudo = False
    if shutil.which("smartctl") and any(t.kind != "SD card" for t in targets):
        use_sudo = ensure_sudo_for_health(interactive)
    reports = []
    for target in targets:
        report = quick_health_check(target, use_sudo)
        print_health_report(report)
        log_and_compare(report, check="quick")
        reports.append(report)
    healthy = not any(r.verdict in (HEALTH_WARNING, HEALTH_FAILING) for r in reports)
    if not extended:
        return healthy

    self_testable = [r for r in reports if r.smart_available and r.self_test_minutes is not None]
    readable = [r for r in reports if r not in self_testable and r.target.mounts]
    started = []
    for report in self_testable:
        if report.self_test_running is not None:
            print(f"\n{report.target.name}: a self-test is already running.")
            started.append(report)
            continue
        minutes = (report.self_test_minutes or {}).get("extended")
        estimate = f" The drive estimates about {_format_duration(minutes * 60)}." if minutes else ""
        print(f"\n{report.target.name}: starting the drive's extended self-test.{estimate}")
        ok, message = start_extended_self_test(report, use_sudo)
        if ok:
            started.append(report)
        else:
            print(f"  The drive didn't start the self-test ({message.strip() or 'no reason given'}).")
            if report.target.mounts:
                readable.append(report)
    if started:
        print(_wrap(
            "The test runs inside the drive, so you can keep using the computer. Keep "
            "the drive plugged in until it finishes. Some USB enclosures put the drive "
            "to sleep when it's idle, which stops the test; if that happens, the result "
            "will say it was interrupted.", indent="  "))
        if not interactive or _confirm("Wait here and show progress?"):
            watch_self_tests(started, use_sudo)
    for report in readable:
        target = report.target
        print(f"\n{target.name}: this drive can't test itself, so every file on it will be "
              "read to find any that can't be read.")
        print("  This takes about as long as copying the whole drive. Press Ctrl-C to stop.")
        report_path = _health_report_path(target)
        files, done_bytes, errors, finished = read_every_file(target.mounts[0], report_path)
        _print_read_check_result(target, files, done_bytes, errors, finished, report_path)
        read_report = HealthReport(target)
        read_report.serial = report.serial
        read_report.verdict = HEALTH_WARNING if errors else (HEALTH_GOOD if finished else HEALTH_UNKNOWN)
        state = "finished" if finished else "stopped early"
        append_health_log(read_report, check="file read", notes=(
            f"Read {files:,} files ({_human_size(done_bytes) or '0 B'}), {state}; "
            f"{len(errors):,} unreadable" + (f", listed in {report_path.name}" if errors else "")))
        healthy = healthy and not errors
    return healthy


def health_targets_for(directory=None):
    """All drives, or just the one holding *directory*."""
    targets = detect_health_targets()
    if directory is None:
        return targets
    root = _root_cache_prefix(_mount_root(directory))
    return [t for t in targets if any(_root_cache_prefix(m) == root for m in t.mounts)]
