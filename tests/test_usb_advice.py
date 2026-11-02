import os

import pytest

import findphotodates as fpd

pytestmark = pytest.mark.skipif(os.name == "nt", reason="uses symlinks like Linux sysfs")


def _fake_sys(tmp_path, driver="uas", speed="5000", version=" 3.20", quirks=""):
    """A minimal /sys for /dev/sdb on USB port 6-1."""
    root = tmp_path / "sys"
    usb = root / "devices/pci0000:00/usb6/6-1"
    interface = usb / "6-1:1.0"
    scsi = interface / "host0/target0:0:0/0:0:0:0"
    scsi.mkdir(parents=True)
    (usb / "idVendor").write_text("1058\n")
    (usb / "idProduct").write_text("2627\n")
    (usb / "speed").write_text(speed + "\n")
    (usb / "version").write_text(version + "\n")
    (interface / "bInterfaceNumber").write_text("00\n")
    drivers = root / "bus/usb/drivers"
    (drivers / driver).mkdir(parents=True)
    os.symlink(drivers / driver, interface / "driver")
    (root / "block/sdb").mkdir(parents=True)
    os.symlink(scsi, root / "block/sdb/device")
    params = root / "module/usb_storage/parameters"
    params.mkdir(parents=True)
    (params / "quirks").write_text(quirks + "\n")
    return str(root)


UAS_LOG = """\
sd 0:0:0:0: [sdb] tag#3 uas_eh_abort_handler 0 uas-tag 4 inflight: CMD IN
scsi host0: uas_eh_device_reset_handler start
usb 6-1: reset SuperSpeed USB device number 3 using xhci_hcd
sd 0:0:0:0: [sda] tag#1 uas_eh_abort_handler 0 uas-tag 1 inflight: CMD IN
"""


def test_uas_errors_suggest_the_quirk(tmp_path):
    findings = fpd.usb_connection_findings("/dev/sdb", _fake_sys(tmp_path), UAS_LOG)
    assert len(findings) == 1
    level, headline, explanation = findings[0]
    assert level == fpd.HEALTH_WARNING
    assert "1 UAS command error(s), 1 connection reset(s)" in headline
    assert "echo 1058:2627:u | sudo tee /sys/module/usb_storage/parameters/quirks" in explanation
    assert "options usb-storage quirks=1058:2627:u" in explanation


def test_quirk_keeps_existing_entries(tmp_path):
    root = _fake_sys(tmp_path, quirks="0bc2:2322:u")
    _, _, explanation = fpd.usb_connection_findings("/dev/sdb", root, UAS_LOG)[0]
    assert "echo 0bc2:2322:u,1058:2627:u |" in explanation


def test_quirk_already_set_is_not_duplicated_or_used_to_drop_others(tmp_path):
    root = _fake_sys(tmp_path, quirks="0bc2:2322:u,1058:2627:u")
    _, _, explanation = fpd.usb_connection_findings("/dev/sdb", root, UAS_LOG)[0]
    assert "echo 0bc2:2322:u,1058:2627:u |" in explanation


def test_quirk_is_merged_into_existing_flags_for_the_same_device(tmp_path):
    root = _fake_sys(tmp_path, quirks="0bc2:2322:u,1058:2627:m")
    _, _, explanation = fpd.usb_connection_findings("/dev/sdb", root, UAS_LOG)[0]
    assert "echo 0bc2:2322:u,1058:2627:mu |" in explanation


def test_usb3_drive_enumerated_at_usb2_is_found_from_bos(tmp_path):
    root = _fake_sys(tmp_path, driver="usb-storage", speed="480", version=" 2.10")
    usb = tmp_path / "sys/devices/pci0000:00/usb6/6-1"
    (usb / "bos_descriptors").write_bytes(bytes.fromhex(
        "050f1600020710020e0f00000a1003000e00010a2000"))
    findings = fpd.usb_connection_findings("/dev/sdb", root, "")
    assert [f[0] for f in findings] == [fpd.HEALTH_NOTE]
    (usb / "bos_descriptors").write_bytes(bytes.fromhex("050f0c000107100206000000"))
    assert fpd.usb_connection_findings("/dev/sdb", root, "") == []


def test_no_advice_without_errors_or_with_usb_storage(tmp_path):
    assert fpd.usb_connection_findings("/dev/sdb", _fake_sys(tmp_path), "") == []
    other = tmp_path / "other"
    other.mkdir()
    assert fpd.usb_connection_findings("/dev/sdb", _fake_sys(other, driver="usb-storage"),
                                       UAS_LOG) == []


def test_usb3_drive_at_usb2_speed(tmp_path):
    findings = fpd.usb_connection_findings(
        "/dev/sdb", _fake_sys(tmp_path, driver="usb-storage", speed="480"), "")
    assert [f[0] for f in findings] == [fpd.HEALTH_NOTE]
    assert "USB 2 speed" in findings[0][1]
    slow = tmp_path / "usb2"
    slow.mkdir()
    assert fpd.usb_connection_findings(
        "/dev/sdb", _fake_sys(slow, driver="usb-storage", speed="480", version=" 2.00"),
        "") == []


def test_unknown_device_gives_no_advice(tmp_path):
    assert fpd.usb_connection_findings("/dev/sdz", _fake_sys(tmp_path), UAS_LOG) == []
