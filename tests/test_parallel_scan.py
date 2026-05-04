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

        def batch_query(self, filepaths):
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
    )
    assert fpd.run_scan(
        str(root),
        str(multi),
        None,
        quiet=True,
        hash_options=hash_options,
        workers=4,
    )

    assert multi.read_bytes() == single.read_bytes()


def test_geolocate_lock_serializes_nominatim_requests(monkeypatch):
    timestamps = []
    timestamps_lock = threading.Lock()
    start_event = threading.Event()
    errors = []

    def fake_urlopen(req, timeout):
        with timestamps_lock:
            timestamps.append(time.monotonic())
        return FakeResponse({"display_name": "Serialized Place"})

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

        def batch_query(self, filepaths):
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
        )
    finally:
        timer.cancel()

    assert result is False
    cache = fpd.load_cache(str(output))
    assert 0 <= len(cache) < 30


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

        def batch_query(self, filepaths):
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


def test_normal_shutdown_timeout_warns_and_returns_quickly(
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
            time.sleep(2)

        def batch_query(self, filepaths):
            return {
                fp: ("2024:01:01 10:00:00", "37.871", "-122.273")
                for fp in filepaths
            }

    monkeypatch.setattr(fpd, "ExifToolPersistent", HangingStopExifTool)
    output = tmp_path / "out.tsv"

    started = time.monotonic()
    assert fpd.run_scan(
        str(root),
        str(output),
        {"jpg"},
        quiet=True,
        hash_options=fpd.parse_hash_args(hash_mode="off"),
        workers=1,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert "did not exit within 0.2 seconds; abandoning" in capsys.readouterr().err


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
