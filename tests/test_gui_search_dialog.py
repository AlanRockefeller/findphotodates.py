"""The search window refreshes its index one list set at a time."""

import queue
import threading
import time
import types

import pytest

pytest.importorskip("customtkinter")
gui = pytest.importorskip("findphotodates_gui")


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _BlockingIndex:
    """Each refresh waits until the test releases it."""

    def __init__(self):
        self.calls, self.active, self.most_active = [], 0, 0
        self.started = {}
        self.release = {}
        self.skipped = []
        self._lock = threading.Lock()

    def expect(self, name):
        self.started[name] = threading.Event()
        self.release[name] = threading.Event()

    def refresh(self, paths, progress=None):
        name = paths[0]
        with self._lock:
            self.calls.append(name)
            self.active += 1
            self.most_active = max(self.most_active, self.active)
        self.started[name].set()
        assert self.release[name].wait(5)
        with self._lock:
            self.active -= 1
        return 1, {"one": 10, "two": 20, "three": 30}[name]


def _dialog(index):
    return types.SimpleNamespace(
        index=index, file_lists=[], events=queue.Queue(), index_generation=0,
        _index_lock=threading.Lock(), index_ready=False, pending_search=False,
        _closed=False, _after_id=None, status_var=_Var(), query_var=_Var(),
        date_var=_Var(), search_btn=types.SimpleNamespace(configure=lambda **kw: None),
        after=lambda *args: None, _search=lambda: None, _drain_events=None,
    )


def _use(dialog, name):
    dialog.file_lists = [types.SimpleNamespace(path=name)]
    gui.FileSearchDialog._start_index(dialog)


def _wait_until_free(lock):
    deadline = time.monotonic() + 5
    while lock.locked():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_lists_changed_during_a_refresh_wait_and_old_results_are_ignored():
    index = _BlockingIndex()
    for name in ("one", "three"):
        index.expect(name)
    dialog = _dialog(index)

    _use(dialog, "one")
    assert index.started["one"].wait(5)
    _use(dialog, "two")    # replaced by "three" before it could start
    _use(dialog, "three")

    index.release["one"].set()
    assert index.started["three"].wait(5)
    gui.FileSearchDialog._drain_events(dialog)
    assert not dialog.index_ready  # "one" finished, but those lists were replaced

    index.release["three"].set()
    _wait_until_free(dialog._index_lock)
    gui.FileSearchDialog._drain_events(dialog)
    assert dialog.index_ready
    assert "30 files ready" in dialog.status_var.get()
    assert index.calls == ["one", "three"]
    assert index.most_active == 1
