"""Folders only: matches grouped into the folder whose name was searched for."""

import csv

from findphotodates_search import InventorySearchIndex, folder_for

FIELDS = ["filepath", "date_taken", "size_bytes", "mtime_ns", "gps_lat", "gps_lon",
          "location", "content_hash"]


def _list(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        for filepath, date, size, mtime in rows:
            writer.writerow({"filepath": filepath, "date_taken": date, "size_bytes": size,
                             "mtime_ns": mtime})


def test_folder_for_picks_the_highest_matching_folder():
    path = "/mnt/c/Pictures/olympus.stack.input.photos/2026/2026-01-03/P1.JPG"
    assert folder_for(path, ["olympus", "stack", "input"]) == "/mnt/c/Pictures/olympus.stack.input.photos"
    assert folder_for(path, ["olympus", "01"]) == "/mnt/c/Pictures/olympus.stack.input.photos/2026/2026-01-03"
    assert folder_for(path, ["p1"]) == "/mnt/c/Pictures/olympus.stack.input.photos/2026/2026-01-03"
    assert folder_for(path, []) == "/mnt/c/Pictures/olympus.stack.input.photos/2026/2026-01-03"
    assert folder_for(r"F:\Photos\a.jpg", ["photos"]) == r"F:\Photos"
    assert folder_for("/a.jpg", []) == "/"


def test_search_folders_counts_files_per_matching_folder(tmp_path):
    drive_c, drive_h = tmp_path / "Drive C.tsv", tmp_path / "Drive H.tsv"
    _list(drive_c, [
        ("/c/olympus.stack.input.photos/2025/a.JPG", "2025:12:31 10:00:00", 100, 5),
        ("/c/olympus.stack.input.photos/2026/b.ORF", "2026:01:03 10:00:00", 200, 9),
        ("/c/olympus.stack.input.photos/2026/notes.json", "", 50, 7),
        ("/c/other/c.JPG", "2024:01:01 10:00:00", 1, 1),
    ])
    _list(drive_h, [("/h/olympus.stack.input.photos/x.JPG", "", 10, 3)])
    index = InventorySearchIndex(tmp_path / "search.sqlite3")
    index.refresh([drive_c, drive_h])

    found = index.search_folders("olympus.stack.input")
    assert [(f.filepath, f.files, f.size_bytes) for f in found] == [
        ("/c/olympus.stack.input.photos", 3, 350),
        ("/h/olympus.stack.input.photos", 1, 10),
    ]
    assert (found[0].first_date, found[0].last_date, found[0].newest_mtime_ns) == \
        ("2025-12-31", "2026-01-03", 9)
    assert found[1].first_date == ""

    # A filter about files groups by each file's own folder.
    assert [f.filepath for f in index.search_folders("ext:orf")] == \
        ["/c/olympus.stack.input.photos/2026"]
    assert index.search_folders("olympus", should_stop=lambda: True) is None


def test_windows_drive_root_keeps_its_separator():
    assert folder_for(r"F:\a.jpg", []) == "F:\\"
    assert folder_for(r"F:\Photos\a.jpg", ["photos"]) == r"F:\Photos"
