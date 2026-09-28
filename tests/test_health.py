"""Tests for drive health checks: smartctl JSON interpretation and the file read check."""

import os

import pytest

import findphotodates as fpd


def _target(**kw):
    defaults = dict(device="/dev/sda", name="Sierra Club", model="", size_bytes=5_000_000_000_000,
                    transport="usb", rotational=True, mounts=["/run/media/u/Sierra Club"])
    defaults.update(kw)
    return fpd.HealthTarget(**defaults)


def _attr(attr_id, name, raw, when_failed=""):
    return {"id": attr_id, "name": name, "value": 100, "worst": 100, "thresh": 0,
            "when_failed": when_failed, "raw": {"value": raw, "string": str(raw)}}


def _ata(attrs=(), passed=True, self_test_status=None, log=None, polling=None):
    data = {
        "smartctl": {"version": [7, 5], "exit_status": 0},
        "device": {"name": "/dev/sda", "type": "sat", "protocol": "ATA"},
        "model_name": "WDC WD50NDZW-11BHVS1",
        "smart_status": {"passed": passed},
        "power_on_time": {"hours": 17532},
        "temperature": {"current": 34},
        "ata_smart_attributes": {"revision": 16, "table": list(attrs)},
        "ata_smart_data": {"self_test": {
            "status": self_test_status or {"value": 0, "string": "completed without error", "passed": True},
            "polling_minutes": polling or {"short": 2, "extended": 724},
        }},
    }
    if log is not None:
        data["ata_smart_self_test_log"] = {"standard": {"revision": 1, "table": log, "count": len(log)}}
    return data


def test_healthy_usb_hard_drive():
    target = _target()
    log = [{"type": {"value": 1, "string": "Short offline"},
            "status": {"value": 0, "string": "Completed without error", "passed": True},
            "lifetime_hours": 17000}]
    report = fpd.interpret_smart(target, _ata([_attr(5, "Reallocated_Sector_Ct", 0)], log=log))
    assert report.verdict == fpd.HEALTH_GOOD
    assert report.smart_available
    assert report.findings[0][1] == "No warning signs"
    assert "Powered on for 17,532 hours (about 2.0 years)" in report.facts
    assert "Temperature now: 34°C" in report.facts
    assert "Last self-test: short offline, completed without error at 17,000 hours" in report.facts
    assert report.self_test_minutes == {"short": 2, "extended": 724}
    assert report.self_test_running is None
    assert target.model == "WDC WD50NDZW-11BHVS1"


def test_pending_sectors_are_a_warning():
    report = fpd.interpret_smart(_target(), _ata([
        _attr(197, "Current_Pending_Sector", 8),
        _attr(198, "Offline_Uncorrectable", 8),
    ]))
    assert report.verdict == fpd.HEALTH_WARNING
    headlines = [h for _, h, _ in report.findings]
    assert "8 sector(s) are unreadable and waiting to be replaced" in headlines
    assert "No warning signs" not in headlines


def test_a_few_reallocated_sectors_are_only_a_note():
    report = fpd.interpret_smart(_target(), _ata([_attr(5, "Reallocated_Sector_Ct", 3)]))
    assert report.verdict == fpd.HEALTH_GOOD
    assert (fpd.HEALTH_NOTE, "3 bad sector(s) have been replaced with spares") in [
        (level, h) for level, h, _ in report.findings]


def test_seagate_packed_raw_values_use_low_bits():
    # Seagate stores extra counters in the high bits of some raw values.
    report = fpd.interpret_smart(_target(), _ata([_attr(187, "Reported_Uncorrect", 0x0005_0000)]))
    assert report.verdict == fpd.HEALTH_GOOD


def test_failed_smart_status_and_threshold():
    report = fpd.interpret_smart(_target(), _ata(
        [_attr(5, "Reallocated_Sector_Ct", 3000, when_failed="now")], passed=False))
    assert report.verdict == fpd.HEALTH_FAILING
    assert any("FAILING" in h for _, h, _ in report.findings)


def test_failed_last_self_test_is_a_warning_but_aborted_is_not():
    failed = [{"type": {"string": "Extended offline"},
               "status": {"value": 7, "string": "Completed: read failure", "passed": False},
               "lifetime_hours": 100}]
    assert fpd.interpret_smart(_target(), _ata(log=failed)).verdict == fpd.HEALTH_WARNING
    aborted = [{"type": {"string": "Extended offline"},
                "status": {"value": 1, "string": "Aborted by host", "passed": False},
                "lifetime_hours": 100}]
    assert fpd.interpret_smart(_target(), _ata(log=aborted)).verdict == fpd.HEALTH_GOOD


def test_running_self_test_is_reported():
    status = {"value": 249, "string": "in progress, 90% remaining", "remaining_percent": 90}
    report = fpd.interpret_smart(_target(), _ata(self_test_status=status))
    assert report.self_test_running == 90


