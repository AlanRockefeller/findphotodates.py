"""The GUI's finished-job summary and dialog text layout (no display needed)."""

import pytest

pytest.importorskip("customtkinter")
gui = pytest.importorskip("findphotodates_gui")
dialogs = pytest.importorskip("findphotodates_gui_dialogs")


def _summarize(lines):
    summary = {}
    for line in lines:
        gui.note_summary_line(line, summary)
    return gui.summary_text(summary)


def test_scan_summary_counts_files_and_changes():
    lines = [
        "Dates written to '/x/Sierra Club (5F61-DDF5).tsv' (877,829 files listed).",
        "Files indexed: 877,829 (162,703 with EXIF dates)",
        "Changes since the last scan:",
        "New files: 1,203",
        "       54  Important/Videos",
        "Removed (no longer found): 12",
    ]
    assert _summarize(lines) == ("877,829 files in the file list (162,703 with photo dates)"
                                 " · 1,203 new, 12 removed since the last scan")


def test_scan_summary_first_scan_and_no_changes():
    first = ["Dates written to 'x' (33 files listed).",
             "This is a new file list, so there is no earlier scan to compare with."]
    assert _summarize(first) == "33 files in the file list · first scan of this drive"
    same = ["Dates written to 'x' (5 files listed).", "  Nothing new, changed or removed."]
    assert _summarize(same) == "5 files in the file list · no changes since the last scan"


def test_jobs_without_a_summary_say_nothing():
    assert _summarize(["Checking drive health…", "PASSED"]) is None


def test_dialog_uses_a_short_first_paragraph_as_its_headline():
    assert dialogs.split_message("Make or update file lists",
                                 "Run these scans in order?\n\nLightroom: update its list") == (
        "Run these scans in order?", "Lightroom: update its list")
    assert dialogs.split_message("Unmount", "E is safe to unplug.") == ("E is safe to unplug.", "")
    long_line = "x" * 200
    assert dialogs.split_message("Command", long_line) == ("Command", long_line)
