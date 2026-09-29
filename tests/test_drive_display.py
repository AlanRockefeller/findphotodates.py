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
