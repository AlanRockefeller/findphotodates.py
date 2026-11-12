"""The ExifTool version check: only a missing or old ExifTool needs the download link."""

import subprocess
import types

import findphotodates as fpd
import findphotodates_menu as menu


def _fake_run(monkeypatch, stdout="", returncode=0, error=None):
    def run(cmd, *args, **kwargs):
        if error:
            raise error
        return types.SimpleNamespace(returncode=returncode, stdout=stdout)
    monkeypatch.setattr(menu.subprocess, "run", run)


def test_version_is_read_from_exiftool(monkeypatch):
    _fake_run(monkeypatch, stdout="13.55\n")
    assert fpd._exiftool_version() == 13.55
    assert fpd._exiftool_available()


def test_missing_or_failing_exiftool_has_no_version(monkeypatch):
    _fake_run(monkeypatch, error=FileNotFoundError())
    assert fpd._exiftool_version() is None
    _fake_run(monkeypatch, error=subprocess.TimeoutExpired("exiftool", 10))
    assert fpd._exiftool_version() is None
    _fake_run(monkeypatch, returncode=1)
    assert fpd._exiftool_version() is None
    assert not fpd._exiftool_available()


def test_unreadable_version_still_runs_but_counts_as_old(monkeypatch):
    _fake_run(monkeypatch, stdout="garbage")
    usable, up_to_date, message = fpd._exiftool_status(fpd._exiftool_version())
    assert usable and not up_to_date
    assert message.startswith("This ExifTool is out of date")


def test_status_only_asks_for_an_update_when_needed():
    assert fpd._exiftool_status(None)[:2] == (False, False)
    usable, up_to_date, message = fpd._exiftool_status(10.80)
    assert usable and not up_to_date and "10.80" in message
    assert fpd._exiftool_status(fpd.EXIFTOOL_MIN_VERSION)[:2] == (True, True)
    assert fpd._exiftool_status(13.55) == (True, True, "ExifTool 13.55 is ready.")
