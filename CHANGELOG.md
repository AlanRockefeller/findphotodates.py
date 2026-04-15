# Changelog

## v1.5.1 (2026-04-09)

- Made sure it works well in both Windows and Linux. On my WSL system, it works a whole lot faster when running in Windows vs. a drive mounted via WSL.
- Added some tests and configured them to work with Pytest.

### Bug Fixes

- **Cross-platform character normalization**: Fixed cache mismatch issues caused by Windows vs WSL representing certain "funny characters" (like quotes) differently in filenames.

### Features

- **Path Style Options**: Added `--linux` and `--windows` flags to specify the path format in the output inventory. Defaults to auto-detection (preserves existing inventory style, then infers from directory path, then falls back to platform default).

## v1.5 (2026-04-05)

### Performance

- **Batched ExifTool queries**: Instead of querying one file at a time, files are now sent to exiftool in batches (default 50) using `-json` output. This reduces per-file IPC overhead significantly on large scans. Tunable via `EXIFTOOL_BATCH_SIZE` constant.
- **`os.scandir()`-based discovery**: Replaced `os.walk()` with a recursive `os.scandir()` walker. This is faster because `scandir()` returns file type info from the directory entry itself, avoiding extra `stat()` calls during the walk.
- **Symlink-aware `realpath()` skip**: `realpath()` is now only called for symlinks (detected during `scandir()` traversal). Regular files use `os.path.abspath()` instead. On trees with few symlinks this eliminates hundreds of thousands of unnecessary syscalls.
- **Larger discovery queue**: Queue size increased from 10K to 50K entries, reducing producer thread blocking on large scans where the consumer processes files faster than discovery can fill the queue.

### Timing statistics

- **Detailed timing breakdown**: The `--debugperformance` summary now reports more granular and accurately named buckets:
  - `Inventory cache load` — time to read and parse the existing TSV
  - `Discovery (wall)` — total producer thread lifetime
  - `queue put-wait` — time the producer spent blocked waiting for queue space
  - `realpath() calls` — time spent resolving symlinks only
  - `stat() calls` — time spent in `os.stat()` on the consumer side
  - `TSV cache lookups` — time spent checking the in-memory cache dict
  - `ExifTool batches` — time waiting for exiftool batch responses
  - `Content hashing`, `Checkpoints`, `Final write` — unchanged
- The old "File discovery" label (which measured producer thread lifetime, not walk time) is renamed to "Discovery (wall)" and supplemented with the queue-wait sub-timing.

### Scan model changes

- **All files indexed by default**: The tool now indexes every file in the directory tree, not just media files. Non-media files get filesystem metadata (filepath, size, mtime, content hash) but skip ExifTool. Use `--only-media` to get the old behavior of indexing only photos and videos.
- **New `--only-media` flag**: Limits indexing to photo + video extensions (JPG, JPEG, NEF, ORF, MP4, MOV, AVI, MKV, WMV, FLV, WEBM, M4V, 3GP). Equivalent to the old default behavior.
- **ExifTool is now extension-gated**: Only files with extensions likely to contain useful EXIF dates or GPS are sent to ExifTool. The allowlist (`EXIFTOOL_EXTENSIONS`) covers common photo RAW formats (CR2, CR3, ARW, DNG, RW2, RAF, SRW, PEF, X3F), image formats with EXIF support (TIF, TIFF, HEIC, HEIF, AVIF, WEBP, PNG, JXL), and video containers (MTS, M2TS, TS, VOB, MPG, MPEG) in addition to the original photo/video sets. All other files are indexed with filesystem metadata only.
- **Files without EXIF dates are now included in the inventory**: Previously, files where ExifTool found no date were omitted from the TSV. Now all discovered files are listed, with an empty `date_taken` field if no date was extracted. This means the inventory is a complete filesystem index.
- **Summary updated**: The end-of-scan summary now shows total files indexed vs. files with EXIF dates, and uses a broader label when non-media files are present.
- `--only-photos`, `--video`, and `--extension` continue to work as before but are now explicit opt-ins for narrowing the scope. The priority is: `--extension` > `--video` > `--only-photos` > `--only-media` > all files.

### Compatibility

- Existing inventory TSV files and SQLite hash caches remain fully compatible. Cache keys use the same path for non-symlink files (`abspath == realpath` when no symlinks are involved).
- The `--debugperformance` flag and the automatic timing display for scans over 1 hour both use the new breakdown format.
- Saved scan configs (`--save`/`--scan`) from v1.4 will continue to use whatever extension list was saved. New saves without a filter flag will save `extensions: null` (all files).

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