def test_nvme_wear_and_errors():
    data = {
        "smartctl": {"exit_status": 0},
        "model_name": "WD_BLACK SN850X 8000GB",
        "smart_status": {"passed": True},
        "nvme_smart_health_information_log": {
            "critical_warning": 0, "temperature": 45, "available_spare": 100,
            "available_spare_threshold": 10, "percentage_used": 93, "power_on_hours": 900,
            "media_errors": 2,
        },
        "nvme_self_test_log": {"current_self_test_operation": {"value": 0}, "table": []},
    }
    report = fpd.interpret_smart(_target(device="/dev/nvme1n1", transport="nvme", rotational=False), data)
    assert report.verdict == fpd.HEALTH_WARNING
    assert "Wear: 93% of its rated life used" in report.facts
    assert "Temperature now: 45°C" in report.facts
    headlines = " ".join(h for _, h, _ in report.findings)
    assert "93% of its rated life" in headlines and "2 data error(s)" in headlines
    assert report.self_test_minutes == {}


def test_nvme_critical_warning_is_failing():
    data = {"smart_status": {"passed": True},
            "nvme_smart_health_information_log": {"critical_warning": 4, "percentage_used": 1}}
    assert fpd.interpret_smart(_target(transport="nvme"), data).verdict == fpd.HEALTH_FAILING


def test_permission_denied_explains_how_to_fix():
    data = {"smartctl": {"exit_status": 2, "messages": [
        {"string": "Smartctl open device: /dev/sda [SAT] failed: Permission denied", "severity": "error"}]}}
    report = fpd.interpret_smart(_target(), data)
    assert report.verdict == fpd.HEALTH_UNKNOWN and not report.smart_available
    assert "administrator access" in report.problem


def test_unsupported_usb_bridge():
    data = {"smartctl": {"exit_status": 1, "messages": [
        {"string": "/dev/sdb: Unknown USB bridge [0x1058:0x2627 (0x1034)]", "severity": "error"}]}}
    report = fpd.interpret_smart(_target(), data)
    assert "doesn't report health data" in report.problem


def test_sd_card_is_not_sent_to_smartctl(monkeypatch):
    monkeypatch.setattr(fpd, "_run_smartctl", lambda *a, **k: pytest.fail("smartctl called"))
    sd = _target(device="/dev/mmcblk0", transport="mmc", name="OM SYSTEM")
    assert sd.kind == "SD card"
    report = fpd.quick_health_check(sd)
    assert "SD cards don't report health data" in report.problem


def test_usb_bridge_retry_with_sat(monkeypatch):
    calls = []

    def fake(args, device, use_sudo, device_type=None, timeout=120):
        calls.append(device_type)
        if device_type == "sat":
            return _ata()
        return {"smartctl": {"messages": [{"string": "Unknown USB bridge", "severity": "error"}]}}

    monkeypatch.setattr(fpd, "_run_smartctl", fake)
    data, used = fpd._read_smart(_target(), use_sudo=False)
    assert calls == [None, "sat"] and used == "sat" and data["smart_status"]["passed"]


def test_kind_labels():
    assert _target().kind == "USB hard drive"
    assert _target(transport="nvme", device="/dev/nvme0n1", rotational=False).kind == "NVMe SSD"
    assert _target(transport="sata", rotational=False).kind == "SSD or flash drive"
    assert _target(transport="mmc", device="/dev/mmcblk0").kind == "SD card"


def test_description_does_not_repeat_model_as_name():
    t = _target(name="WD_BLACK SN850X", model="WD_BLACK SN850X", transport="nvme", size_bytes=0)
    assert t.description() == "WD_BLACK SN850X (NVMe SSD)"


