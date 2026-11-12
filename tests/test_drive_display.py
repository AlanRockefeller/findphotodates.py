"""Drive summaries stay readable in the menu and GUI."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import findphotodates as fp


def test_last_updated_uses_today_and_local_time():
    now = datetime.now().replace(hour=16, minute=42, second=0, microsecond=0)
    assert fp._last_updated_label(now.timestamp(), now=now) == "Today 16:42"
    yesterday = now - timedelta(days=1)
    assert fp._last_updated_label(yesterday.timestamp(), now=now) == yesterday.strftime("%Y-%m-%d")


def test_menu_shows_a_wrapped_drive_table(capsys, tmp_path):
    inventory = tmp_path / "Camera list.tsv"
    inventory.write_text("test", encoding="utf-8")
    drive = fp.DriveInfo("/run/media/alan/Camera Card", label="Camera Card",
                         fstype="exfat", size_bytes=64_000_000_000)
    fp._print_drive_menu([drive], {drive.mount: SimpleNamespace(path=inventory)},
                         tmp_path, [inventory])
    output = capsys.readouterr().out
    assert "| #" in output and "| Drive" in output
    assert "| Filesystem" in output and "| Updated" in output
    assert "Location: /run/media/alan/Camera Card" in output
    assert "File list: Camera list.tsv" in output
    assert "Today " in output
    table_lines = [line for line in output.splitlines() if line.startswith(("|", "+"))]
    assert len({len(line) for line in table_lines}) == 1


def test_dirty_ntfs_drives_are_found_from_the_kernel_log():
    import findphotodates_drives as drives

    log = "\n".join([
        " sdb: sdb1 sdb2",
        'ntfs3(sdb2): volume is dirty and "force" flag is not set!',
        " sda: sda1 sda2",
        'ntfs3(sda2): volume is dirty and "force" flag is not set!',
        " sdb: sdb1",  # another disk plugged in as sdb: start over for it
    ])
    assert drives._dirty_ntfs_devices(log) == {"sda2"}


def test_unmounted_drives_skip_system_partitions_and_mark_dirty_ones(monkeypatch):
    import json
    import findphotodates_drives as drives

    lsblk = {"blockdevices": [
        {"name": "sda", "path": "/dev/sda", "type": "disk", "hotplug": True, "children": [
            {"name": "sda1", "path": "/dev/sda1", "type": "part", "hotplug": True,
             "fstype": "vfat", "label": "EFI", "parttypename": "EFI System", "size": 200},
            {"name": "sda2", "path": "/dev/sda2", "type": "part", "hotplug": True,
             "fstype": "ntfs", "label": "LaCie", "size": 4000, "mountpoint": None},
        ]},
        {"name": "sdc1", "path": "/dev/sdc1", "type": "part", "hotplug": True,
         "fstype": "exfat", "label": "Card", "size": 64, "mountpoint": "/run/media/a/Card"},
        {"name": "nvme0n1p3", "path": "/dev/nvme0n1p3", "type": "part", "hotplug": False,
         "fstype": "ntfs", "size": 888},
    ]}

    def run_quiet(cmd, timeout=30):
        if cmd[0] == "lsblk":
            return json.dumps(lsblk)
        return 'ntfs3(sda2): volume is dirty and "force" flag is not set!'

    monkeypatch.setattr(drives.platform, "system", lambda: "Linux")
    monkeypatch.setattr(drives, "_is_wsl", lambda: False)
    monkeypatch.setattr(drives.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(drives, "_run_quiet", run_quiet)
    assert drives.unmounted_drives() == [
        drives.UnmountedDrive("/dev/sda2", "LaCie", "ntfs", 4000, True)]
    assert drives.mount_command("/dev/sda2", read_only=True) == [
        "udisksctl", "mount", "-b", "/dev/sda2", "-o", "ro"]


def test_menu_explains_a_dirty_drive(capsys, monkeypatch):
    import findphotodates_drives as drives
    import findphotodates_menu as menu

    monkeypatch.setattr(drives.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(menu, "mount_command", drives.mount_command)

    fp._print_unmounted([drives.UnmountedDrive("/dev/sda2", "LaCie", "ntfs", 4 * 10**12, True)])
    out = capsys.readouterr().out
    assert "✗ LaCie" in out and "udisksctl mount -b /dev/sda2 -o ro" in out and "chkdsk" in out


def test_older_kernels_colon_dirty_message_is_recognized():
    import findphotodates_drives as drives

    log = " sdb: sdb1 sdb2\nntfs3: sdb2: volume is dirty and \"force\" flag is not set!"
    assert drives._dirty_ntfs_devices(log) == {"sdb2"}


def test_lsblk_without_parttypename_falls_back(monkeypatch):
    import json
    import findphotodates_drives as drives

    calls = []

    def run_quiet(cmd, timeout=30):
        calls.append(cmd)
        if cmd[0] == "lsblk" and "PARTTYPENAME" in cmd[-1]:
            return None  # util-linux older than 2.35
        return json.dumps({"blockdevices": [
            {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "hotplug": True,
             "fstype": "exfat", "label": "Card", "size": 64}]})

    monkeypatch.setattr(drives.platform, "system", lambda: "Linux")
    monkeypatch.setattr(drives, "_is_wsl", lambda: False)
    monkeypatch.setattr(drives.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(drives, "_run_quiet", run_quiet)
    assert [d.device for d in drives.unmounted_drives()] == ["/dev/sdb1"]
    assert len(calls) == 2


def test_menu_without_udisksctl_suggests_sudo_mount(capsys, monkeypatch):
    import findphotodates_drives as drives
    import findphotodates_menu as menu

    monkeypatch.setattr(menu, "mount_command", lambda device, read_only=False: None)
    fp._print_unmounted([drives.UnmountedDrive("/dev/sda2", "LaCie", "ntfs", 1, True)])
    assert "sudo mount -o ro /dev/sda2 /mnt/LaCie" in capsys.readouterr().out


def test_mount_commands_are_quoted_for_awkward_labels(capsys, monkeypatch):
    import shlex
    import findphotodates_drives as drives
    import findphotodates_menu as menu

    monkeypatch.setattr(menu, "mount_command", lambda device, read_only=False: None)
    fp._print_unmounted([drives.UnmountedDrive("/dev/sd b2", "John's $(rm) Drive", "ntfs", 1,
                                               True)])
    line = next(l.strip() for l in capsys.readouterr().out.splitlines() if "sudo mount" in l)
    assert line == "sudo mkdir -p /mnt/John_s_rm_Drive && sudo mount -o ro '/dev/sd b2' /mnt/John_s_rm_Drive"
    first, second = line.split(" && ")
    assert shlex.split(second)[-2:] == ["/dev/sd b2", "/mnt/John_s_rm_Drive"]

