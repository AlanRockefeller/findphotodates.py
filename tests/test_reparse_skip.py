"""Tests for Windows reparse-point directory skipping in find_files().

We can't create real NTFS junctions in the test environment, so we exercise
_is_windows_reparse_point_dir() with fake DirEntry objects whose stat() returns
an object carrying st_file_attributes — mirroring what os.scandir() returns on
Windows.  We also verify find_files() still indexes ordinary files on POSIX.
"""

import os
import stat as stat_mod
import tempfile

import findphotodates


REPARSE_BIT = getattr(stat_mod, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
DIRECTORY_BIT = 0x10


class _FakeStat:
    def __init__(self, attrs):
        self.st_file_attributes = attrs


class _FakeEntry:
    def __init__(self, path, attrs):
        self.path = path
        self.name = os.path.basename(path)
        self._attrs = attrs

    def stat(self, follow_symlinks=True):
        return _FakeStat(self._attrs)


def test_reparse_dir_helper_true_when_bit_set():
    entry = _FakeEntry(
        "C:\\ProgramData\\Application Data", REPARSE_BIT | DIRECTORY_BIT
    )
    assert findphotodates._is_windows_reparse_point_dir(entry) is True


def test_reparse_dir_helper_false_when_bit_clear():
    entry = _FakeEntry("C:\\ProgramData\\Adobe", DIRECTORY_BIT)
    assert findphotodates._is_windows_reparse_point_dir(entry) is False


def test_reparse_dir_helper_false_when_attribute_missing():
    # Simulate a POSIX scandir entry — stat() result has no st_file_attributes.
    class _PosixEntry:
        path = "/tmp/foo"

        def stat(self, follow_symlinks=True):
            return os.stat_result((0,) * 10)

    assert findphotodates._is_windows_reparse_point_dir(_PosixEntry()) is False


def test_find_files_indexes_normal_files_on_posix():
    # Sanity check on this platform: ordinary files are still yielded.
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "hello.txt")
        with open(p, "w") as f:
            f.write("hi")
        results = list(findphotodates.find_files(d, None))
        assert any(r[0] == p for r in results)
