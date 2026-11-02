import csv
import os
from types import SimpleNamespace

import pytest

import findphotodates as fpd


def _patch_exiftool(monkeypatch, responses, queried):
    class FixtureExifTool:
        aborted = False

        def abort(self):
            self.aborted = True

        def start(self):
            pass

        def stop(self):
            pass

        def batch_query(self, filepaths, fast2=False):
            queried.extend(filepaths)
            return {fp: responses.get(fp, (None, None, None)) for fp in filepaths}

    monkeypatch.setattr(fpd, "ExifToolPersistent", FixtureExifTool)


def _rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        lines = (line for line in handle if not line.startswith("#"))
        return {os.path.basename(r["filepath"]): r
                for r in csv.DictReader(lines, delimiter="\t")}


def test_placeholder_stat_detection():
    windows = SimpleNamespace(st_file_attributes=0x400000 | 0x20, st_size=5_000_000)
    assert fpd.is_placeholder_stat(windows)
    assert not fpd.is_placeholder_stat(SimpleNamespace(st_file_attributes=0x20, st_size=5))
    assert fpd.is_placeholder_stat(SimpleNamespace(st_flags=0x40000000, st_size=10))
    assert fpd.is_placeholder_stat(SimpleNamespace(st_blocks=0, st_size=1_000_000))
    assert not fpd.is_placeholder_stat(SimpleNamespace(st_blocks=0, st_size=600))
    assert not fpd.is_placeholder_stat(SimpleNamespace(st_blocks=8, st_size=4096))


@pytest.mark.skipif(not hasattr(os.stat_result, "st_blocks"), reason="needs st_blocks")
def test_scan_lists_placeholders_without_reading_them(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    root.mkdir()
    (root / "real.jpg").write_bytes(b"x" * 300)
    with open(root / "cloud.jpg", "wb") as handle:
        handle.truncate(2_000_000)  # sparse: no data blocks, like a cloud placeholder
    if os.stat(root / "cloud.jpg").st_blocks != 0:
        pytest.skip("this filesystem doesn't make sparse files")
    queried = []
    _patch_exiftool(monkeypatch, {str(root / "real.jpg"): ("2024:01:01 10:00:00", "", "")},
                    queried)
    output = tmp_path / "list.tsv"
    options = fpd.parse_hash_args(hash_mode="sample")
    assert fpd.run_scan(str(root), str(output), None, quiet=True, hash_options=options,
                        workers=1, min_image_size=0, reuse_other_lists=False)
    rows = _rows(output)
    assert rows["cloud.jpg"]["flags"] == fpd.FLAG_PLACEHOLDER
    assert rows["cloud.jpg"]["content_hash"] == ""
    assert rows["real.jpg"]["flags"] == ""
    assert rows["real.jpg"]["content_hash"]
    assert queried == [str(root / "real.jpg")]

    # A rescan reuses the list and keeps the flag.
    queried.clear()
    assert fpd.run_scan(str(root), str(output), None, quiet=True, hash_options=options,
                        workers=1, min_image_size=0, reuse_other_lists=False)
    assert _rows(output)["cloud.jpg"]["flags"] == fpd.FLAG_PLACEHOLDER
    assert queried == []


def test_lists_without_flags_column_are_still_reused(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    root.mkdir()
    photo = root / "a.jpg"
    photo.write_bytes(b"x" * 300)
    st = photo.stat()
    output = tmp_path / "old.tsv"
    with open(output, "w", encoding="utf-8", newline="") as handle:
        handle.write(f"# inventory_root={root}\n{fpd.TSV_HEADER}\n")
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(fpd.TSV_COLUMNS[:-1])  # a v1.6 list
        writer.writerow([str(photo), "2020:05:05 05:05:05", st.st_size, st.st_mtime_ns,
                         "", "", "", ""])
    queried = []
    _patch_exiftool(monkeypatch, {}, queried)
    assert fpd.run_scan(str(root), str(output), None, quiet=True,
                        hash_options=fpd.parse_hash_args(hash_mode="off"), workers=1,
                        min_image_size=0, reuse_other_lists=False)
    assert queried == []
    assert _rows(output)["a.jpg"]["date_taken"] == "2020:05:05 05:05:05"
