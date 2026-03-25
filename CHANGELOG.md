# Changelog

## v1.4 (2026-03-24)

### Performance

- **Hashing OFF by default**: `--hash` now defaults to `off` instead of `sample`. Initial indexing no longer computes content hashes, making first scans of large drives significantly faster. Hashes can be added later with `--add-hashes`.
- **Persistent exiftool process**: Instead of spawning a new `exiftool` process per file (~50–100 ms startup overhead each), a single persistent process is kept alive using `exiftool -stay_open True`. This eliminates process-spawn overhead and is the largest performance improvement — expected 10–50x faster EXIF extraction on large scans.
- **Background file discovery**: A producer thread walks the directory tree in the background while the main thread processes files. Uses a bounded queue (10K items) for backpressure. Discovery and processing run concurrently — processing starts immediately without waiting for the full file list.
- **Lighter default sample hash**: Default `--sample-chunk-mib` changed from 1.0 MiB to 0.0625 MiB (64 KiB). The sample hash reads 3 × 64 KiB = 192 KiB per file instead of 3 × 1 MiB = 3 MiB. This is sufficient for its purpose (distinguishing files with the same name and size) and reduces I/O on large scans.

### New features

- **`--add-hashes` flag**: Fills in missing content hashes for an existing inventory TSV without re-running the full scan. Reads the TSV, computes hashes for rows with blank `content_hash` (where files still exist and match size/mtime), and writes back atomically. Uses the SQLite hash cache. Resumable — just re-run if interrupted.

### Changes

- **Two-phase progress reporting**:
  - While discovery is running: shows files discovered, files processed, rate, and elapsed time. No ETA (total is unknown).
  - After discovery completes: shows processed/total, percent complete, rate, and a rolling-window ETA (based on last 30 seconds of throughput).
- Checkpoint interval changed from every 100 files to every 500 files (or 5 minutes, whichever comes first) to reduce overhead from rewriting the full TSV.
- Progress line updates at most every 2 seconds to avoid flooding the terminal.

### check_photo_backups.py

- `--hash-mode` default changed from `compute` to `auto` — target hashes are only computed when inventories contain hashes to compare against. Avoids wasted work when inventories have no hashes.
- `--sample-chunk-mib` default changed from `1.0` to `0.0625` to match findphotodates.py.
- Blank hashes in inventories are fully supported — files without hashes fall through to strong/weak/name matching (no false matches, no errors).

### Compatibility

- The `--sample-chunk-mib 1.0` flag can still be passed explicitly to get the old hash behavior.
- Existing hash cache entries are naturally invalidated (cache key includes chunk size), so the first run after upgrading will recompute hashes with the new defaults. No manual cache cleanup needed.
- Existing inventory TSV files remain fully compatible as resume caches. New runs will pick up where they left off.
- The saved-scan config (`--save` / `--scan`) will use the new defaults unless the config was saved with explicit hash parameters.

## v1.3 (2026-02-11)

- Initial tracked version with content hashing, hash caching, TSV inventory format, and checkpoint/resume support.
