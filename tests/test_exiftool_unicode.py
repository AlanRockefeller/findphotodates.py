"""Regression tests for Unicode-safe ExifTool subprocess pipes.

Reproduces the U+202F (narrow no-break space) crash on Windows where Python's
default cp1252 stdin encoding raised UnicodeEncodeError.  We don't actually
spawn exiftool here — we monkeypatch subprocess.Popen so the test runs on any
platform without depending on exiftool being installed.
"""

import io
import subprocess

import findphotodates


class _FakeProc:
    def __init__(self, *args, **kwargs):
        self._popen_args = args
        self._popen_kwargs = kwargs
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("{ready}\n")

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_popen_uses_utf8_and_filename_charset(monkeypatch):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    et = findphotodates.ExifToolPersistent()
    et.start()

    assert captured["kwargs"].get("encoding") == "utf-8"
    assert captured["kwargs"].get("errors") == "surrogateescape"
    cmd = captured["cmd"]
    # ExifTool must be told that filenames on the argfile pipe are UTF-8,
    # otherwise on Windows it interprets them as the active code page.
    assert "-charset" in cmd
    assert "filename=UTF8" in cmd
    assert "-stay_open" in cmd


def test_send_and_read_survives_unicode_encode_error(monkeypatch):
    class _BadStdinProc:
        def __init__(self):
            self.stdout = io.StringIO("")
            self.killed = False

            class _Stdin:
                def write(self_inner, _data):
                    raise UnicodeEncodeError("charmap", " ", 0, 1, "bad")

                def flush(self_inner):
                    pass

            self.stdin = _Stdin()

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    et = findphotodates.ExifToolPersistent()
    et._proc = _BadStdinProc()

    # Should not raise — must return None so callers can fall back gracefully.
    result = et._send_and_read(["-json", "/tmp/unicode name.jpg"])
    assert result is None
    assert et._proc is None


def test_unicode_filename_roundtrip_in_batch(monkeypatch):
    """A filename containing U+202F must be processable through batch_query
    without raising — even when the underlying exiftool call yields nothing.
    """

    class _EmptyProc:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("[]\n{ready}\n")

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    et = findphotodates.ExifToolPersistent()
    et._proc = _EmptyProc()

    path = "/tmp/unicode space.jpg"
    out = et.batch_query([path])
    assert out == {path: (None, None, None)}
