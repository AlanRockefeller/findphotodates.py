"""Regression tests for fixes from the November 2026 code audit."""

import os
import tempfile
import types

import pytest

import findphotodates as fpd


class NoExifTool:
    aborted = False

    def abort(self):
        self.aborted = True

    def start(self):
        pass

    def stop(self):
        pass

    def batch_query(self, filepaths, fast2=False):
        return {fp: (None, None, None) for fp in filepaths}


def test_bad_config_raises_systemexit_with_the_reason(tmp_path, monkeypatch, patch_everywhere):
    config = tmp_path / "config.json"
    config.write_text("{not json", encoding="utf-8")
    patch_everywhere("CONFIG_FILE", config)
    with pytest.raises(SystemExit) as caught:
        fpd.load_config()
    assert "Invalid JSON" in str(caught.value.code)


def test_extension_filter_accepts_a_leading_dot():
    args = types.SimpleNamespace(extension=".JPG", video=False, only_photos=False,
                                 only_media=False)
    assert fpd._resolve_extensions(args) == {"jpg"}


def _scan(root, output, monkeypatch, tmpdir):
    monkeypatch.setattr(fpd, "ExifToolPersistent", NoExifTool)
    monkeypatch.setattr(tempfile, "tempdir", str(tmpdir))
    return fpd.run_scan(str(root), str(output), None, quiet=True,
                        hash_options=fpd.parse_hash_args(hash_mode="off"), workers=1,
                        min_image_size=0, reuse_other_lists=False)


def test_empty_scan_warning_log_is_removed(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"x" * 10)
    logs = tmp_path / "tmp"
    logs.mkdir()
    assert _scan(root, tmp_path / "list.tsv", monkeypatch, logs)
    assert list(logs.glob("findphotodates.error.*")) == []


def test_scan_warning_log_with_warnings_is_kept_and_named(tmp_path, capsys):
    path = tmp_path / "warnings.log"
    with fpd._ScanWarningLog(str(path)) as log:
        log.write("Warning: ExifTool worker 1 failed\n")
    assert path.exists()
    assert str(path) in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX permissions, non-root")
def test_read_check_reports_folders_it_cannot_open(tmp_path):
    (tmp_path / "ok.txt").write_bytes(b"fine")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "inside.txt").write_bytes(b"hidden")
    os.chmod(locked, 0)
    try:
        files, _bytes, errors, finished = fpd.read_every_file(str(tmp_path))
    finally:
        os.chmod(locked, 0o755)
    assert finished and files == 1
    assert [path for path, _ in errors] == [str(locked)]


@pytest.mark.skipif(not hasattr(os.stat_result, "st_blocks"), reason="needs st_blocks")
def test_read_check_does_not_read_cloud_placeholders(tmp_path):
    (tmp_path / "real.jpg").write_bytes(b"x" * 300)
    with open(tmp_path / "cloud.jpg", "wb") as handle:
        handle.truncate(2_000_000)
    if os.stat(tmp_path / "cloud.jpg").st_blocks != 0:
        pytest.skip("this filesystem doesn't make sparse files")
    files, done_bytes, errors, _finished = fpd.read_every_file(str(tmp_path))
    assert (files, done_bytes, errors) == (1, 300, [])


def test_disconnect_save_failure_is_reported_not_raised(tmp_path, capsys):
    gone = tmp_path / "unplugged" / "list.tsv"  # its folder no longer exists
    cleaned = []
    error = OSError(5, "Input/output error")
    assert fpd._check_drive_disconnect(error, str(gone), [], str(tmp_path),
                                       fpd.parse_hash_args(hash_mode="off"), False,
                                       lambda: cleaned.append(True))
    assert cleaned == [True]
    assert "Could not write inventory" in capsys.readouterr().out


def test_settings_changes_from_two_threads_are_both_kept(tmp_path, monkeypatch, patch_everywhere):
    import threading

    patch_everywhere("CONFIG_FILE", tmp_path / "config.json")

    def bump(key):
        for _ in range(40):
            fpd.update_config(lambda c: c.update({key: c.get(key, 0) + 1}))

    threads = [threading.Thread(target=bump, args=(key,)) for key in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    config = fpd.load_config()
    assert (config["a"], config["b"]) == (40, 40)


def test_coverage_change_keeps_dates_recorded_meanwhile(tmp_path, monkeypatch, patch_everywhere):
    patch_everywhere("CONFIG_FILE", tmp_path / "config.json")
    fpd.update_coverage_settings(lambda s: setattr(s.drive("A.tsv"), "last_connected",
                                                   "2026-11-01"))
    settings = fpd.update_coverage_settings(lambda s: setattr(s.drive("A.tsv"), "role", "weak"))
    assert (settings.drive("A.tsv").role, settings.drive("A.tsv").last_connected) == \
        ("weak", "2026-11-01")
    before = (tmp_path / "config.json").stat().st_mtime_ns
    fpd.update_coverage_settings(lambda s: False)  # no change: not saved
    assert (tmp_path / "config.json").stat().st_mtime_ns == before


@pytest.mark.parametrize("argv, expected", [
    (["--health"], None),
    (["--health", "--directory", "."], os.path.abspath(".")),
])
def test_health_directory_dot_means_this_folder(argv, expected, monkeypatch):
    import sys

    asked = []
    monkeypatch.setattr(fpd, "health_targets_for", lambda d=None: asked.append(d) or ["x"])
    monkeypatch.setattr(fpd, "run_health_check", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["findphotodates.py", *argv])
    with pytest.raises(SystemExit) as done:
        fpd.main()
    assert done.value.code == 0 and asked == [expected]
