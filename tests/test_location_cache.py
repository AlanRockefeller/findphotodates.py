import json
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


def _write_inventory(path, rows):
    path.write_text(
        "\n".join(
            [
                fpd.TSV_HEADER,
                "\t".join(fpd.TSV_COLUMNS),
                *rows,
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_photo(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"photo")
    return path


def _patch_exiftool(monkeypatch, responses_by_name):
    class FakeExifToolPersistent:
        def start(self):
            pass

        def stop(self):
            pass

        def batch_query(self, filepaths, fast2=False):
            return {
                fp: responses_by_name.get(Path(fp).name, (None, None, None))
                for fp in filepaths
            }

    monkeypatch.setattr(fpd, "ExifToolPersistent", FakeExifToolPersistent)


def test_load_cache_primes_geolocation_cache_from_resolved_locations(tmp_path):
    inventory = tmp_path / "inventory.tsv"
    _write_inventory(
        inventory,
        [
            "/photos/a.jpg\t\t1\t2\t37.871\t-122.273\tAlbany, Alameda County, California, United States\t",
            "/photos/b.jpg\t\t1\t2\t37.881\t-122.283\t37.881, -122.283\t",
        ],
    )

    fpd.load_cache(str(inventory))

    assert fpd._coord_cache[(37.87, -122.27)].startswith("Albany")
    assert (37.88, -122.28) not in fpd._coord_cache


def test_run_scan_reuses_sqlite_location_cache_across_tsvs(tmp_path, monkeypatch):
    _write_photo(tmp_path / "drive_a", "a.jpg")
    _write_photo(tmp_path / "drive_b", "b.jpg")
    _patch_exiftool(
        monkeypatch,
        {
            "a.jpg": ("2024:01:01 12:00:00", "37.871", "-122.273"),
            "b.jpg": ("2024:01:01 12:00:00", "37.872", "-122.274"),
        },
    )

    calls = {"count": 0}

    def fake_urlopen(req, timeout):
        calls["count"] += 1
        return FakeResponse(
            {
                "address": {
                    "city": "Albany",
                    "county": "Alameda County",
                    "state": "California",
                    "country": "United States",
                }
            }
        )

    monkeypatch.setattr(fpd.urllib.request, "urlopen", fake_urlopen)

    location_options = fpd.parse_location_args(
        location_cache_path=str(tmp_path / "location_cache.sqlite")
    )
    hash_options = fpd.parse_hash_args(hash_mode="off")

    assert fpd.run_scan(
        str(tmp_path / "drive_a"),
        str(tmp_path / "a.tsv"),
        {"jpg"},
        locate=True,
        quiet=True,
        hash_options=hash_options,
        location_options=location_options,
        min_image_size=0,
    )
    assert calls["count"] == 1

    fpd._coord_cache.clear()

    assert fpd.run_scan(
        str(tmp_path / "drive_b"),
        str(tmp_path / "b.tsv"),
        {"jpg"},
        locate=True,
        quiet=True,
        hash_options=hash_options,
        location_options=location_options,
        min_image_size=0,
    )
    assert calls["count"] == 1


def test_run_scan_uses_tsv_prime_when_location_cache_disabled(
    tmp_path, monkeypatch
):
    inventory = tmp_path / "drive_a.tsv"
    _write_inventory(
        inventory,
        [
            "/mnt/drive_a/a.jpg\t\t1\t2\t37.871\t-122.273\tAlbany, Alameda County, California, United States\t",
        ],
    )
    fpd.load_cache(str(inventory))

    _write_photo(tmp_path / "drive_b", "b.jpg")
    _patch_exiftool(
        monkeypatch,
        {
            "b.jpg": ("2024:01:01 12:00:00", "37.872", "-122.274"),
        },
    )

    calls = {"count": 0}

    def fake_urlopen(req, timeout):
        calls["count"] += 1
        return FakeResponse({"display_name": "Should Not Be Used"})

    def fail_open_location_cache(*args, **kwargs):
        raise AssertionError("--no-location-cache should not open SQLite")

    monkeypatch.setattr(fpd.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fpd, "open_location_cache", fail_open_location_cache)

    assert fpd.run_scan(
        str(tmp_path / "drive_b"),
        str(tmp_path / "drive_b.tsv"),
        {"jpg"},
        locate=True,
        quiet=True,
        hash_options=fpd.parse_hash_args(hash_mode="off"),
        location_options=fpd.parse_location_args(no_location_cache=True),
        min_image_size=0,
    )

    assert calls["count"] == 0
    assert "Albany, Alameda County, California, United States" in (
        tmp_path / "drive_b.tsv"
    ).read_text(encoding="utf-8")


def test_tsv_primed_geolocate_does_not_call_nominatim(tmp_path, monkeypatch):
    inventory = tmp_path / "inventory.tsv"
    _write_inventory(
        inventory,
        [
            "/photos/a.jpg\t\t1\t2\t37.871\t-122.273\tAlbany, Alameda County, California, United States\t",
        ],
    )
    fpd.load_cache(str(inventory))

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("Nominatim should not be called")

    monkeypatch.setattr(fpd.urllib.request, "urlopen", fail_urlopen)

    assert fpd.geolocate("37.872", "-122.274").startswith("Albany")


def test_sqlite_location_cache_is_reused_without_nominatim(tmp_path, monkeypatch):
    cache_path = tmp_path / "location_cache.sqlite"
    conn = fpd.open_location_cache(str(cache_path))
    calls = {"count": 0}

    def fake_urlopen(req, timeout):
        calls["count"] += 1
        return FakeResponse(
            {
                "address": {
                    "city": "San Francisco",
                    "county": "San Francisco County",
                    "state": "California",
                    "country": "United States",
                }
            }
        )

    monkeypatch.setattr(fpd.urllib.request, "urlopen", fake_urlopen)

    location = fpd.geolocate("37.771", "-122.419", location_conn=conn)
    assert location == "San Francisco, San Francisco County, California, United States"
    assert calls["count"] == 1
    conn.commit()
    conn.close()

    fpd._coord_cache.clear()
    conn = fpd.open_location_cache(str(cache_path))

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("Nominatim should not be called")

    monkeypatch.setattr(fpd.urllib.request, "urlopen", fail_urlopen)

    assert (
        fpd.geolocate("37.772", "-122.418", location_conn=conn)
        == "San Francisco, San Francisco County, California, United States"
    )
    conn.close()


def test_no_location_cache_still_uses_in_memory_cache(monkeypatch):
    calls = {"count": 0}

    def fake_urlopen(req, timeout):
        calls["count"] += 1
        return FakeResponse(
            {
                "address": {
                    "city": "Oakland",
                    "county": "Alameda County",
                    "state": "California",
                    "country": "United States",
                }
            }
        )

    monkeypatch.setattr(fpd.urllib.request, "urlopen", fake_urlopen)

    assert fpd.geolocate("37.801", "-122.271").startswith("Oakland")
    assert fpd.geolocate("37.802", "-122.272").startswith("Oakland")
    assert calls["count"] == 1


def test_default_cache_dir_platforms(monkeypatch, tmp_path):
    monkeypatch.setattr(fpd.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert fpd._default_cache_dir() == tmp_path / "xdg" / "findphotodates"

    monkeypatch.setattr(fpd.platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert (
        fpd._default_cache_dir()
        == tmp_path / "local" / "findphotodates" / "Cache"
    )


def test_windows_hash_cache_migration_moves_once(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    old_dir = home / ".cache" / "findphotodates"
    old_dir.mkdir(parents=True)
    old_path = old_dir / "hash_cache.sqlite"
    old_path.write_text("hash", encoding="utf-8")
    (old_dir / "hash_cache.sqlite-wal").write_text("wal", encoding="utf-8")

    new_path = tmp_path / "local" / "findphotodates" / "Cache" / "hash_cache.sqlite"

    monkeypatch.setattr(fpd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fpd.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(fpd, "DEFAULT_HASH_CACHE_PATH", new_path)

    assert fpd.migrate_default_hash_cache() is True
    assert "Moved existing hash cache" in capsys.readouterr().out
    assert not old_path.exists()
    assert new_path.read_text(encoding="utf-8") == "hash"
    assert Path(str(new_path) + "-wal").read_text(encoding="utf-8") == "wal"

    assert fpd.migrate_default_hash_cache() is False
    assert capsys.readouterr().out == ""


def test_windows_hash_cache_migration_ignores_sidecar_without_main_db(
    monkeypatch, tmp_path, capsys
):
    home = tmp_path / "home"
    old_dir = home / ".cache" / "findphotodates"
    old_dir.mkdir(parents=True)
    old_wal = old_dir / "hash_cache.sqlite-wal"
    old_wal.write_text("wal", encoding="utf-8")

    new_path = tmp_path / "local" / "findphotodates" / "Cache" / "hash_cache.sqlite"

    monkeypatch.setattr(fpd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fpd.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(fpd, "DEFAULT_HASH_CACHE_PATH", new_path)

    assert fpd.migrate_default_hash_cache() is False
    assert capsys.readouterr().out == ""
    assert old_wal.read_text(encoding="utf-8") == "wal"
    assert not new_path.exists()
    assert not Path(str(new_path) + "-wal").exists()
