# findphotodates.py

**Version 1.4 (2026-03-24)** — Alan Rockefeller

A versatile Python tool for extracting and organizing dates from your media files.

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

Ever found yourself with thousands of photos and videos scattered across your drives, wondering when they were actually taken? `findphotodates.py` is the solution. This script scans your photos and videos, extracts the dates they were created (from EXIF data), and organizes this information in a neat text file.

## Features

- Works with common photo formats (JPG, JPEG, NEF, ORF)
- Supports popular video formats (MP4, MOV, AVI, MKV, etc.)
- Recursively searches directories
- Extracts creation dates from EXIF data
- Optional geolocation data extraction (when GPS coordinates are available)
- Provides useful summaries of your media collection
- Estimates processing time for large collections
- Creates a detailed TSV file with a list of your media and when it was created
- **Content hashing** - Generates unique fingerprints (fast sample hash or full-file hash) for backup and deduplication safety
- **Smart caching** - Uses filesystem metadata and a SQLite hash cache to skip re-processing unchanged files
- Saves progress every 5 minutes or 500 files - and handles errors from disconnecting drives gracefully. Picks up where it left off.

## Requirements

- Python 3.x
- **ExifTool** (required for `findphotodates.py`; must be in PATH)
- (Optional) `blake3` library for significantly faster hashing (`pip install blake3`)
- (Optional) `exifread` library for `check_photo_backups.py` JPEG date reading (`pip install exifread`)

## Installation

1. **Install ExifTool:**
   - **macOS:** `brew install exiftool`
   - **Ubuntu/Debian:** `sudo apt-get install libimage-exiftool-perl`
   - **Windows:** Download from [ExifTool website](https://exiftool.org/) and add to PATH
2. **Download scripts:** Download `findphotodates.py` and `check_photo_backups.py`.
3. **Make executable:** `chmod +x findphotodates.py check_photo_backups.py`

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

**Tradeoff Note:** Cache keys include the absolute file path to prevent false identity carryover. This means if you move a file or change its drive mount point (e.g., changing drive letters on Windows), it must be re-hashed once. First runs on a collection will be slower while hashes are computed. SQLite caches can grow over time; simply delete the `.sqlite` file to reclaim space.

### Algorithms

- Defaults to `blake3` if the library is installed (exceptionally fast).
- Otherwise, defaults to `blake2b` (128-bit) for sample hashes and `sha256` for full hashes.
- Use `--hash-algo` to explicitly override. Note: `--hash full` requires `blake3` or `sha256`.

## Recommended Workflow

1.  **Generate Inventories:** Generate a fast inventory for each backup drive (no hashing by default).
    ```bash
    ./findphotodates.py --directory "/Volumes/Drive1" -o Drive1_inventory.tsv
    ./findphotodates.py --directory "/Volumes/Drive2" -o Drive2_inventory.tsv
    ```
2.  **Add Hashes (when needed):** Before verifying backups, add content hashes to enable verified matching.
    ```bash
    ./findphotodates.py -o Drive1_inventory.tsv --add-hashes
    ./findphotodates.py -o Drive2_inventory.tsv --add-hashes
    ```
3.  **Verify Target Folder:** Use `check_photo_backups.py` to compare a staging area against those inventories.
    ```bash
    ./check_photo_backups.py --target "/home/user/Staging" --inventories "Drive1_inventory.tsv,Drive2_inventory.tsv"
    ```
4.  **Review and Cleanup:** Confirmed files are listed in `safe_to_delete.txt`. You can also use the generated `delete_safe.sh` script for semi-automated removal.

## Usage (findphotodates.py)

### Basic Usage
```bash
./findphotodates.py --directory "/path/to/your/media"
```

### Examples
```bash
# Scan for videos only
./findphotodates.py --video -o my_videos.tsv

# Scan and geolocate photos
./findphotodates.py --only-photos --locate

# Save a scan configuration for an external drive
./findphotodates.py --directory "/Volumes/Backup" --save

# Write legacy format output
./findphotodates.py --directory "/Volumes/Drive1" --old-format -o Drive1_inventory.txt
```

### Command Line Options
```
usage: findphotodates.py [-h] [--directory DIRECTORY] [-o OUTPUT] [-q] [--debug]
                        [--video] [--only-photos] [--extension EXTENSION] [--locate]
                        [--hash {sample,full,off}] [--sample-chunks INT]
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
| `--video` | Search for video files only |
| `--only-photos` | Search for photo files only |
| `--extension` | Specify a custom file extension (e.g., "cr2") |
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
```
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
```
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
- `hash_config_mismatch.txt`: Digest found but sampling parameters differed.

## Troubleshooting

- **ExifTool missing:** Ensure `exiftool` is in your PATH. Try running `exiftool -ver`.
- **No dates found:** Some files may lack EXIF data. The script will leave the date blank.
- **Slow performance:** First runs compute hashes. Install the `blake3` library for best performance.
- **Drive disconnects:** If a drive is unplugged, progress is saved. Reconnect and run again to resume.

## License

This tool is licensed under the GNU General Public License v3.0 (GPL-3.0).

## Contributing

Found a bug? Have a suggestion? Feel free to open an issue or submit a pull request.
