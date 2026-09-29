"""Tests for scanning drives that can't be read cleanly: partial saves, read
errors, slowing down on a struggling drive, and health-log warnings."""

import errno
import os
import threading
import time

import pytest

import findphotodates as fpd


def _make_tree(root, names):
    for rel in names:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel, encoding="utf-8")


def _rows(out):
    header, _ = fpd.read_inventory_header(out)
    root = header["inventory_root"]
    return {os.path.relpath(line.split("\t")[0], root).replace(os.sep, "/")
            for line in out.read_text(encoding="utf-8").splitlines()
            if not line.startswith("#") and not line.startswith("filepath")}


NAMES = [f"a/{i}.txt" for i in range(6)] + [f"b/{i}.txt" for i in range(6)]


def test_ctrl_c_keeps_rows_the_scan_has_not_reached(tmp_path, monkeypatch):
    drive = tmp_path / "drive"
    _make_tree(drive, NAMES)
    out = tmp_path / "list.tsv"
    assert fpd.run_scan(str(drive), str(out), None, quiet=True) is not False
    assert _rows(out) == set(NAMES)

    calls = {"n": 0}
    real = fpd.get_content_hash

    def interrupt_after_three(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 4:
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(fpd, "get_content_hash", interrupt_after_three)
    assert fpd.run_scan(str(drive), str(out), None, quiet=True) is False
    # Only 3 files were processed, but the saved list still has all 12.
    assert _rows(out) == set(NAMES)


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions and a non-root user")
def test_unreadable_folder_keeps_old_rows_and_is_reported(tmp_path, capsys):
    drive = tmp_path / "drive"
    _make_tree(drive, NAMES)
    out = tmp_path / "list.tsv"
    assert fpd.run_scan(str(drive), str(out), None, quiet=True) is not False
    os.chmod(drive / "b", 0)
    try:
        assert fpd.run_scan(str(drive), str(out), None, quiet=False) is not False
    finally:
        os.chmod(drive / "b", 0o755)
    printed = capsys.readouterr().out
    assert _rows(out) == set(NAMES)  # b/* kept from the previous list
    assert "Removed" not in printed.split("Changes since the last scan:")[1]
    assert "1 folder(s) couldn't be opened (Permission denied)" in printed
    assert "6 entries from the previous list were kept" in printed
    assert "usually mean the drive is failing" not in printed  # permissions aren't I/O errors
    reports = list((tmp_path / "Read errors").iterdir())
    assert len(reports) == 1 and "drive/b" in reports[0].read_text(encoding="utf-8").replace(os.sep, "/")


def _fake_exiftool(monkeypatch, error_for_all=True, delay=0.0):
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    class FakeExifTool:
        def __init__(self):
            self.last_errors = {}

        def start(self):
            pass

        def stop(self):
            pass

        def batch_query(self, filepaths, fast2=False):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(delay)
            with lock:
                active["now"] -= 1
            self.last_errors = {fp: "Error reading file" for fp in filepaths} if error_for_all else {}
            return {fp: (None, None, None) for fp in filepaths}

    monkeypatch.setattr(fpd, "ExifToolPersistent", FakeExifTool)
    return active


def test_read_errors_slow_the_scan_down_and_warn(tmp_path, monkeypatch, capsys):
    drive = tmp_path / "drive"
    _make_tree(drive, [f"p/{i}.jpg" for i in range(8)])
    _fake_exiftool(monkeypatch)
    monkeypatch.setattr(fpd, "EXIFTOOL_BATCH_SIZE", 2)
    out = tmp_path / "list.tsv"
    assert fpd.run_scan(str(drive), str(out), {"jpg"}, quiet=False, workers=4,
                        min_image_size=0) is not False
    printed = capsys.readouterr().out
    assert "8 photo(s)/video(s) couldn't be read to get their dates (Error reading file)" in printed
    assert "usually mean the drive is failing" in printed


def test_throttle_limits_concurrent_readers():
    throttle = fpd._ReadThrottle(3)
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def reader():
        with throttle:
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1

    throttle.set_limit(1)
    threads = [threading.Thread(target=reader) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert active["max"] == 1 and throttle.limit == 1


def test_is_io_error():
    assert fpd._is_io_error(OSError(errno.EIO, "Input/output error"))
    assert not fpd._is_io_error(PermissionError(errno.EACCES, "Permission denied"))
    assert not fpd._is_io_error(FileNotFoundError(errno.ENOENT, "No such file"))
    assert fpd._is_read_error_message("Error reading file")
    assert fpd._is_read_error_message("Error opening file")
    assert not fpd._is_read_error_message("JPEG format error")


def _failing_log(tmp_path, health="FAILING", pending="2440", volume_ids="8A54-01D8", drive="Crime Pays"):
    path = tmp_path / fpd.HEALTH_LOG_NAME
    cols = fpd._HEALTH_LOG_COLUMNS
    row = {c: "" for c in cols}
    row.update(date="2026-09-28 14:58", drive=drive, check="quick", health=health,
               pending_sectors=pending, notes="2,440 sector(s) are unreadable",
               volume_ids=volume_ids)
    path.write_text("\t".join(cols) + "\n" + "\t".join(row[c] for c in cols) + "\n", encoding="utf-8")


def test_health_warning_before_scanning_a_failing_drive(tmp_path, monkeypatch, capsys):
    drive_dir = tmp_path / "Crime Pays"
    _make_tree(drive_dir, ["a.txt"])
    _failing_log(tmp_path)
    monkeypatch.setattr(fpd, "default_inventory_dir", lambda: tmp_path)
    monkeypatch.setattr(fpd, "identify_drive",
                        lambda path: fpd.DriveInfo(str(drive_dir), "Crime Pays", "8a54-01d8"))
    assert fpd.run_scan(str(drive_dir), str(tmp_path / "out.tsv"), None, quiet=False) is not False
    printed = capsys.readouterr().out
    assert "this drive was rated FAILING: 2,440 sector(s) are unreadable" in " ".join(printed.split())


def test_no_warning_for_minor_or_other_drives(tmp_path, monkeypatch):
    monkeypatch.setattr(fpd, "default_inventory_dir", lambda: tmp_path)
    drive = fpd.DriveInfo("/x", "Lightroom", "768C-FB5E")
    _failing_log(tmp_path, health="WARNING", pending="0", volume_ids="768C-FB5E", drive="Lightroom")
    assert fpd._troubling_health_record("/x", drive) is None  # e.g. one cable error
    _failing_log(tmp_path)
    assert fpd._troubling_health_record("/x", drive) is None  # a different drive
    old_style = fpd.DriveInfo("/y", "Crime Pays", "")
    _failing_log(tmp_path, volume_ids="")  # rows from before volume IDs were logged
    assert fpd._troubling_health_record("/y", old_style)["health"] == "FAILING"


def test_old_health_log_gets_new_columns(tmp_path):
    path = tmp_path / fpd.HEALTH_LOG_NAME
    old_cols = fpd._HEALTH_LOG_COLUMNS[:-1]
    path.write_text("\t".join(old_cols) + "\n" + "\t".join(["2026-01-01 00:00", "Old"] + [""] * (len(old_cols) - 2)) + "\n",
                    encoding="utf-8")
    report = fpd.HealthReport(fpd.HealthTarget("/dev/sdz", "New", volume_ids=["1234abcd"]))
    report.verdict = fpd.HEALTH_GOOD
    report.metrics = {"pending_sectors": 0}
    fpd.append_health_log(report, list_dir=tmp_path)
    rows = fpd.read_health_log(tmp_path)
    assert path.read_text(encoding="utf-8").splitlines()[0].split("\t") == fpd._HEALTH_LOG_COLUMNS
    assert [r["drive"] for r in rows] == ["Old", "New"]
    assert rows[1]["volume_ids"] == "1234-ABCD"


def test_health_targets_follow_main_menu_order(monkeypatch):
    targets = [fpd.HealthTarget("/dev/sdb", "Z", mounts=["/run/media/u/Zed"]),
               fpd.HealthTarget("/dev/sdc", "Unmounted"),
               fpd.HealthTarget("/dev/nvme1n1", "C", mounts=["/mnt/c"])]
    monkeypatch.setattr(fpd, "_detect_health_targets", lambda: targets)
    assert [t.name for t in fpd.detect_health_targets()] == ["C", "Z", "Unmounted"]


def test_read_problems_are_shown_during_the_scan(tmp_path, monkeypatch, capsys):
    drive = tmp_path / "drive"
    _make_tree(drive, [f"p/{i}.jpg" for i in range(3)])
    _fake_exiftool(monkeypatch)
    out = tmp_path / "list.tsv"
    assert fpd.run_scan(str(drive), str(out), {"jpg"}, quiet=False, min_image_size=0) is not False
    printed = capsys.readouterr().out
    live, summary = printed.split("Read problems:")
    for i in range(3):
        assert f"Can't read: p/{i}.jpg (Error reading file)" in live


def test_live_read_problems_are_capped(tmp_path, monkeypatch, capsys):
    drive = tmp_path / "drive"
    _make_tree(drive, [f"p/{i:02d}.jpg" for i in range(30)])
    _fake_exiftool(monkeypatch)
    out = tmp_path / "list.tsv"
    assert fpd.run_scan(str(drive), str(out), {"jpg"}, quiet=False, min_image_size=0) is not False
    printed = capsys.readouterr().out
    live = printed.split("Read problems:")[0]
    assert live.count("Can't read: ") == 20
    assert "(More than 20 read problems; the rest will be listed at the end" in live
    assert "30 photo(s)/video(s) couldn't be read" in printed


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions and a non-root user")
def test_ctrl_c_also_reports_read_problems(tmp_path, monkeypatch, capsys):
    drive = tmp_path / "drive"
    _make_tree(drive, NAMES)
    os.chmod(drive / "b", 0)
    calls = {"n": 0}
    real = fpd.get_content_hash

    def interrupt_late(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 6:  # after walking a/ (and failing to open b/)
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(fpd, "get_content_hash", interrupt_late)
    try:
        assert fpd.run_scan(str(drive), str(tmp_path / "list.tsv"), None, quiet=False) is False
    finally:
        os.chmod(drive / "b", 0o755)
    printed = capsys.readouterr().out
    after_interrupt = printed.split("Interrupted by Ctrl-C")[1]
    assert "Read problems:" in after_interrupt
    assert "1 folder(s) couldn't be opened (Permission denied)" in after_interrupt
    assert "entries from the previous list were kept" not in after_interrupt
