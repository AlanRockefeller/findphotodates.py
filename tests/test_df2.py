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
    (tmp_path / "f:photo.taken.dates.txt").write_text("./x/y.jpg: 2016:01:03 18:03:43\n")
    assert df2.main([str(tmp_path), str(tmp_path / "f:photo.taken.dates.txt")]) == 0
    out = capsys.readouterr().out
    ext = next(line for line in out.splitlines() if line.startswith("Ext "))
    assert "5" in ext.split()[1] and "9.0 KB" in ext and "1 cloud-only" in ext
    assert "1 inside archives" in ext
    assert any(line.startswith("f ") for line in out.splitlines())
