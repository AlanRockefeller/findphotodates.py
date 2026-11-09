import os

import df2
import findphotodates as fpd


def test_summary_of_new_and_old_lists(tmp_path, capsys):
    rows = [("/d/a.jpg", "", 2000, 1, "", "", "", "abc", ""),
            ("/d/b.mov", "", 3000, 1, "", "", "", "", ""),
            ("/d/notes.txt", "", 10, 1, "", "", "", "", ""),
            ("/d/c.jpg", "", 4000, 1, "", "", "", "", "placeholder"),
            ("/d/x.zip/in.jpg", "", 500, 1, "", "", "", "", "archive")]
    fpd.write_dates_to_file_atomic(str(tmp_path / "Ext (1A2B-3C4D).tsv"), rows,
                                   inventory_root="/d", extra_headers=["volume_label=Ext"])
    legacy = [tmp_path / "g_photo.taken.dates.txt"]  # the spelling Windows allows
    if os.name != "nt":  # ":" can't be in a Windows file name
        legacy.append(tmp_path / "f:photo.taken.dates.txt")
    for path in legacy:
        path.write_text("./x/y.jpg: 2016:01:03 18:03:43\n")
    assert df2.main([str(tmp_path)] + [str(path) for path in legacy]) == 0
    out = capsys.readouterr().out
    ext = next(line for line in out.splitlines() if line.startswith("Ext "))
    assert "5" in ext.split()[1] and "9.0 KB" in ext and "1 cloud-only" in ext
    assert "1 inside archives" in ext
    for path in legacy:
        assert any(line.startswith(path.name[0] + " ") for line in out.splitlines())


def test_default_lists_include_old_lists_in_home(tmp_path, monkeypatch):
    monkeypatch.setattr(df2.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(df2, "list_dir", lambda: tmp_path / "lists")
    old = tmp_path / "g_photo.taken.dates.txt"
    old.write_text("./x/y.jpg: 2016:01:03 18:03:43\n")
    assert df2.find_lists([]) == [old]
