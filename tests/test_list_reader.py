"""One reader for file lists: names with tabs, quotes and line breaks."""

import os

import pytest

import findphotodates as fpd
import findphotodates_coverage as cov
from findphotodates_common import split_list_lines
from findphotodates_search import InventorySearchIndex

ODD = '/drive/odd "name"\twith tab\nand line break.jpg'


def _write(path, root="/drive"):
    rows = [
        (ODD, "2024:06:30 10:00:00", 123_456, 5, "", "", "", "", ""),
        ("/drive/plain.jpg", "2024:06:30 10:00:01", 234_567, 6, "", "", "", "", ""),
    ]
    fpd.write_dates_to_file_atomic(str(path), rows, inventory_root=root)
    return path


def test_rows_with_quotes_tabs_and_line_breaks(tmp_path):
    path = _write(tmp_path / "list.tsv")
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(split_list_lines(handle))
    assert rows[0] == fpd.TSV_COLUMNS
    assert [r[0] for r in rows[1:]] == [ODD, "/drive/plain.jpg"]
    assert all(len(r) == len(fpd.TSV_COLUMNS) for r in rows)


def test_damaged_quoting_only_loses_that_line():
    lines = ["filepath\tsize\n", '"/broken\t1\n', "/ok.jpg\t2\n", "/ok2.jpg\t3\n"]
    rows = list(split_list_lines(lines))
    assert rows[0] == ["filepath", "size"]
    assert ["/ok.jpg", "2"] in rows and ["/ok2.jpg", "3"] in rows


def test_blank_lines_are_not_rows(tmp_path):
    lines = ["filepath\tsize\n", "\n", "/a.jpg\t1\n", "\r\n", "\t\n", "/b.jpg\t2\n", "\n"]
    assert list(split_list_lines(lines)) == [["filepath", "size"], ["/a.jpg", "1"],
                                             ["/b.jpg", "2"]]
    # A blank line inside a quoted name is part of the name.
    quoted = ["filepath\tsize\n", '"/odd\n', "\n", 'name.jpg"\t3\n']
    assert list(split_list_lines(quoted))[1] == ["/odd\n\nname.jpg", "3"]

    path = _write(tmp_path / "list.tsv")
    with open(path, "a", encoding="utf-8", newline="") as handle:
        handle.write("\n\n")
    index = InventorySearchIndex(tmp_path / "index.sqlite3")
    _changed, total = index.refresh([path])
    assert total == 2


def test_coverage_search_and_reuse_all_read_the_odd_name(tmp_path):
    lists = tmp_path / "lists"
    lists.mkdir()
    path = _write(lists / "A.tsv")
    names = {row.name for row in cov.iter_media(path)}
    assert "odd \"name\"\twith tab\nand line break.jpg" in names

    index = InventorySearchIndex(tmp_path / "search.sqlite3")
    assert index.refresh([path]) == (1, 2)

    other = fpd._build_other_lists_index(str(lists / "B.tsv"), quiet=True)
    key = fpd._other_list_key(ODD.rsplit("/", 1)[-1].lower(), 123_456, 5)
    assert other[key] == "2024:06:30 10:00:00"


@pytest.mark.skipif(os.name == "nt", reason="Windows file names can't contain line breaks")
def test_add_hashes_keeps_rows_with_line_breaks(tmp_path):
    folder = tmp_path / "drive"
    folder.mkdir()
    odd = folder / "two\nlines.jpg"
    odd.write_bytes(b"x" * 300)
    st = odd.stat()
    path = tmp_path / "list.tsv"
    fpd.write_dates_to_file_atomic(
        str(path), [(str(odd), "", st.st_size, st.st_mtime_ns, "", "", "", "", "")],
        inventory_root=str(folder))
    assert fpd.add_hashes_to_inventory(
        str(path), fpd.parse_hash_args(hash_mode="sample", no_hash_cache=True), quiet=True)
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(split_list_lines(handle))
    assert len(rows) == 2 and rows[1][0] == str(odd) and rows[1][7]
