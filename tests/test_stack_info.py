"""The stack column: Olympus focus bracketing and stacking from maker notes."""

import csv
from pathlib import Path

import findphotodates as fpd


def test_stack_label_prefers_the_stacked_result():
    assert fpd._stack_label({"StackedImage": "Focus-stacked (15 images)",
                             "DriveMode": "Sequential"}) == "Focus-stacked (15 images)"
    assert fpd._stack_label({"StackedImage": "No",
                             "DriveMode": "Focus Bracketing, Shot 3"}) == "Focus Bracketing, Shot 3"
    assert fpd._stack_label({"StackedImage": "No", "DriveMode": "Single Shot"}) == "-"
    assert fpd._stack_label({"StackedImage": "-", "DriveMode": "-"}) == "-"
    assert fpd._is_olympus("OLYMPUS IMAGING CORP.") and fpd._is_olympus("OM Digital Solutions")
    assert not fpd._is_olympus("Canon") and not fpd._is_olympus(None)


def test_rows_needing_a_stack_read():
    folders = {"/d/stack"}
    old = {"stack": ""}
    assert fpd._needs_stack_read("/d/x/a.orf", "orf", old, None) is True
    assert fpd._needs_stack_read("/d/stack/a.jpg", "jpg", old, None) is None
    assert fpd._needs_stack_read("/d/stack/a.jpg", "jpg", old, folders) is True
    assert fpd._needs_stack_read("/d/other/a.jpg", "jpg", old, folders) is False
    assert fpd._needs_stack_read("/d/x/a.orf", "orf", {"stack": "-"}, None) is False
    assert fpd._needs_stack_read("/d/x/a.nef", "nef", old, None) is False
    assert fpd._needs_stack_read("/d/x/a.orf", "orf", {"stack": "", "flags": "archive"},
                                 None) is False


def _fake_exiftool(monkeypatch, reads, stack):
    class FakeExifTool:
        aborted = False

        def __init__(self):
            self.last_errors, self.last_stack = {}, {}

        def abort(self):
            self.aborted = True

        def start(self):
            pass

        def stop(self):
            pass

        def batch_query(self, filepaths, fast2=False):
            reads.extend(Path(fp).name for fp in filepaths)
            self.last_stack = {fp: stack.get(Path(fp).name, "-") for fp in filepaths
                               if fpd._file_extension(fp) in fpd.STACK_EXTENSIONS}
            return {fp: ("2026:11:02 15:48:14", None, None) for fp in filepaths}

    monkeypatch.setattr(fpd, "ExifToolPersistent", FakeExifTool)


def _scan(root, output):
    assert fpd.run_scan(str(root), str(output), None, quiet=True,
                        hash_options=fpd.parse_hash_args(hash_mode="off"), workers=1,
                        min_image_size=0, path_style="auto", reuse_other_lists=False)
    with output.open(newline="") as f:
        rows = [line for line in f if not line.startswith("#")]
    return {Path(r["filepath"]).name: r["stack"]
            for r in csv.DictReader(rows, delimiter="\t")}


def test_scan_writes_stack_and_reads_old_lists_once(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    (root / "burst").mkdir(parents=True)
    (root / "other").mkdir()
    for name in ("burst/P1.ORF", "burst/P1.JPG", "burst/P1 stacked.JPG", "other/x.jpg",
                 "other/y.png"):
        (root / name).write_bytes(b"photo " + name.encode())
    stack = {"P1.ORF": "Focus Bracketing, Shot 1", "P1.JPG": "Focus Bracketing, Shot 1",
             "P1 stacked.JPG": "Focus-stacked (15 images)"}
    reads = []
    _fake_exiftool(monkeypatch, reads, stack)
    output = tmp_path / "list.tsv"

    assert _scan(root, output) == {
        "P1.ORF": "Focus Bracketing, Shot 1", "P1.JPG": "Focus Bracketing, Shot 1",
        "P1 stacked.JPG": "Focus-stacked (15 images)", "x.jpg": "-", "y.png": "",
    }

    # A list from before the stack column: the raw file and the JPGs beside
    # it are read again, once; other photos are left alone.
    text = output.read_text()
    lines = [line.rsplit("\t", 1)[0] if not line.startswith("#") else line
             for line in text.splitlines()]
    output.write_text("\n".join(lines) + "\n")
    reads.clear()
    assert _scan(root, output)["P1 stacked.JPG"] == "Focus-stacked (15 images)"
    assert sorted(reads) == ["P1 stacked.JPG", "P1.JPG", "P1.ORF"]

    reads.clear()
    _scan(root, output)
    assert reads == []


def test_olympus_jpgs_read_with_fast2_are_read_again_for_maker_notes(monkeypatch):
    sent = []

    def fake_send(self, args):
        sent.append(list(args))
        fast2 = "-fast2" in args
        records = []
        for fp in [a for a in args if a.startswith("/")]:
            olympus = fp.endswith("o.jpg")
            records.append({
                "SourceFile": fp, "DateTimeOriginal": "2026:11:02 15:48:14",
                "GPSLatitude": "-", "GPSLongitude": "-",
                "Make": "OM Digital Solutions" if olympus else "Canon",
                "DriveMode": "-" if fast2 or not olympus else "Focus Bracketing, Shot 2",
                "StackedImage": "-" if fast2 or not olympus else "No",
            })
        import json
        return json.dumps(records).splitlines()

    monkeypatch.setattr(fpd.ExifToolPersistent, "_send_and_read", fake_send)
    tool = fpd.ExifToolPersistent()
    results = tool.batch_query(["/p/o.jpg", "/p/c.jpg"], fast2=True)
    assert set(results) == {"/p/o.jpg", "/p/c.jpg"}
    assert tool.last_stack == {"/p/o.jpg": "Focus Bracketing, Shot 2", "/p/c.jpg": "-"}
    assert len(sent) == 2 and "-fast2" not in sent[1] and "/p/c.jpg" not in sent[1]


def test_stack_is_searchable_and_dash_is_blank(tmp_path):
    from findphotodates_search import InventorySearchIndex
    inventory = tmp_path / "Drive.tsv"
    with inventory.open("w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(fpd.TSV_COLUMNS)
        for name, stack in (("a.ORF", "Focus Bracketing, Shot 1"),
                            ("b.JPG", "Focus-stacked (15 images)"), ("c.JPG", "-")):
            writer.writerow([f"/d/{name}", "", "1", "1", "", "", "", "", "", stack])
    index = InventorySearchIndex(tmp_path / "search.sqlite3")
    index.refresh([inventory])

    def names(query):
        return [Path(r.filepath).name for r in index.search(query)[0]]
    assert names("stack:bracketing") == ["a.ORF"]
    assert names("stack:stacked") == ["b.JPG"]
    assert sorted(names("focus")) == ["a.ORF", "b.JPG"]
    assert names("stack:missing") == ["c.JPG"]
