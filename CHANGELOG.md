# Changelog

## v1.6.1 (2026-09-29)

- Much faster scans of backup drives. Photos and videos that were already scanned on another drive (same name, size and modification time, as copies keep) now take their date and GPS from that drive's list instead of being read again. Every list in the same folder as the one being written is used, so in the menu and GUI that's all your drive lists. A partial scan of the Erowid drive found 99% of its photos already listed for other drives. Only list entries with a date or GPS are reused; photos whose earlier scan found nothing are read again. `--no-reuse-other-lists` turns this off.
- Hard drives are read two files at a time instead of four. With one set of heads (and, on many USB enclosures, one command at a time), more readers just make the drive seek back and forth between folders. SSDs, network shares and drives on Windows or WSL are unaffected.
- Stopping a scan is now immediate. Before, it waited 5 seconds for each ExifTool process to finish its batch, which on a slow drive can take minutes, then warned that the workers "did not exit". Batches in progress are now dropped and read again when the scan resumes.

## v1.6.0 (2026-09-28)

- Added a desktop GUI for the interactive workflow, with connected drive selection, live scan progress, resumable stop, list browsing, and drive health checks.
- Added a collapsible Advanced section with the menu's scan settings, folder scans, backup checks, list import and location controls, and equivalent command copy.
- Added Linux, macOS, and Windows GUI build configuration, a release workflow, and `build-release.sh`.
- Stopping a scan no longer shrinks the file list. Progress saves (every 15 minutes, on Ctrl-C, or when a drive disconnects) now keep the previous list's entries for files the scan hasn't reached yet. Before, stopping partway through dropped them until the next full scan.
- Files and folders that can't be read are now reported instead of silently skipped. Each one is shown as it happens (up to 20; the rest go in the end summary), the progress line keeps a running count of unreadable files, and each scan ends with a summary, also when stopped with Ctrl-C, and the full list is saved in a `Read errors` folder next to the file lists. Entries for files in folders that couldn't be read are kept from the previous list rather than reported as removed. ExifTool's "Error reading file" results are included.
- One unreadable file no longer stops the whole scan as a "disconnected drive"; the scan only stops if the drive itself has gone.
- When a drive is struggling (several read errors, or reading becomes extremely slow), the scan warns you that the drive may be failing, suggests stopping to copy your files off, and reads one file at a time instead of four so it doesn't hammer damaged areas.
- Before scanning a drive whose last health check found bad sectors or read errors, or rated it FAILING, the program warns you and asks whether to continue (in a terminal), and reads one file at a time if you go ahead.
- The health log now records each drive's volume IDs, so warnings find the right drive even if it's renamed. Logs from earlier versions are upgraded automatically.
- The health check lists drives in the same order as the main menu.
- Fix: a drive holding copies of another drive's folders could be matched to that other drive's file list by its contents, and scanning it would have overwritten that list. Lists that record a different drive's serial number are no longer matched by contents, and the menu and GUI refuse to scan a drive into a list recorded for another drive.
- Fix: a small file list could be matched to the wrong drive by its contents, because one matching file was counted several times.
- `--health` no longer requires ExifTool, which it doesn't use.
- Health checks now find drives formatted without partitions (mounted straight from the disk, as some memory cards and USB sticks are).
- The GUI's drive health dialog has a Cancel button; closing it no longer starts a quick check of every drive.
- Fix: in the GUI, two selected drives with the same name and no serial number were given the same new file list, so the second scan overwrote the first.
- `build-release.sh` stops if it can't check GitHub for an existing tag, instead of assuming the tag is free.
- GUI startup now names missing Tk or CustomTkinter and gives the matching installation steps.
- Enlarged GUI text on Linux, reduced the requested window size, and added hover explanations for Advanced controls. Connected drives now appear in tables in both interfaces, and lists updated today show "Today" with the update time.

## v1.5.3 (2026-09-28)

