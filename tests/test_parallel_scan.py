import csv
import io
import os
import signal
import threading
import time
from pathlib import Path

import pytest

import findphotodates as fpd


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        import json

        return json.dumps(self.payload).encode("utf-8")


@pytest.fixture(autouse=True)
def reset_geolocation_state():
    saved_cache = dict(fpd._coord_cache)
    saved_time = fpd._last_geolocate_time
    fpd._coord_cache.clear()
    fpd._last_geolocate_time = 0
    yield
    fpd._coord_cache.clear()
    fpd._coord_cache.update(saved_cache)
    fpd._last_geolocate_time = saved_time


def _make_fixture_tree(root):
    files = {
        "a.jpg": b"jpg-a",
        "b.png": b"png-b",
        "c.mp4": b"mp4-c",
        "d.txt": b"text-d",
        "sub/e.jpg": b"jpg-e",
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return files


def _responses_for_fixture(root):
    return {
        str(root / "a.jpg"): ("2024:01:01 10:00:00", "37.871", "-122.273"),
        str(root / "b.png"): ("2024:01:02 10:00:00", "37.872", "-122.274"),
        str(root / "c.mp4"): ("2024:01:03 10:00:00", "", ""),
        str(root / "sub/e.jpg"): ("2024:01:04 10:00:00", "37.873", "-122.275"),
    }


def _patch_fixture_exiftool(monkeypatch, responses):
    class FixtureExifTool:
        def start(self):
            pass

        def stop(self):
            pass

        def batch_query(self, filepaths, fast2=False):
            return {fp: responses.get(fp, (None, None, None)) for fp in filepaths}

    monkeypatch.setattr(fpd, "ExifToolPersistent", FixtureExifTool)


def _expected_inventory_bytes(root, hash_options):
    rows = []
    responses = _responses_for_fixture(root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        filepath = str(path)
        stat_info = path.stat()
        if path.suffix.lower().lstrip(".") in fpd.EXIFTOOL_EXTENSIONS:
            date_taken, gps_lat, gps_lon = responses.get(filepath, ("", "", ""))
        else:
            date_taken, gps_lat, gps_lon = "", "", ""
        rows.append(
            [
                filepath,
                date_taken or "",
                str(stat_info.st_size),
                str(stat_info.st_mtime_ns),
                gps_lat or "",
                gps_lon or "",
                "",
                "",
            ]
        )

    out = io.StringIO()
    out.write(f"# inventory_root={root}\n")
    out.write(f"# hash_mode={hash_options.hash_mode}\n")
    out.write(fpd.TSV_HEADER + "\n")
    writer = csv.writer(out, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(fpd.TSV_COLUMNS)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def test_single_worker_matches_expected_serial_inventory(tmp_path, monkeypatch):
    root = tmp_path / "scan"
    _make_fixture_tree(root)
    responses = _responses_for_fixture(root)
    _patch_fixture_exiftool(monkeypatch, responses)
    output = tmp_path / "single.tsv"
    hash_options = fpd.parse_hash_args(hash_mode="off")

    assert fpd.run_scan(
        str(root),
        str(output),
        None,
        quiet=True,
        hash_options=hash_options,
        workers=1,
        min_image_size=0,
    )

    assert output.read_bytes() == _expected_inventory_bytes(root, hash_options)


def test_multi_worker_matches_single_worker_inventory(tmp_path, monkeypatch):
    root = tmp_path / "scan"
    _make_fixture_tree(root)
    responses = _responses_for_fixture(root)
    _patch_fixture_exiftool(monkeypatch, responses)
    hash_options = fpd.parse_hash_args(hash_mode="off")
    single = tmp_path / "single.tsv"
    multi = tmp_path / "multi.tsv"

    assert fpd.run_scan(
        str(root),
        str(single),
        None,
        quiet=True,
        hash_options=hash_options,
        workers=1,
        min_image_size=0,
    )
    assert fpd.run_scan(
        str(root),
        str(multi),
        None,
        quiet=True,
        hash_options=hash_options,
        workers=4,
        min_image_size=0,
    )

    assert multi.read_bytes() == single.read_bytes()


def _write_sized_file(root, name, size):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    pattern = name.encode("ascii") or b"x"
    path.write_bytes((pattern * ((size // len(pattern)) + 1))[:size])
    return path


def _read_data_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return [
            row
            for row in csv.DictReader(
                (line for line in f if not line.startswith("#")),
                delimiter="\t",
            )
        ]


def _patch_recording_exiftool(monkeypatch, calls):
    class RecordingExifTool:
        def start(self):
            pass

        def stop(self):
            pass

        def batch_query(self, filepaths, fast2=False):
            calls.append((fast2, tuple(sorted(Path(fp).name for fp in filepaths))))
            return {
                fp: ("2024:01:01 10:00:00", "37.871", "-122.273")
                for fp in filepaths
            }

    monkeypatch.setattr(fpd, "ExifToolPersistent", RecordingExifTool)


def test_min_image_size_filter_routes_tiny_images_only(tmp_path, monkeypatch):
    root = tmp_path / "scan"
    _write_sized_file(root, "tiny.jpg", 1_000)
    _write_sized_file(root, "normal.jpg", 200_000)
    _write_sized_file(root, "tiny.png", 5_000)
    _write_sized_file(root, "tiny.nef", 1_000)
    _write_sized_file(root, "tiny.mp4", 1_000)
    calls = []
    _patch_recording_exiftool(monkeypatch, calls)
    output = tmp_path / "out.tsv"

    assert fpd.run_scan(
        str(root),
        str(output),
        None,
        quiet=True,
        hash_options=fpd.parse_hash_args(hash_mode="off"),
        workers=1,
        min_image_size=100_000,
    )

    sent = {name for _fast2, names in calls for name in names}
    assert "tiny.jpg" not in sent
    assert "tiny.png" not in sent
    assert {"normal.jpg", "tiny.nef", "tiny.mp4"} <= sent

    rows = {Path(row["filepath"]).name: row for row in _read_data_rows(output)}
    assert set(rows) == {"tiny.jpg", "normal.jpg", "tiny.png", "tiny.nef", "tiny.mp4"}
    assert rows["tiny.jpg"]["size_bytes"] == "1000"
    assert rows["tiny.png"]["size_bytes"] == "5000"
    assert rows["tiny.jpg"]["date_taken"] == ""
    assert rows["tiny.png"]["date_taken"] == ""
    assert rows["normal.jpg"]["date_taken"] == "2024:01:01 10:00:00"
    assert rows["tiny.nef"]["date_taken"] == "2024:01:01 10:00:00"
    assert rows["tiny.mp4"]["date_taken"] == "2024:01:01 10:00:00"


def test_min_image_size_zero_disables_filter(tmp_path, monkeypatch):
    root = tmp_path / "scan"
    for name, size in [
        ("tiny.jpg", 1_000),
        ("normal.jpg", 200_000),
        ("tiny.png", 5_000),
        ("tiny.nef", 1_000),
        ("tiny.mp4", 1_000),
    ]:
        _write_sized_file(root, name, size)
    calls = []
    _patch_recording_exiftool(monkeypatch, calls)

    assert fpd.run_scan(
        str(root),
        str(tmp_path / "out.tsv"),
        None,
        quiet=True,
        hash_options=fpd.parse_hash_args(hash_mode="off"),
        workers=1,
        min_image_size=0,
    )

    sent = {name for _fast2, names in calls for name in names}
    assert sent == {"tiny.jpg", "normal.jpg", "tiny.png", "tiny.nef", "tiny.mp4"}


def test_fast2_applies_only_to_safe_image_extensions(tmp_path, monkeypatch):
    root = tmp_path / "scan"
    for name in ["a.jpg", "b.png", "c.nef", "d.mp4"]:
        _write_sized_file(root, name, 1_000)
    calls = []
    _patch_recording_exiftool(monkeypatch, calls)

    assert fpd.run_scan(
        str(root),
        str(tmp_path / "out.tsv"),
        None,
        quiet=True,
        hash_options=fpd.parse_hash_args(hash_mode="off"),
        workers=1,
        min_image_size=0,
    )

    assert len(calls) == 2
    assert (True, ("a.jpg", "b.png")) in calls
    assert (False, ("c.nef", "d.mp4")) in calls


def test_all_jpeg_batch_runs_only_fast2_query(tmp_path, monkeypatch):
    root = tmp_path / "scan"
    for name in ["a.jpg", "b.jpeg", "c.jpg"]:
        _write_sized_file(root, name, 1_000)
    calls = []
    _patch_recording_exiftool(monkeypatch, calls)

    assert fpd.run_scan(
        str(root),
        str(tmp_path / "out.tsv"),
        None,
        quiet=True,
        hash_options=fpd.parse_hash_args(hash_mode="off"),
        workers=1,
        min_image_size=0,
    )

    assert calls == [(True, ("a.jpg", "b.jpeg", "c.jpg"))]


def test_all_raw_batch_runs_only_full_parse_query(tmp_path, monkeypatch):
    root = tmp_path / "scan"
    for name in ["a.nef", "b.nef", "c.nef"]:
        _write_sized_file(root, name, 1_000)
    calls = []
    _patch_recording_exiftool(monkeypatch, calls)

    assert fpd.run_scan(
        str(root),
        str(tmp_path / "out.tsv"),
        None,
        quiet=True,
        hash_options=fpd.parse_hash_args(hash_mode="off"),
        workers=1,
        min_image_size=0,
    )

    assert calls == [(False, ("a.nef", "b.nef", "c.nef"))]


def test_file_counts_include_size_filtered_and_worker_paths(
    tmp_path, monkeypatch
):
    root = tmp_path / "scan"
    _write_sized_file(root, "tiny-a.jpg", 1_000)
    _write_sized_file(root, "tiny-b.jpg", 1_000)
    _write_sized_file(root, "normal.jpg", 200_000)
    _write_sized_file(root, "a.txt", 100)
    _write_sized_file(root, "b.txt", 100)
    calls = []
    captured_counts = {}
    _patch_recording_exiftool(monkeypatch, calls)

    def capture_summary(photo_data, file_counts, quiet, debug=False):
        captured_counts.update(file_counts)

    monkeypatch.setattr(fpd, "summarize_results", capture_summary)

    assert fpd.run_scan(
        str(root),
        str(tmp_path / "out.tsv"),
        None,
        quiet=True,
        hash_options=fpd.parse_hash_args(hash_mode="off"),
        workers=1,
        min_image_size=100_000,
    )

    assert captured_counts["jpg"] == 3
    assert captured_counts["txt"] == 2
    sent = {name for _fast2, names in calls for name in names}
    assert sent == {"normal.jpg"}


def test_geolocate_lock_serializes_nominatim_requests(monkeypatch):
    timestamps = []
    timestamps_lock = threading.Lock()
    fake_time_lock = threading.Lock()
    fake_now = {"value": 1_000.0}
    start_event = threading.Event()
    errors = []

    def fake_time():
        with fake_time_lock:
            return fake_now["value"]

    def fake_sleep(seconds):
        with fake_time_lock:
            fake_now["value"] += seconds

    def fake_urlopen(req, timeout):
        with timestamps_lock:
            timestamps.append(fpd.time.time())
        return FakeResponse({"display_name": "Serialized Place"})

    monkeypatch.setattr(fpd.time, "time", fake_time)
    monkeypatch.setattr(fpd.time, "sleep", fake_sleep)
    monkeypatch.setattr(fpd.urllib.request, "urlopen", fake_urlopen)

    def call_geolocate(i):
        try:
            start_event.wait()
            fpd.geolocate(f"{10 + i * 0.02:.4f}", f"{20 + i * 0.02:.4f}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=call_geolocate, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    start_event.set()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(timestamps) == 20
    intervals = [b - a for a, b in zip(timestamps, timestamps[1:], strict=False)]
    assert all(interval >= 0.99 for interval in intervals)


def test_location_cache_concurrent_writes(tmp_path):
    conn = fpd.open_location_cache(str(tmp_path / "location_cache.sqlite"))
    errors = []

    def write_rows(worker_id):
        try:
            for i in range(100):
                fpd.put_cached_location(
                    conn,
                    float(worker_id),
                    float(i),
                    fpd.LOCATION_CACHE_PRECISION,
                    f"location-{worker_id}-{i}",
                )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write_rows, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    with fpd._sqlite_lock:
        count = conn.execute("SELECT COUNT(*) FROM location_cache").fetchone()[0]
    conn.close()
    assert count == 400


def test_keyboard_interrupt_during_dispatch_writes_clean_partial_tsv(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(fpd, "EXIFTOOL_BATCH_SIZE", 5)
    root = tmp_path / "scan"
    for i in range(30):
        path = root / f"{i:03d}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpg")

    class SlowExifTool:
        def start(self):
            pass

        def stop(self):
            pass

        def batch_query(self, filepaths, fast2=False):
            time.sleep(0.1)
            return {
                fp: ("2024:01:01 10:00:00", "37.871", "-122.273")
                for fp in filepaths
            }

    monkeypatch.setattr(fpd, "ExifToolPersistent", SlowExifTool)
    output = tmp_path / "partial.tsv"
    timer = threading.Timer(0.16, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        result = fpd.run_scan(
            str(root),
            str(output),
            {"jpg"},
            quiet=True,
            hash_options=fpd.parse_hash_args(hash_mode="off"),
            workers=2,
            min_image_size=0,
        )
    finally:
        timer.cancel()

    assert result is False
    cache = fpd.load_cache(str(output))
    assert 0 <= len(cache) < 30


def test_repeated_keyboard_interrupt_during_cleanup_stays_friendly(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(fpd, "EXIFTOOL_BATCH_SIZE", 2)
    root = tmp_path / "scan"
    for i in range(30):
        path = root / f"{i:03d}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpg")

    class SlowExifTool:
        def start(self):
            pass

        def stop(self):
            pass

        def batch_query(self, filepaths, fast2=False):
            time.sleep(0.5)
            return {
                fp: ("2024:01:01 10:00:00", "37.871", "-122.273")
                for fp in filepaths
            }

    monkeypatch.setattr(fpd, "ExifToolPersistent", SlowExifTool)
    output = tmp_path / "partial.tsv"
    first = threading.Timer(0.08, lambda: os.kill(os.getpid(), signal.SIGINT))
    second = threading.Timer(0.10, lambda: os.kill(os.getpid(), signal.SIGINT))
    first.start()
    second.start()
    try:
        result = fpd.run_scan(
            str(root),
            str(output),
            {"jpg"},
            quiet=True,
            hash_options=fpd.parse_hash_args(hash_mode="off"),
            workers=1,
            min_image_size=0,
        )
    finally:
        first.cancel()
        second.cancel()

    captured = capsys.readouterr()
    assert result is False
    assert "Interrupted by Ctrl-C" in captured.out
    assert "Traceback" not in captured.out


def test_crashing_worker_records_failed_batch_with_blank_exif(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(fpd, "EXIFTOOL_BATCH_SIZE", 2)
    root = tmp_path / "scan"
    for i in range(6):
        path = root / f"{i:03d}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpg")

    class FlakyExifTool:
        calls = 0
        lock = threading.Lock()

        def start(self):
            pass

        def stop(self):
            pass

        def batch_query(self, filepaths, fast2=False):
            with self.lock:
                type(self).calls += 1
                call = type(self).calls
            if call == 1:
                raise RuntimeError("boom")
            return {
                fp: ("2024:01:01 10:00:00", "37.871", "-122.273")
                for fp in filepaths
            }

    monkeypatch.setattr(fpd, "ExifToolPersistent", FlakyExifTool)
    output = tmp_path / "out.tsv"

    assert fpd.run_scan(
        str(root),
        str(output),
        {"jpg"},
        quiet=True,
        hash_options=fpd.parse_hash_args(hash_mode="off"),
        workers=1,
        min_image_size=0,
    )

    with open(output, "r", encoding="utf-8", newline="") as f:
        rows = [
            row
            for row in csv.DictReader(
                (line for line in f if not line.startswith("#")),
                delimiter="\t",
            )
        ]
    assert len(rows) == 6
    assert FlakyExifTool.calls == 3
    assert any(not row["date_taken"] for row in rows)
    assert any(row["date_taken"] for row in rows)


def test_missing_exiftool_startup_failure_fails_scan(tmp_path, monkeypatch, capsys):
    root = tmp_path / "scan"
    path = root / "a.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jpg")

    class MissingExifTool:
        def start(self):
            raise FileNotFoundError("exiftool")

        def stop(self):
            pass

    monkeypatch.setattr(fpd, "ExifToolPersistent", MissingExifTool)
    output = tmp_path / "out.tsv"

    assert (
        fpd.run_scan(
            str(root),
            str(output),
            {"jpg"},
            quiet=True,
            hash_options=fpd.parse_hash_args(hash_mode="off"),
            workers=1,
            min_image_size=0,
        )
        is False
    )
    assert "failed to start" in capsys.readouterr().err
    assert not output.exists()


def test_normal_shutdown_timeout_fails_and_returns_quickly(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(fpd, "WORKER_SHUTDOWN_TIMEOUT_SECONDS", 0.2)
    root = tmp_path / "scan"
    path = root / "a.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jpg")

    class HangingStopExifTool:
        def start(self):
            pass

        def stop(self):
            time.sleep(0.6)

        def batch_query(self, filepaths, fast2=False):
            return {
                fp: ("2024:01:01 10:00:00", "37.871", "-122.273")
                for fp in filepaths
            }

    monkeypatch.setattr(fpd, "ExifToolPersistent", HangingStopExifTool)
    output = tmp_path / "out.tsv"
    write_contexts = []
    real_safe_write_inventory = fpd._safe_write_inventory

    def recording_safe_write_inventory(*args, **kwargs):
        write_contexts.append(kwargs.get("context"))
        return real_safe_write_inventory(*args, **kwargs)

    monkeypatch.setattr(fpd, "_safe_write_inventory", recording_safe_write_inventory)

    started = time.monotonic()
    assert (
        fpd.run_scan(
            str(root),
            str(output),
            {"jpg"},
            quiet=True,
            hash_options=fpd.parse_hash_args(hash_mode="off"),
            workers=1,
            min_image_size=0,
        )
        is False
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    err = capsys.readouterr().err
    assert "did not exit within 0.2 seconds; abandoning; failing scan" in err
    assert "final write" not in write_contexts
    assert not output.exists()


def test_perf_summary_marks_parallel_sums_and_worker_active(capsys):
    assert fpd.WORKER_SHUTDOWN_TIMEOUT_SECONDS == 60
    fpd._print_perf_summary(
        "fixture",
        {
            "wall_total": 10,
            "worker_count": 4,
            "t_exiftool": 30,
            "t_hashing": 5,
            "t_geolocate": 2,
            "worker_details": [
                {
                    "worker_id": 1,
                    "wall": 10,
                    "t_active": 7,
                    "t_exiftool": 6,
                    "t_hashing": 1,
                    "t_geolocate": 0,
                    "t_queue_push": 0.1,
                    "files": 3,
                    "errors": 0,
                },
                {
                    "worker_id": 2,
                    "wall": 9,
                    "t_active": 6,
                    "t_exiftool": 5,
                    "t_hashing": 1,
                    "t_geolocate": 0,
                    "t_queue_push": 0.1,
                    "files": 2,
                    "errors": 1,
                },
            ],
        },
    )

    out = capsys.readouterr().out
    assert "ExifTool (4w sum)" in out
    assert "Content hashing (4w sum)" in out
    assert "Geolocation (4w sum)" in out
    assert out.count("(sum across workers)") == 3
    assert "Worker active total" in out
    assert "Worker total" not in out
    assert "active=7.00s" in out
