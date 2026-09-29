"""The desktop helper must preserve the interactive scan's drive metadata."""

from unittest.mock import patch

import findphotodates as fp
import findphotodates_gui_worker as worker


def test_scan_passes_advanced_settings_and_drive_identity(tmp_path):
    source = tmp_path / "camera"
    source.mkdir()
    output = tmp_path / "lists" / "camera.tsv"
    drive = fp.DriveInfo(str(source), label="Camera", volume_id="ABCD-1234")
    seen = {}

    def fake_scan(settings, directory, destination, drive=None):
        seen.update(settings=settings, directory=directory, destination=destination, drive=drive)
        output.parent.mkdir()
        output.write_text("test", encoding="utf-8")
        return True

    with patch.object(fp, "identify_drive", return_value=drive), patch.object(
        fp, "_run_interactive_scan", side_effect=fake_scan
    ):
        result = worker.main([
            "scan", "--directory", str(source), "--output", str(output), "--whole-drive",
            "--list-dir", str(output.parent), "--only-media", "--hash", "sample",
            "--locate", "--retry-blank-exif", "--workers", "8",
            "--min-image-size", "0", "--path-style", "windows",
        ])

    assert result == 0
    assert seen["drive"] is drive
    assert seen["directory"] == str(source)
    assert seen["destination"] == str(output)
    settings = seen["settings"]
    assert (settings.only_media, settings.hash_mode, settings.locate,
            settings.retry_blank_exif, settings.workers,
            settings.min_image_size, settings.path_style) == (
                True, "sample", True, True, 8, 0, "windows")


def test_failed_scan_does_not_rename_a_list(tmp_path):
    output = tmp_path / "Old.tsv"
    output.write_text("original", encoding="utf-8")
    drive = fp.DriveInfo(str(tmp_path), label="Camera", volume_id="ABCD-1234")
    with patch.object(fp, "identify_drive", return_value=drive), patch.object(
        fp, "_run_interactive_scan", return_value=False
    ):
        result = worker.main(["scan", "--directory", str(tmp_path), "--whole-drive",
                              "--output", str(output), "--list-dir", str(tmp_path)])
    assert result == 1
    assert output.read_text(encoding="utf-8") == "original"


def test_folder_scan_keeps_its_custom_name_even_at_drive_root(tmp_path):
    output = tmp_path / "My Folder.tsv"
    seen = {}

    def fake_scan(settings, directory, destination, drive=None):
        seen["drive"] = drive
        output.write_text("listed", encoding="utf-8")
        return True

    with patch.object(fp, "identify_drive") as identify, patch.object(
        fp, "_run_interactive_scan", side_effect=fake_scan
    ):
        result = worker.main(["scan", "--directory", str(tmp_path),
                              "--output", str(output), "--list-dir", str(tmp_path)])
    assert result == 0
    assert seen["drive"] is None
    identify.assert_not_called()
    assert output.read_text(encoding="utf-8") == "listed"
