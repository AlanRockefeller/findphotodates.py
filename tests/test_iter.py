import io
import csv


def test_file_iteration():
    f = io.StringIO("line1\nline2\nline3\nline4\n")
    for line in f:
        assert line.strip() == "line1"
        break
    assert f.read() == "line2\nline3\nline4\n"


def test_csv_reader_continuation():
    f2 = io.StringIO("h1\th2\nval1\tval2\nval3\tval4\n")
    for line in f2:
        assert line.strip() == "h1\th2"
        break

    rows = list(csv.reader(f2, delimiter="\t"))
    assert rows == [["val1", "val2"], ["val3", "val4"]]