def test_read_every_file_reports_unreadable(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "good.jpg").write_bytes(b"x" * 100_000)
    (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
    bad = tmp_path / "a" / "bad.jpg"
    bad.write_bytes(b"y" * 10)
    os.chmod(bad, 0)
    try:
        if os.access(bad, os.R_OK):
            pytest.skip("running as root; can't make an unreadable file")
        report_path = tmp_path / "out" / "unreadable.txt"
        files, done, errors, finished = fpd.read_every_file(str(tmp_path), report_path)
    finally:
        os.chmod(bad, 0o644)
    assert finished and files == 2 and done == 100_004
    assert [os.path.basename(p) for p, _ in errors] == ["bad.jpg"]
    assert "bad.jpg" in report_path.read_text(encoding="utf-8")


def test_health_targets_for_directory(monkeypatch, tmp_path):
    a = _target(name="A", mounts=[str(tmp_path)])
    b = _target(name="B", mounts=["/somewhere/else"])
    monkeypatch.setattr(fpd, "detect_health_targets", lambda: [a, b])
    monkeypatch.setattr(fpd, "_mount_root", lambda path: str(tmp_path))
    assert fpd.health_targets_for(str(tmp_path / "sub")) == [a]
    assert fpd.health_targets_for(None) == [a, b]


def test_install_advice_names_package_manager(monkeypatch):
    monkeypatch.setattr(fpd.platform, "system", lambda: "Linux")
    monkeypatch.setattr(fpd, "_is_wsl", lambda: False)
    monkeypatch.setattr(fpd.shutil, "which", lambda tool: "/usr/bin/pacman" if tool == "pacman" else None)
    advice = " ".join(fpd.health_tool_advice())
    assert "sudo pacman -S smartmontools" in advice


# ---------------------------------------------------------------------------
# Health log
# ---------------------------------------------------------------------------


def _report_with(metrics, verdict=fpd.HEALTH_GOOD, serial="WX123", name="Sierra Club"):
    report = fpd.HealthReport(_target(name=name, model="WDC WD50NDZW"))
    report.verdict = verdict
    report.serial = serial
    report.metrics = dict(metrics)
    report.smart_available = True
    return report


def test_health_log_appends_rows_with_header(tmp_path):
    from datetime import datetime
    fpd.append_health_log(_report_with({"power_on_hours": 100, "pending_sectors": 0}),
                          list_dir=tmp_path, when=datetime(2026, 1, 2, 3, 4))
    fpd.append_health_log(_report_with({"power_on_hours": 900, "pending_sectors": 8}, fpd.HEALTH_WARNING),
                          list_dir=tmp_path, notes="8 sector(s) are unreadable")
    lines = (tmp_path / fpd.HEALTH_LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == fpd._HEALTH_LOG_COLUMNS
    assert len(lines) == 3
    rows = fpd.read_health_log(tmp_path)
    assert rows[0]["date"] == "2026-01-02 03:04" and rows[0]["health"] == "GOOD"
    assert rows[0]["notes"] == "No warning signs"
    assert rows[1]["pending_sectors"] == "8" and rows[1]["serial"] == "WX123"


def test_unknown_results_are_not_logged(tmp_path):
    report = fpd.HealthReport(_target())
    report.problem = "needs administrator access"
    assert fpd.append_health_log(report, list_dir=tmp_path) is None
    assert not (tmp_path / fpd.HEALTH_LOG_NAME).exists()


def test_health_changes_reports_increases_only():
    before = {"health": "GOOD", "pending_sectors": "0", "reallocated_sectors": "4",
              "ssd_wear_percent": "10", "connection_errors": "3"}
    after = {"health": "WARNING", "pending_sectors": "8", "reallocated_sectors": "4",
             "ssd_wear_percent": "11", "connection_errors": ""}
    changes = fpd.health_changes(before, after)
    assert "Unreadable sectors waiting to be replaced rose from 0 to 8" in changes
    assert "Health changed from GOOD to WARNING" in changes
    assert not any("wear" in c.lower() or "Replaced" in c or "connection" in c for c in changes)


def test_log_and_compare_prints_changes(tmp_path, capsys):
    fpd.log_and_compare(_report_with({"pending_sectors": 0}), list_dir=tmp_path)
    assert "First check of this drive" in capsys.readouterr().out
    fpd.log_and_compare(_report_with({"pending_sectors": 0}), list_dir=tmp_path)
    assert "No change since the last check" in capsys.readouterr().out
    fpd.log_and_compare(_report_with({"pending_sectors": 3}, fpd.HEALTH_WARNING), list_dir=tmp_path)
    out = capsys.readouterr().out
    assert "rose from 0 to 3" in out and "GOOD to WARNING" in out


def test_drives_matched_by_serial_even_if_renamed(tmp_path, capsys):
    fpd.log_and_compare(_report_with({"pending_sectors": 0}, name="Old Label"), list_dir=tmp_path)
    fpd.log_and_compare(_report_with({"pending_sectors": 2}, name="New Label"), list_dir=tmp_path)
    assert "rose from 0 to 2" in capsys.readouterr().out


def test_history_and_unconnected_drives(tmp_path, capsys):
    fpd.append_health_log(_report_with({"power_on_hours": 10, "pending_sectors": 0}), list_dir=tmp_path)
    fpd.append_health_log(_report_with({"power_on_hours": 20, "pending_sectors": 5}, fpd.HEALTH_WARNING),
                          list_dir=tmp_path, notes="5 sector(s) are unreadable")
    fpd.append_health_log(_report_with({"ssd_wear_percent": 3}, serial="NV1", name="Internal SSD"),
                          list_dir=tmp_path)
    fpd.print_health_history(tmp_path)
    out = capsys.readouterr().out
    assert "Sierra Club (WDC WD50NDZW, serial WX123)" in out
    assert "5 pending" in out and "rose from 0 to 5" in out
    assert "Internal SSD" in out and "3 % worn" in out
    fpd.print_unconnected_health([_target(name="Internal SSD")], tmp_path)
    out = capsys.readouterr().out
    assert "Sierra Club" in out and "WARNING" in out and "Internal SSD" not in out


def test_interpret_smart_fills_metrics_and_serial():
    data = _ata([_attr(5, "Reallocated_Sector_Ct", 2), _attr(197, "Current_Pending_Sector", 0),
                 _attr(199, "UDMA_CRC_Error_Count", 7)])
    data["serial_number"] = "WX1234"
    report = fpd.interpret_smart(_target(), data)
    assert report.serial == "WX1234"
    assert report.metrics["power_on_hours"] == 17532 and report.metrics["temperature_c"] == 34
    assert report.metrics["reallocated_sectors"] == 2 and report.metrics["pending_sectors"] == 0
    assert report.metrics["connection_errors"] == 7
