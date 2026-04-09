# findphotodates.py

**Version 1.5 (2026-04-05)** — Alan Rockefeller

A filesystem inventory tool that indexes all files and extracts EXIF dates and GPS from media files.

## Table of Contents
1. [What is this?](#what-is-this)
2. [Features](#features)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Content Hashing](#content-hashing)
6. [Recommended Workflow](#recommended-workflow)
7. [Usage (findphotodates.py)](#usage-findphotodatespy)
8. [Backup Checking (check_photo_backups.py)](#backup-checking-check_photo_backupspy)
9. [Troubleshooting](#troubleshooting)
10. [License](#license)

## What is this?

`findphotodates.py` recursively indexes all files in a directory tree, producing a TSV inventory with filepath, size, mtime, and content hash for every file. For media files (photos, videos, RAW images), it also extracts EXIF creation dates and GPS coordinates via ExifTool. The result is a complete filesystem inventory that doubles as a media date catalog.

## Features

- **Indexes all file types** by default — documents, archives, media, everything
- Extracts creation dates and GPS from media files via ExifTool (photos, videos, RAW formats)
- Only sends ExifTool-eligible file types to ExifTool — other files get fast filesystem-only indexing
- Works with common photo formats (JPG, JPEG, NEF, ORF, CR2, CR3, ARW, DNG, HEIC, HEIF, AVIF, and more)
- Supports popular video formats (MP4, MOV, AVI, MKV, WMV, FLV, WEBM, M4V, 3GP, MTS, etc.)
- Recursively searches directories
- Optional geolocation data extraction (when GPS coordinates are available)
- Provides useful summaries of your collection
- Estimates processing time for large collections
- Creates a detailed TSV file with a complete inventory of your files
- **Content hashing** - Generates unique fingerprints (fast sample hash or full-file hash) for backup and deduplication safety
- **Smart caching** - Uses filesystem metadata and a SQLite hash cache to skip re-processing unchanged files
- **Cross-platform cache reuse** - Inventories created under WSL get cache hits when re-read from native Windows and vice versa
- Saves progress every 15 minutes and handles errors from disconnecting drives gracefully. Picks up where it left off.
- **Batched ExifTool** - Queries multiple files per ExifTool round-trip for faster metadata extraction on large scans
- **Fast directory walking** - Uses `os.scandir()` instead of `os.walk()` for faster file discovery
- **Detailed timing breakdown** - Shows where time is actually spent (discovery, exiftool, stat, hashing, etc.)

## Requirements

- Python 3.x
- **ExifTool** must be installed and available on your system PATH. Run `exiftool -ver` to verify. This applies on Linux, macOS, WSL, **and** native Windows.
- (Optional) `blake3` library for significantly faster hashing (`pip install blake3`)
- (Optional) `exifread` library for `check_photo_backups.py` JPEG date reading (`pip install exifread`)

## Installation

1. **Install ExifTool:**
   - **macOS:** `brew install exiftool`
   - **Ubuntu/Debian / WSL:** `sudo apt-get install libimage-exiftool-perl`
   - **Windows:** Download from [ExifTool website](https://exiftool.org/) and add to PATH. Verify with `exiftool -ver` in PowerShell or Command Prompt.
2. **Download scripts:** Download `findphotodates.py` and `check_photo_backups.py`.
3. **Make executable (Linux/macOS/WSL):** `chmod +x findphotodates.py check_photo_backups.py`
   On Windows, run with `python findphotodates.py`.

## Content Hashing

`findphotodates.py` includes robust content hashing to help with backup verification and deduplication.

### Hashing Modes

- **`off` (Default)**: No hashing during indexing. Fast initial scans. Hashes can be added later with `--add-hashes`.
- **`sample`**: Creates a fast fingerprint by hashing the first/last and evenly spaced chunks of the file (plus the file size). Use `--hash sample` to enable.
- **`full`**: Hashes the entire file content. Slower but provides a cryptographically strong guarantee of file identity.

### Hash Caching

Both scripts use SQLite caches to avoid repeated hashing:
- `findphotodates.py` uses a cache at `~/.cache/findphotodates/hash_cache.sqlite` (configurable via `--hash-cache`).
- `check_photo_backups.py` uses a cache at `~/.cache/check_photo_backups/fingerprints.sqlite` (configurable via `--fingerprint-cache`).

**Tradeoff Note:** Cache keys include a normalized file path to prevent false identity carryover. WSL mount paths (`/mnt/f/...`) and Windows drive paths (`F:\...`) are normalized to the same key, so switching between WSL and native Windows does not cause cache misses. However, if you move a file to a different directory or rename it, the file must be re-hashed once. First runs on a collection will be slower while hashes are computed. SQLite caches can grow over time; simply delete the `.sqlite` file to reclaim space.

### Algorithms

- Defaults to `blake3` if the library is installed (exceptionally fast).
- Otherwise, defaults to `blake2b` (128-bit) for sample hashes and `sha256` for full hashes.
- Use `--hash-algo` to explicitly override. Note: `--hash full` requires `blake3` or `sha256`.

## Recommended Workflow

1.  **Generate Inventories:** Generate a fast inventory for each backup drive (no hashing by default).
    ```bash
    # Linux / WSL
    ./findphotodates.py --directory /mnt/f/Photos -o f_inventory.tsv
    ./findphotodates.py --directory /mnt/o -o o_inventory.tsv

    # Windows (PowerShell)
    python findphotodates.py --directory F:\Photos -o f_inventory.tsv
    python findphotodates.py --directory O:\ -o o_inventory.tsv
    ```
    Inventories are cross-platform — a TSV created under WSL gets cache hits when re-read from native Windows and vice versa.

2.  **Add Hashes (when needed):** Before verifying backups, add content hashes to enable verified matching.
    ```bash
    ./findphotodates.py -o f_inventory.tsv --add-hashes
    ./findphotodates.py -o o_inventory.tsv --add-hashes
    ```
3.  **Verify Target Folder:** Use `check_photo_backups.py` to compare a staging area against those inventories.
    ```bash
    ./check_photo_backups.py --target "/home/user/Staging" --inventories "f_inventory.tsv,o_inventory.tsv"
    ```
4.  **Review and Cleanup:** Confirmed files are listed in `safe_to_delete.txt`. You can also use the generated `delete_safe.sh` script for semi-automated removal.

## Usage (findphotodates.py)

### Basic Usage
```bash
# Index all files in a directory (default)
./findphotodates.py --directory "/path/to/your/files"

# Index only media files (photos + videos)
./findphotodates.py --directory "/path/to/your/media" --only-media
```

### Examples
```bash
# Linux / WSL — full inventory of an external drive
./findphotodates.py --directory /mnt/o -o o_inventory.tsv --save

# Windows (PowerShell) — same drive, same inventory file gets cache hits
python findphotodates.py --directory O:\ -o o_inventory.tsv

# Scan for videos only
./findphotodates.py --video -o my_videos.tsv

# Scan and geolocate photos
./findphotodates.py --only-photos --locate

# Save a scan configuration for an external drive
./findphotodates.py --directory /mnt/f -o f_inventory.tsv --save

# Write legacy format output
./findphotodates.py --directory /mnt/f --old-format -o f_inventory.txt
```

### Command Line Options
```text
usage: findphotodates.py [-h] [--directory DIRECTORY] [-o OUTPUT] [-q] [--debug]
                        [--only-media] [--video] [--only-photos] [--extension EXTENSION]
                        [--locate] [--hash {sample,full,off}] [--sample-chunks INT]
                        [--sample-chunk-mib FLOAT] [--hash-algo {blake3,blake2b,sha256}]
                        [--hash-cache PATH] [--no-hash-cache] [--hash-exts LIST]
                        [--old-format] [--save] [--scan]
```

| Option | Description |
|--------|-------------|
| `--directory` | Directory to search (default: current directory) |
| `-o`, `--output`, `--out` | Output file path (default: photo.dates.tsv or .txt) |
| `-q`, `--quiet` | Run quietly without printing progress |
| `--debug` | Run in debug mode with verbose output |
| `--only-media` | Index only media files (photos + videos) instead of all files |
| `--video` | Index only video files |
| `--only-photos` | Index only photo files |
| `--extension` | Index only files with a specific extension (e.g., "pdf") |
| `--locate` | Try to extract location data (if available) |
| `--hash` | Hashing mode: `off` (default), `sample`, or `full` |
| `--add-hashes` | Fill in missing hashes for an existing inventory TSV |
| `--sample-chunks` | Number of chunks for sample hash (default: 3) |
| `--sample-chunk-mib` | Size of each chunk in MiB (default: 0.0625 = 64 KiB) |
| `--hash-algo` | Override hash algorithm (`blake3`, `blake2b`, or `sha256`) |
| `--hash-cache` | Path to SQLite hash cache |
| `--no-hash-cache` | Disable hash cache |
| `--hash-exts` | Comma-separated extensions to hash (default: hash all) |
| `--old-format` | Write legacy output (`./path: YYYY:MM:DD`) without hashes |
| `--save` | Save current scan configuration for later use with --scan |
| `--scan` | Run all saved scan configurations |

## Backup Checking (check_photo_backups.py)

`check_photo_backups.py` computes target-side fingerprints and compares them against your backup inventories.

**Important:** If any inventory was generated with `--hash full`, the check script will automatically compute full hashes for targets to ensure verified matching. Metadata-only matches (name/size/date) are NOT considered "safe to delete" by default unless `--allow-strong-without-hash` is used.

```bash
# Verify that photos in a target folder exist in your inventories
./check_photo_backups.py --target "/path/to/verify" --inventories "inv1.tsv,inv2.tsv" --delete-script delete_safe.sh
```

### Command Line Options
```text
usage: check_photo_backups.py [-h] --target TARGET [--inventories LIST]
                             [--hash-mode {auto,compute,off}]
                             [--hash-algo {auto,blake3,blake2b,sha256}]
                             [--out-csv FILE] [--missing-list FILE]
                             [--safe-list FILE] [--needs-hash-list FILE]
                             [--no-verified-match-list FILE]
                             [--hash-config-mismatch-list FILE]
                             [--delete-script FILE] [--drive-map LIST]
                             [--sort] [--fingerprint-cache PATH]
                             [--no-fingerprint-cache]
                             [--allow-strong-without-hash]
                             [--allow-weak-without-hash]
                             [--allow-hash-config-mismatch]
                             [-q] [--debug]
```

| Option | Description |
|--------|-------------|
| `--target` | Folder to verify (staging area) |
| `--inventories` | Comma-separated inventory files from backup drives |
| `--hash-mode` | Hashing mode: `auto` (default — compute only when inventories have hashes), `compute`, or `off` |
| `--out-csv` | Path to the detailed CSV report |
| `--safe-list` | List of confirmed safe-to-delete files |
| `--needs-hash-list` | Files that matched by metadata but were not hashed |
| `--delete-script` | Generates a shell script to delete safe files |
| `--drive-map` | Map drive labels to roots (e.g., c=/mnt/c,d=/mnt/d) |
| `--fingerprint-cache` | Path to the target-side fingerprint cache |
| `--allow-strong-without-hash` | Mark strong matches (name+size+date) as safe |
| `--allow-weak-without-hash` | Mark weak matches (name+size) as safe |
| `-q`, `--quiet` | Suppress normal output |

## Inventory Output Format (findphotodates.py)

The script generates a TSV file with metadata headers describing the scan parameters. (Note: Columns are separated by tabs; spacing below is for display only).
```text
# inventory_root=/Users/alan/Photos
# hash_mode=sample
# content_hash_format=samplehash_v1
# samplehash_v1 algo=blake3 chunks=3 chunk_mib=0.0625
# Generated by findphotodates.py
filepath	date_taken	size_bytes	mtime_ns	gps_lat	gps_lon	location	content_hash
/Users/alan/Photos/IMG_2354.jpg	2023:06:12 15:42:33	2456789	1686582153000000000	37.7749	-122.4194		a3b1c2d3...
```

## Reports Output (check_photo_backups.py)

The detailed CSV report (`backup_check_report.csv`) provides safety and matching information:
- `safety`: Security status (e.g., `verified_hash`, `strong`, `missing`, `hash_config_mismatch`).
- `match_type`: The level of matching found (e.g., `verified_hash`, `strong`, `weak`).
- `safe_to_delete`: `yes` if the file meets safety criteria.

Output lists are generated for different states:
- `safe_to_delete.txt`: Confirmed files.
- `needs_hash.txt`: Metadata matches that weren't hashed (rerun with `--hash-mode compute`).
- `no_verified_match.txt`: Hashed files where the digest wasn't found in any inventory.

## Troubleshooting

- **ExifTool missing:** Ensure `exiftool` is installed and in your PATH. Run `exiftool -ver` to check. On Windows, make sure the ExifTool directory is in your system PATH environment variable.
- **No dates found:** Some files may lack EXIF data. The script will leave the date blank.
- **Slow performance:** First runs compute hashes. Install the `blake3` library for best performance.
- **Drive disconnects:** If a drive is unplugged, progress is saved. Reconnect and run again to resume.
- **Cross-platform cache misses:** Inventory cache keys normalize WSL (`/mnt/f/...`) and Windows (`F:\...`) paths to a shared form. If you still see cache misses switching environments, ensure you are scanning the same physical drive.

## License

This tool is licensed under the GNU General Public License v3.0 (GPL-3.0).

## Contributing

Found a bug? Have a suggestion? Feel free to open an issue or submit a pull request.