- New menu. Run the program with no options and it shows your connected drives and their file lists. Pick a drive to update its list, or to make one if it doesn't have one yet. `u` updates every connected drive. Everything else is under *Advanced options*, and each option explains what it does and why you might want it. `--interactive` opens the menu explicitly; running with any other options works as before.
- File lists now live in Documents. They're kept in `Documents/findphotodates` (on WSL, the Windows Documents folder, so Windows and WSL share them) and named after their drive, e.g. `Sierra Club (5F61-DDF5).tsv`. The menu offers to move lists from earlier versions (like `~/f:photo.taken.dates.txt`) there and rename them.
- Drives are recognised wherever they're mounted. Whole-drive scans record the drive's name and serial number in the list. The menu uses that, or checks the drive's contents for older lists, to pair each list with its drive, even if it was `L:` or `/mnt/l` before and is `/run/media/you/Sierra Club` now.
- Much faster rescans after a drive moves. Previously, a drive mounted at a new location didn't match any entry in its old list, so every photo and video went back through ExifTool. Now the old entries are mapped to the new location. Rescanning the Sierra Club drive went from about 3½ hours to about 11 minutes. This only happens when the old location no longer exists, so scanning a different folder into an existing list can't borrow the wrong data.
- Each rescan now ends with a report of what changed since the last scan: new files, changed files (different size or date), files that were moved or renamed (same size and date, new location), and files that are gone. A few changes are listed one by one; many are grouped by folder. This appears both in the menu and on the command line.
- Drive health checks, from the main menu (`h`) or with `--health` on the command line. The quick check reads each drive's own health data (bad sectors, errors, temperature, age, SSD wear, last self-test) and explains any warning in plain language. The extended check also runs each drive's full-surface self-test, or for drives that can't test themselves, like SD cards, reads every file and lists any that can't be read. It recommends installing smartmontools if it's missing and asks for your password through sudo when it needs administrator access.
- Health checks are logged to `Drive health log.tsv` in the file lists folder: one row per check with the drive's serial number, result and key numbers. Each check points out anything that got worse since that drive's previous one. The health menu shows the last known health of drives that aren't connected, and `h` there (or `--health history`) shows each drive's history.
- Photos and videos that were moved or copied to another folder aren't read by ExifTool again. If a file has the same name, size and modification time as one in the previous list, its date and GPS are reused. Renamed or edited files are still read.
- In the main menu you can pick several drives at once, for example `3 4` or `2-4`, and they're updated one after another.
- Fix: updating a list that used Windows-style paths (`J:\...`) from a Linux mount point no longer writes broken paths like `\run\media\...`.
- Backup checker: `check_photo_backups.py` now finds the lists in `Documents/findphotodates` automatically, and can be run from the menu. Its reports go in a `Backup checks` folder next to your lists.
- Added tests for the new drive-matching, naming and import code.

## v1.5.2 (2026-05-03)

- Fix a bug where Windows junctions can cause an infinite loop
- Fix a minor display bug so ANSI color codes are only sent if the output is a terminal
- Add a persistent SQLite reverse-geocoding cache so `--locate` can reuse resolved GPS coordinates across inventories and avoid repeated Nominatim requests
- Run ExifTool batches through configurable parallel worker threads with `--workers` (default: 4)
- Speed up safe image metadata extraction by using ExifTool `-fast2` for JPEG/PNG/WebP batches
- Add `--min-image-size` to skip ExifTool for tiny JPG/JPEG/PNG/WebP files that rarely contain useful EXIF, while still indexing them with filesystem metadata
- Fail scans when ExifTool worker startup or shutdown fails instead of silently treating unavailable or stuck workers as successful blank metadata extraction

## v1.5.1 (2026-04-15)

- Made sure it works well in both Windows and Linux. On my WSL system, it works a whole lot faster when running in Windows vs. a drive mounted via WSL.
- Added some tests and configured them to work with Pytest.

### Bug Fixes

- Cross-platform character normalization: Fixed cache mismatch issues caused by Windows vs WSL representing certain "funny characters" (like quotes) differently in filenames.

### Features

- Path Style Options: Added `--linux` and `--windows` flags to specify the path format in the output inventory. Defaults to auto-detection (preserves existing inventory style, then infers from directory path, then falls back to platform default).

## v1.5 (2026-04-05)

### Performance

- Batched ExifTool queries: Instead of querying one file at a time, files are now sent to exiftool in batches (default 50) using `-json` output. This cuts per-file overhead on large scans. Tunable via `EXIFTOOL_BATCH_SIZE` constant.
- `os.scandir()`-based discovery: Replaced `os.walk()` with a recursive `os.scandir()` walker. This is faster because `scandir()` returns file type info from the directory entry itself, avoiding extra `stat()` calls during the walk.
- Symlink-aware `realpath()` skip: `realpath()` is now only called for symlinks (detected during `scandir()` traversal). Regular files use `os.path.abspath()` instead. On trees with few symlinks this eliminates hundreds of thousands of unnecessary syscalls.
- Larger discovery queue: Queue size increased from 10K to 50K entries, reducing producer thread blocking on large scans where the consumer processes files faster than discovery can fill the queue.

### Timing statistics

- Detailed timing breakdown: The `--debugperformance` summary now reports more granular and accurately named buckets:
  - `Inventory cache load`: time to read and parse the existing TSV
  - `Discovery (wall)`: total producer thread lifetime
  - `queue put-wait`: time the producer spent blocked waiting for queue space
  - `realpath() calls`: time spent resolving symlinks only
  - `stat() calls`: time spent in `os.stat()` on the consumer side
  - `TSV cache lookups`: time spent checking the in-memory cache dict
  - `ExifTool batches`: time waiting for exiftool batch responses
  - `Content hashing`, `Checkpoints`, `Final write`: unchanged
- The old "File discovery" label (which measured producer thread lifetime, not walk time) is renamed to "Discovery (wall)" and supplemented with the queue-wait sub-timing.

### Scan model changes

