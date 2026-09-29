"""Tests for interactive-mode helpers: drive identity, list matching, naming, import."""

import os
from pathlib import Path

import findphotodates as fpd


def _write_inventory(path, root, rows, extra_headers=()):
    """Write a minimal TSV inventory; rows are (relative_path, size)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# inventory_root={root}\n")
        for header in extra_headers:
            f.write(f"# {header}\n")
        f.write("# hash_mode=off\n")
        f.write(fpd.TSV_HEADER + "\n")
        f.write("\t".join(fpd.TSV_COLUMNS) + "\n")
        for rel, size in rows:
            f.write(f"{root.rstrip('/')}/{rel}\t\t{size}\t1\t\t\t\t\n")


def _make_drive_tree(base, count=30):
    """Create files on a fake drive and return [(relative_path, size)]."""
    rows = []
    for i in range(count):
        rel = f"photos/{i:03d}/IMG_{i:04d}.JPG"
        full = Path(base) / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"x" * (100 + i))
        rows.append((rel, 100 + i))
    return rows


# ---------------------------------------------------------------------------
# Volume IDs and names
# ---------------------------------------------------------------------------


def test_normalize_volume_id_forms():
    assert fpd._normalize_volume_id("5f61-ddf5") == "5F61-DDF5"
    assert fpd._normalize_volume_id("5F61DDF5") == "5F61-DDF5"
    # Linux shows NTFS serials as 16 hex digits; Windows shows the low 32 bits.
    assert fpd._normalize_volume_id("01D2A3B4ECEFE97E") == "ECEF-E97E"
    uuid = "3f2a9c1b-0000-4000-8000-123456789abc"
    assert fpd._normalize_volume_id(uuid) == uuid.upper()
    assert fpd._normalize_volume_id(None) == ""


def test_display_name_and_canonical_list_name():
    assert fpd.DriveInfo("/mnt/c").display_name == "Drive C"
    assert fpd.DriveInfo("C:\\").display_name == "Drive C"
    assert fpd.DriveInfo("/run/media/u/Sierra Club", "Sierra Club").display_name == "Sierra Club"
    drive = fpd.DriveInfo("/run/media/u/X", "Photos: 2024?", "5f61ddf5")
    assert fpd.canonical_list_name(drive) == "Photos_ 2024_ (5F61-DDF5).tsv"
    long_id = fpd.DriveInfo("/Volumes/B", "Backup", "3F2A9C1B-0000-4000-8000-123456789ABC")
    assert fpd.canonical_list_name(long_id) == "Backup (3F2A9C1B).tsv"


def test_legacy_list_name_from_root(tmp_path):
    for root, expected in (("/mnt/f", "Drive F.tsv"), ("J:\\", "Drive J.tsv"),
                           ("/run/media/u/Card", "Card.tsv")):
        p = tmp_path / "x.tsv"
        _write_inventory(p, root, [])
        fl = fpd.FileList(p, fpd.read_inventory_header(p)[0])
        assert fpd._legacy_list_name(fl) == expected


def test_read_inventory_header(tmp_path):
    p = tmp_path / "a.tsv"
    _write_inventory(p, "/mnt/l", [("a.jpg", 5)], ["volume_label=Sierra Club", "volume_ids=5F61-DDF5"])
    header, is_inventory = fpd.read_inventory_header(p)
    assert is_inventory
    assert header["inventory_root"] == "/mnt/l"
    assert header["volume_ids"] == "5F61-DDF5"
    other = tmp_path / "notes.tsv"
    other.write_text("name\tvalue\n", encoding="utf-8")
    assert fpd.read_inventory_header(other)[1] is False


# ---------------------------------------------------------------------------
# Matching lists to drives
# ---------------------------------------------------------------------------


def test_match_by_volume_id_even_when_path_changed(tmp_path):
    p = tmp_path / "a.tsv"
    _write_inventory(p, "/mnt/l", [], ["volume_ids=5F61-DDF5"])
    fl = fpd.FileList(p, fpd.read_inventory_header(p)[0])
    other = fpd.DriveInfo(str(tmp_path / "other"), "Other", "1111-2222")
    target = fpd.DriveInfo(str(tmp_path / "moved"), "Sierra Club", "5f61ddf5")
    fpd.match_lists_to_drives([fl], [other, target])
    assert fl.drive is target and fl.matched_by == "drive ID"


def test_match_by_unchanged_location(tmp_path):
    mount = tmp_path / "mnt_c"
    mount.mkdir()
    p = tmp_path / "c.tsv"
    _write_inventory(p, str(mount), [])
    fl = fpd.FileList(p, fpd.read_inventory_header(p)[0])
    drive = fpd.DriveInfo(str(mount), "", "ABCD-0001")
    fpd.match_lists_to_drives([fl], [drive])
    assert fl.drive is drive and fl.matched_by == "location"


def test_match_by_contents_when_drive_moved(tmp_path):
    real = tmp_path / "Sierra Club"
    decoy = tmp_path / "Card"
    rows = _make_drive_tree(real)
    _make_drive_tree(decoy, count=3)
    p = tmp_path / "l.tsv"
    _write_inventory(p, "/mnt/l", rows)  # recorded under a mount that no longer exists
    fl = fpd.FileList(p, fpd.read_inventory_header(p)[0])
    decoy_drive = fpd.DriveInfo(str(decoy), "Card", "0000-0001")
    real_drive = fpd.DriveInfo(str(real), "Sierra Club", "5F61-DDF5")
    fpd.match_lists_to_drives([fl], [decoy_drive, real_drive])
    assert fl.drive is real_drive and fl.matched_by == "contents"


def test_no_match_for_unrelated_drive(tmp_path):
    rows = _make_drive_tree(tmp_path / "a")
    other = tmp_path / "b"
    other.mkdir()
    p = tmp_path / "a.tsv"
    _write_inventory(p, "/mnt/a", rows)
    fl = fpd.FileList(p, fpd.read_inventory_header(p)[0])
    fpd.match_lists_to_drives([fl], [fpd.DriveInfo(str(other), "B", "0000-0002")])
    assert fl.drive is None


def test_one_drive_gets_only_one_list(tmp_path):
    drive_dir = tmp_path / "d"
    rows = _make_drive_tree(drive_dir)
    lists = []
    for name in ("x.tsv", "y.tsv"):
        p = tmp_path / name
        _write_inventory(p, f"/mnt/{name[0]}", rows)
        lists.append(fpd.FileList(p, fpd.read_inventory_header(p)[0]))
    fpd.match_lists_to_drives(lists, [fpd.DriveInfo(str(drive_dir), "D", "0000-0003")])
    assert sum(fl.drive is not None for fl in lists) == 1


# ---------------------------------------------------------------------------
# Volume header lines
# ---------------------------------------------------------------------------


def test_volume_headers_only_for_whole_drives(tmp_path):
    assert fpd._volume_header_lines(str(tmp_path / "none.tsv"), str(tmp_path)) == []


def test_volume_headers_merge_ids_for_same_drive(tmp_path):
    p = tmp_path / "a.tsv"
    _write_inventory(p, "/Volumes/Sierra Club", [], ["volume_label=Sierra Club", "volume_ids=AAAA-BBBB"])
    drive = fpd.DriveInfo("/run/media/u/Sierra Club", "Sierra Club", "5F61-DDF5")
    lines = fpd._volume_header_lines(str(p), drive.mount, drive)
    assert lines == ["volume_label=Sierra Club", "volume_ids=5F61-DDF5,AAAA-BBBB"]


def test_volume_headers_replace_ids_for_different_drive(tmp_path):
    p = tmp_path / "a.tsv"
    _write_inventory(p, "/mnt/x", [], ["volume_label=Old", "volume_ids=AAAA-BBBB"])
    drive = fpd.DriveInfo("/run/media/u/New", "New", "1234-5678")
    assert fpd._volume_header_lines(str(p), drive.mount, drive)[1] == "volume_ids=1234-5678"


def test_writer_includes_extra_headers(tmp_path):
    out = tmp_path / "out.tsv"
    row = (str(tmp_path / "a.jpg"), "", 1, 1, "", "", "", "")
    fpd.write_dates_to_file_atomic(str(out), [row], inventory_root=str(tmp_path),
                                   extra_headers=["volume_label=Card", "volume_ids=1234-5678"])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[1:3] == ["# volume_label=Card", "# volume_ids=1234-5678"]
    header, is_inventory = fpd.read_inventory_header(out)
    assert is_inventory and header["volume_label"] == "Card"


# ---------------------------------------------------------------------------
# Import, row counts, path style
# ---------------------------------------------------------------------------


def test_import_legacy_lists_names_and_moves(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    drive_dir = tmp_path / "Sierra Club"
    rows = _make_drive_tree(drive_dir)
    _write_inventory(home / "l:photo.taken.dates.txt", "/mnt/l", rows)
    _write_inventory(home / "f:photo.taken.dates.txt", "/mnt/f", [("a.jpg", 1)])
    (home / "unrelated.txt").write_text("hello\n", encoding="utf-8")
    found = fpd.find_legacy_lists(home)
    assert sorted(p.name for p in found) == ["f:photo.taken.dates.txt", "l:photo.taken.dates.txt"]
    dest = tmp_path / "Documents" / "findphotodates"
    drive = fpd.DriveInfo(str(drive_dir), "Sierra Club", "5F61-DDF5")
    moved = dict((old.name, new.name) for old, new in fpd.import_file_lists(found, dest, [drive]))
    assert moved == {"l:photo.taken.dates.txt": "Sierra Club (5F61-DDF5).tsv",
                     "f:photo.taken.dates.txt": "Drive F.tsv"}
    assert not (home / "l:photo.taken.dates.txt").exists()
    assert (home / "unrelated.txt").exists()
    assert [fl.path.name for fl in fpd.find_file_lists(dest)] == ["Drive F.tsv", "Sierra Club (5F61-DDF5).tsv"]


def test_import_never_overwrites(tmp_path):
    dest = tmp_path / "lists"
    dest.mkdir()
    (dest / "Drive F.tsv").write_text("existing\n", encoding="utf-8")
    src = tmp_path / "f:photo.taken.dates.txt"
    _write_inventory(src, "/mnt/f", [])
    [(_, new)] = fpd.import_file_lists([src], dest, [])
    assert new.name == "Drive F (2).tsv"
    assert (dest / "Drive F.tsv").read_text(encoding="utf-8") == "existing\n"


def test_count_rows(tmp_path):
    p = tmp_path / "a.tsv"
    _write_inventory(p, "/mnt/a", [("a", 1), ("b", 2), ("c", 3)], ["volume_ids=1234-5678"])
    assert fpd._count_rows(p) == 3


def test_windows_style_list_not_kept_for_posix_mount(tmp_path):
    p = tmp_path / "j.tsv"
    _write_inventory(p, "J:\\", [])
    with open(p, "a", encoding="utf-8") as f:
        f.write("J:\\a.jpg\t\t1\t1\t\t\t\t\n")
    assert fpd.resolve_output_path_style("auto", str(p), "/run/media/u/Drive") == "linux"
    assert fpd.resolve_output_path_style("auto", str(p), "/mnt/j") == "windows"


def test_default_inventory_dir_honours_config(tmp_path, monkeypatch):
    monkeypatch.setattr(fpd, "load_config", lambda: {"inventory_dir": str(tmp_path / "lists")})
    assert fpd.default_inventory_dir() == tmp_path / "lists"
    monkeypatch.setattr(fpd, "load_config", lambda: {"saved_scans": []})
    assert fpd.default_inventory_dir().name == "findphotodates"


def test_command_line_reflects_settings():
    s = fpd._InteractiveSettings()
    s.only_media, s.hash_mode, s.workers = True, "sample", 2
    cmd = s.command_line("/run/media/u/Sierra Club", "/home/u/Documents/findphotodates/S.tsv")
    assert "--only-media" in cmd and "--hash sample" in cmd and "--workers 2" in cmd
    assert "'/run/media/u/Sierra Club'" in cmd or '"/run/media/u/Sierra Club"' in cmd


# ---------------------------------------------------------------------------
# Change report after a rescan
# ---------------------------------------------------------------------------


def _scan(directory, output):
    assert fpd.run_scan(str(directory), str(output), None, quiet=False) is not False


def test_change_report_after_rescan(tmp_path, capsys):
    d = tmp_path / "d"
    (d / "a").mkdir(parents=True)
    for name in ("keep.txt", "gone.txt", "move_me.txt"):
        (d / "a" / name).write_text(name, encoding="utf-8")
    out = tmp_path / "list.tsv"
    _scan(d, out)
    assert "This is a new file list" in capsys.readouterr().out

    (d / "a" / "gone.txt").unlink()
    (d / "a" / "keep.txt").write_text("different size now", encoding="utf-8")
    (d / "b").mkdir()
    os.replace(d / "a" / "move_me.txt", d / "b" / "moved.txt")
    (d / "b" / "new.txt").write_text("new", encoding="utf-8")
    _scan(d, out)
    report = capsys.readouterr().out.split("Changes since the last scan:", 1)[1]
    assert "New files: 1\n  b/new.txt" in report
    assert "Changed files (different size or date): 1\n  a/keep.txt" in report
    assert "Moved or renamed: 1\n  a/move_me.txt  ->  b/moved.txt" in report
    assert "Removed (no longer found): 1\n  a/gone.txt" in report

    _scan(d, out)
    assert "Nothing new, changed or removed." in capsys.readouterr().out


def test_change_report_groups_many_files_by_folder(capsys):
    root = "/drive"
    new = [(f"/drive/big/f{i}.jpg", 10 + i, 1) for i in range(40)] + [("/drive/x.jpg", 5, 1)]
    fpd.print_change_report(root, new, [], [])
    out = capsys.readouterr().out
    assert "New files: 41" in out
    assert "      40  big" in out
    assert "       1  (top folder)" in out


def test_pair_moved_files_ignores_empty_files():
    new = [("/d/new_empty", 0, 5), ("/d/new_real", 7, 5)]
    removed = [("/d/old_empty", 0, 5), ("/d/old_real", 7, 5)]
    moved, still_new, still_removed = fpd._pair_moved_files(new, removed)
    assert moved == [("/d/old_real", "/d/new_real")]
    assert still_new == ["/d/new_empty"]
    assert still_removed == ["/d/old_empty"]


# ---------------------------------------------------------------------------
# Choosing several drives in the main menu
# ---------------------------------------------------------------------------


def test_parse_drive_choices():
    assert fpd.parse_drive_choices("3", 5) == [3]
    assert fpd.parse_drive_choices("3 4", 5) == [3, 4]
    assert fpd.parse_drive_choices("4,3", 5) == [4, 3]
    assert fpd.parse_drive_choices("2-4 1", 5) == [2, 3, 4, 1]
    assert fpd.parse_drive_choices("3 3", 5) == [3]
    for bad in ("", "0", "6", "3 x", "4-2", "u", "a", "q", "1-9"):
        assert fpd.parse_drive_choices(bad, 5) == [], bad


def test_update_several_drives_in_a_row(tmp_path, capsys):
    list_dir = tmp_path / "lists"
    drives = []
    for name, vid in (("Card A", "AAAA-0001"), ("Card B", "BBBB-0002")):
        mount = tmp_path / name
        mount.mkdir()
        (mount / "photo.txt").write_text(name, encoding="utf-8")
        drives.append(fpd.DriveInfo(str(mount), name, vid))
    state = {"list_dir": list_dir, "dirty": False}
    fpd._update_drives(fpd._InteractiveSettings(), drives, {}, state)
    out = capsys.readouterr().out
    assert sorted(p.name for p in list_dir.iterdir()) == ["Card A (AAAA-0001).tsv", "Card B (BBBB-0002).tsv"]
    assert "Card A                   done" in out and "Card B                   done" in out
    assert state["dirty"] is True


# ---------------------------------------------------------------------------
# Reusing EXIF results for moved or copied photos
# ---------------------------------------------------------------------------


def _list_with_photo(tmp_path, rel, date):
    """A drive folder with one fake photo, and a list that already knows its date."""
    drive = tmp_path / "drive"
    photo = drive / rel
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"\xff\xd8" + b"x" * 200_000)  # not real EXIF: ExifTool finds no date
    st = photo.stat()
    out = tmp_path / "list.tsv"
    row = (str(photo), date, st.st_size, st.st_mtime_ns, "37.9", "-122.6", "", "")
    fpd.write_dates_to_file_atomic(str(out), [row], inventory_root=str(drive))
    return drive, photo, out


def _rows(out):
    header, _ = fpd.read_inventory_header(out)
    lines = [l.split("\t") for l in out.read_text(encoding="utf-8").splitlines()
             if not l.startswith("#") and not l.startswith("filepath")]
    return {os.path.relpath(l[0], header["inventory_root"]): l for l in lines}


def test_moved_photo_keeps_its_date_without_exiftool(tmp_path, capsys):
    drive, photo, out = _list_with_photo(tmp_path, "old/P1010001.JPG", "2021:01:24 11:11:30")
    (drive / "new").mkdir()
    os.replace(photo, drive / "new" / "P1010001.JPG")  # a move keeps size and mtime
    assert fpd.run_scan(str(drive), str(out), None, quiet=False) is not False
    rows = _rows(out)
    assert rows[os.path.join("new", "P1010001.JPG")][1:2] == ["2021:01:24 11:11:30"]
    assert rows[os.path.join("new", "P1010001.JPG")][4:6] == ["37.9", "-122.6"]
    printed = capsys.readouterr().out
    assert "1 moved or copied photos/videos reused their earlier dates" in printed
    assert "Moved or renamed: 1\n  old/P1010001.JPG  ->  new/P1010001.JPG" in printed


def test_copied_photo_reuses_date_too(tmp_path):
    drive, photo, out = _list_with_photo(tmp_path, "a/IMG_1.JPG", "2020:05:01 10:00:00")
    copy = drive / "backup" / "IMG_1.JPG"
    copy.parent.mkdir()
    copy.write_bytes(photo.read_bytes())
    os.utime(copy, ns=(photo.stat().st_atime_ns, photo.stat().st_mtime_ns))  # like cp -p
    assert fpd.run_scan(str(drive), str(out), None, quiet=True) is not False
    rows = _rows(out)
    assert rows[os.path.join("a", "IMG_1.JPG")][1] == "2020:05:01 10:00:00"
    assert rows[os.path.join("backup", "IMG_1.JPG")][1] == "2020:05:01 10:00:00"


def test_renamed_or_modified_photo_is_read_again(tmp_path):
    drive, photo, out = _list_with_photo(tmp_path, "a/IMG_2.JPG", "2020:05:01 10:00:00")
    os.replace(photo, drive / "a" / "renamed.JPG")  # different name: no reuse
    assert fpd.run_scan(str(drive), str(out), None, quiet=True) is not False
    assert _rows(out)[os.path.join("a", "renamed.JPG")][1] == ""


# ---------------------------------------------------------------------------
# A drive holding copies of another drive's folders must not take its list
# ---------------------------------------------------------------------------


def test_copy_drive_does_not_take_another_drives_list(tmp_path):
    copy_drive = tmp_path / "Erowid"
    rows = _make_drive_tree(copy_drive)  # same folders as the Backblaze drive
    p = tmp_path / "Backblaze (6498-2A47).tsv"
    _write_inventory(p, "/run/media/u/Backblaze", rows, ["volume_ids=6498-2A47"])
    fl = fpd.FileList(p, fpd.read_inventory_header(p)[0])
    fpd.match_lists_to_drives([fl], [fpd.DriveInfo(str(copy_drive), "Erowid", "5D85-2968")])
    assert fl.drive is None


def test_content_match_still_allowed_across_id_formats(tmp_path):
    # e.g. a list made on Linux (exFAT serial) read on a Mac (volume UUID)
    drive_dir = tmp_path / "Sierra Club"
    rows = _make_drive_tree(drive_dir)
    p = tmp_path / "Sierra Club (5F61-DDF5).tsv"
    _write_inventory(p, "/run/media/u/Sierra Club", rows, ["volume_ids=5F61-DDF5"])
    fl = fpd.FileList(p, fpd.read_inventory_header(p)[0])
    mac = fpd.DriveInfo(str(drive_dir), "Sierra Club", "3F2A9C1B-0000-4000-8000-123456789ABC")
    fpd.match_lists_to_drives([fl], [mac])
    assert fl.drive is mac and fl.matched_by == "contents"


def test_list_belongs_to_other_drive():
    erowid = fpd.DriveInfo("/x", "Erowid", "5D85-2968")
    assert fpd.list_belongs_to_other_drive(["6498-2A47"], erowid)
    assert not fpd.list_belongs_to_other_drive(["5D85-2968"], erowid)
    assert not fpd.list_belongs_to_other_drive([], erowid)
    assert not fpd.list_belongs_to_other_drive(["6498-2A47"], fpd.DriveInfo("/y", "NoID", ""))
    assert not fpd.list_belongs_to_other_drive(["3F2A9C1B-0000-4000-8000-123456789ABC"], erowid)


def test_scan_refuses_to_overwrite_another_drives_list(tmp_path, capsys):
    drive_dir = tmp_path / "Erowid"
    _make_drive_tree(drive_dir, count=2)
    p = tmp_path / "Backblaze (6498-2A47).tsv"
    _write_inventory(p, "/run/media/u/Backblaze", [("x.jpg", 1)],
                     ["volume_label=Backblaze", "volume_ids=6498-2A47"])
    before = p.read_bytes()
    drive = fpd.DriveInfo(str(drive_dir), "Erowid", "5D85-2968")
    assert fpd._run_interactive_scan(fpd._InteractiveSettings(), str(drive_dir), p, drive=drive) is False
    assert p.read_bytes() == before
    assert "is the file list for Backblaze" in capsys.readouterr().out


def test_one_row_list_does_not_match_by_contents(tmp_path):
    # Sampling a tiny list lands on the same row again and again; one matching
    # file must not count as several.
    drive_dir = tmp_path / "drive"
    rows = _make_drive_tree(drive_dir, count=1)
    p = tmp_path / "tiny.tsv"
    _write_inventory(p, "/mnt/z", rows)
    assert len(fpd._sample_inventory_rows(p)) == 1
    fl = fpd.FileList(p, fpd.read_inventory_header(p)[0])
    fpd.match_lists_to_drives([fl], [fpd.DriveInfo(str(drive_dir), "Z", "0000-0009")])
    assert fl.drive is None


def test_unused_path_skips_paths_already_assigned(tmp_path):
    first = fpd._unused_path(tmp_path / "Card.tsv")
    second = fpd._unused_path(tmp_path / "Card.tsv", {first})
    assert first == tmp_path / "Card.tsv" and second == tmp_path / "Card (2).tsv"
