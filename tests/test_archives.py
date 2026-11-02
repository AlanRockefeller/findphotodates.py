import csv
import io
import os
import tarfile
import zipfile

import findphotodates as fpd
import findphotodates_coverage as cov


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


def _scan(root, output, monkeypatch, archives=fpd.DEFAULT_ARCHIVE_MODE, extensions=None):
    monkeypatch.setattr(fpd, "ExifToolPersistent", NoExifTool)
    assert fpd.run_scan(str(root), str(output), extensions, quiet=True,
                        hash_options=fpd.parse_hash_args(hash_mode="off"), workers=1,
                        min_image_size=0, reuse_other_lists=False, archives=archives)
    with open(output, encoding="utf-8", newline="") as handle:
        lines = (line for line in handle if not line.startswith("#"))
        return {r["filepath"][len(str(root)) + 1:]: r
                for r in csv.DictReader(lines, delimiter="\t")}


def _make_archives(root):
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(root / "export.zip", "w") as archive:
        archive.writestr("photos/IMG_1.jpg", b"a" * 1234)
        archive.writestr("notes.txt", b"hello")
        archive.writestr("photos/", b"")
    data = b"b" * 777
    with tarfile.open(root / "backup.tgz", "w:gz") as archive:
        info = tarfile.TarInfo("./DCIM/DSC_9.NEF")
        info.size = len(data)
        info.mtime = 1_600_000_000
        archive.addfile(info, io.BytesIO(data))


def test_archive_kind():
    assert fpd.archive_kind("A.ZIP") == "zip"
    assert fpd.archive_kind("a.tgz") == ""
    assert fpd.archive_kind("a.tar.gz", "all") == "tar"
    assert fpd.archive_kind("a.zip", "off") == ""


def test_zip_contents_are_listed_by_default(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    _make_archives(root)
    rows = _scan(root, tmp_path / "list.tsv", monkeypatch)
    assert rows["export.zip/photos/IMG_1.jpg"]["size_bytes"] == "1234"
    assert rows["export.zip/photos/IMG_1.jpg"]["flags"] == fpd.FLAG_ARCHIVE
    assert "export.zip/notes.txt" in rows
    assert rows["export.zip"]["flags"] == ""  # the archive itself is an ordinary file
    assert not any(k.startswith("backup.tgz/") for k in rows)


def test_all_mode_lists_tar_archives_and_respects_extensions(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    _make_archives(root)
    rows = _scan(root, tmp_path / "list.tsv", monkeypatch, archives="all",
                 extensions=fpd.MEDIA_EXTENSIONS | {"zip", "tgz"})
    assert rows["backup.tgz/DCIM/DSC_9.NEF"]["size_bytes"] == "777"
    assert rows["backup.tgz/DCIM/DSC_9.NEF"]["mtime_ns"] == str(1_600_000_000 * 10**9)
    assert "export.zip/photos/IMG_1.jpg" in rows
    assert "export.zip/notes.txt" not in rows  # not a media file


def test_unchanged_archives_are_not_opened_again(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    _make_archives(root)
    output = tmp_path / "list.tsv"
    _scan(root, output, monkeypatch)
    opened = []
    real = fpd.list_archive
    monkeypatch.setattr(fpd, "list_archive", lambda p, k: opened.append(p) or real(p, k))
    rows = _scan(root, output, monkeypatch)
    assert opened == []
    assert "export.zip/photos/IMG_1.jpg" in rows

    with zipfile.ZipFile(root / "export.zip", "a") as archive:
        archive.writestr("photos/IMG_2.jpg", b"c" * 99)
    os.utime(root / "export.zip", ns=(1, 2_000_000_000_000_000_000))
    rows = _scan(root, output, monkeypatch)
    assert len(opened) == 1
    assert "export.zip/photos/IMG_2.jpg" in rows


def test_damaged_zip_is_listed_as_a_plain_file(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    root.mkdir()
    (root / "broken.zip").write_bytes(b"not a zip")
    rows = _scan(root, tmp_path / "list.tsv", monkeypatch)
    assert list(rows) == ["broken.zip"]


def test_archive_copies_count_for_coverage_but_not_for_cleanup(tmp_path, monkeypatch):
    lists_dir = tmp_path / "lists"
    lists_dir.mkdir()
    exports = tmp_path / "Exports"
    exports.mkdir()
    with zipfile.ZipFile(exports / "facebook.zip", "w") as archive:
        archive.writestr("photos/IMG_1.jpg", b"a" * 1234)
    cards = tmp_path / "Cards"
    cards.mkdir()
    (cards / "IMG_1.jpg").write_bytes(b"a" * 1234)
    _scan(exports, lists_dir / "Exports.tsv", monkeypatch)
    _scan(cards, lists_dir / "Cards.tsv", monkeypatch)
    lists = fpd.find_file_lists(lists_dir)
    drives = {d.name: d for d in cov.build_coverage(lists, cov.CoverageSettings())}
    assert (drives["Cards"].safe.files, drives["Cards"].only_copy.files) == (1, 0)
    for fl in lists:
        fl.drive = fpd.DriveInfo(str(tmp_path / fl.path.stem))
    cards_list = next(fl for fl in lists if fl.path.stem == "Cards")
    plan = cov.plan_cleanup(lists, cov.CoverageSettings(), cards_list.path,
                            keep_folders=["facebook.zip"])
    assert plan.items == []  # a copy inside a zip can't be checked, so nothing is deleted