- All files indexed by default: The tool now indexes every file in the directory tree, not just media files. Non-media files get filesystem metadata (filepath, size, mtime, content hash) but skip ExifTool. Use `--only-media` to get the old behavior of indexing only photos and videos.
- New `--only-media` flag: Limits indexing to photo + video extensions (JPG, JPEG, NEF, ORF, MP4, MOV, AVI, MKV, WMV, FLV, WEBM, M4V, 3GP). Equivalent to the old default behavior.
- ExifTool is now extension-gated: Only files with extensions likely to contain useful EXIF dates or GPS are sent to ExifTool. The allowlist (`EXIFTOOL_EXTENSIONS`) covers common photo RAW formats (CR2, CR3, ARW, DNG, RW2, RAF, SRW, PEF, X3F), image formats with EXIF support (TIF, TIFF, HEIC, HEIF, AVIF, WEBP, PNG, JXL), and video containers (MTS, M2TS, TS, VOB, MPG, MPEG) in addition to the original photo/video sets. All other files are indexed with filesystem metadata only.
- Files without EXIF dates are now included in the inventory: Previously, files where ExifTool found no date were omitted from the TSV. Now all discovered files are listed, with an empty `date_taken` field if no date was extracted. This means the inventory is a complete filesystem index.
- Summary updated: The end-of-scan summary now shows total files indexed vs. files with EXIF dates, and uses a broader label when non-media files are present.
- `--only-photos`, `--video`, and `--extension` continue to work as before but are now explicit opt-ins for narrowing the scope. The priority is: `--extension` > `--video` > `--only-photos` > `--only-media` > all files.

### Compatibility

- Existing inventory TSV files and SQLite hash caches still work. Cache keys use the same path for non-symlink files (`abspath == realpath` when no symlinks are involved).
- The `--debugperformance` flag and the automatic timing display for scans over 1 hour both use the new breakdown format.
- Saved scan configs (`--save`/`--scan`) from v1.4 will continue to use whatever extension list was saved. New saves without a filter flag will save `extensions: null` (all files).

## v1.4 (2026-03-24)

### Performance

- Hashing OFF by default: `--hash` now defaults to `off` instead of `sample`. Initial indexing no longer computes content hashes, so first scans of large drives are much faster. Hashes can be added later with `--add-hashes`.
- Persistent exiftool process: Instead of spawning a new `exiftool` process per file (about 50 to 100 ms of startup time each), a single persistent process is kept alive using `exiftool -stay_open True`. This removes the startup cost and is the biggest speedup in this release: EXIF extraction on large scans should be 10 to 50 times faster.
- Background file discovery: A producer thread walks the directory tree in the background while the main thread processes files. Uses a bounded queue (10K items) for backpressure. Discovery and processing run at the same time, so processing starts without waiting for the full file list.
- Lighter default sample hash: Default `--sample-chunk-mib` changed from 1.0 MiB to 0.0625 MiB (64 KiB). The sample hash reads 3 × 64 KiB = 192 KiB per file instead of 3 × 1 MiB = 3 MiB. This is sufficient for its purpose (distinguishing files with the same name and size) and reduces I/O on large scans.

### New features

- `--add-hashes` flag: Fills in missing content hashes for an existing inventory TSV without re-running the full scan. Reads the TSV, computes hashes for rows with blank `content_hash` (where files still exist and match size/mtime), and writes back atomically. Uses the SQLite hash cache. If interrupted, run it again to resume.

### Changes

- Two-phase progress reporting:
  - While discovery is running: shows files discovered, files processed, rate, and elapsed time. No ETA (total is unknown).
  - After discovery completes: shows processed/total, percent complete, rate, and a rolling-window ETA (based on last 30 seconds of throughput).
- Checkpoint interval changed from every 100 files to every 500 files (or 5 minutes, whichever comes first) to reduce overhead from rewriting the full TSV.
- Progress line updates at most every 2 seconds to avoid flooding the terminal.

### check_photo_backups.py

- `--hash-mode` default changed from `compute` to `auto`: target hashes are only computed when inventories contain hashes to compare against. Avoids wasted work when inventories have no hashes.
- `--sample-chunk-mib` default changed from `1.0` to `0.0625` to match findphotodates.py.
- Blank hashes in inventories are supported: files without hashes fall through to strong/weak/name matching (no false matches, no errors).

### Compatibility

- The `--sample-chunk-mib 1.0` flag can still be passed explicitly to get the old hash behavior.
- Existing hash cache entries are invalidated (the cache key includes the chunk size), so the first run after upgrading will recompute hashes with the new defaults. No manual cache cleanup needed.
- Existing inventory TSV files still work as resume caches. New runs will pick up where they left off.
- The saved-scan config (`--save` / `--scan`) will use the new defaults unless the config was saved with explicit hash parameters.

## v1.3 (2026-02-11)

- Initial tracked version with content hashing, hash caching, TSV inventory format, and checkpoint/resume support.
