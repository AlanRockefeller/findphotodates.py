# findphotodates.py

**Version 1.6.0 (2026-09-28)**, by Alan Rockefeller

A filesystem inventory tool that indexes all files and extracts EXIF dates and GPS from media files.

## Table of Contents

1. [What is this?](#what-is-this)
2. [The Menu (easiest way to use it)](#the-menu-easiest-way-to-use-it)
3. [Desktop GUI](#desktop-gui)
4. [Features](#features)
5. [Requirements](#requirements)
6. [Installation](#installation)
7. [Content Hashing](#content-hashing)
8. [Recommended Workflow](#recommended-workflow)
9. [Usage (findphotodates.py)](#usage-findphotodatespy)
10. [Backup Checking (check_photo_backups.py)](#backup-checking-check_photo_backupspy)
11. [Troubleshooting](#troubleshooting)
12. [License](#license)

## What is this?

`findphotodates.py` recursively indexes all files in a directory tree, producing a TSV inventory with filepath, size, mtime, and content hash for every file. For media files (photos, videos, RAW images), it also extracts EXIF creation dates and GPS coordinates via ExifTool. The result is a complete filesystem inventory that doubles as a media date catalog.

## The Menu (easiest way to use it)

Run the program with no options:

```bash
./findphotodates.py             # Linux / macOS / WSL
python findphotodates.py        # Windows
```

It finds your connected drives and the file list for each one, and shows a menu:

```text
File lists are kept in: /home/alan/Documents/findphotodates  (9 lists)

Connected drives:
+-----+----------------------+-----------+------------+-------------------+
| #   | Drive                | Size      | Filesystem | Updated           |
+-----+----------------------+-----------+------------+-------------------+
| 1   | Sierra Club          | 5.0 TB    | exfat      | Today 14:35       |
| Location: /run/media/alan/Sierra Club                                 |
| File list: Sierra Club (5F61-DDF5).tsv                                 |
+-----+----------------------+-----------+------------+-------------------+
| 2   | OM SYSTEM            | 513 GB    | exfat      | Never             |
| Location: /run/media/alan/OM SYSTEM                                   |
| File list: New: OM SYSTEM (1234-ABCD).tsv                              |
+-----+----------------------+-----------+------------+-------------------+

  To do several drives in a row, type their numbers, e.g. 1 3 or 2-4

  u) Update every connected drive that has a file list
  h) Check drive health
  l) Show all file lists, including drives that aren't connected
  r) Look for drives again (after plugging one in)
  a) Advanced options
  q) Quit
```

Pick a drive to update its list (only new or changed files are read, so this is quick, and afterwards it tells you what changed) or to make a list for a drive that doesn't have one yet. To do several drives one after another, type their numbers together, like `3 4` or `2-4`. `--interactive` (or `-i`) opens the same menu explicitly. Running with any other option works exactly as before, so scripts are unaffected.

### Where file lists are kept

| System  | Folder |
|---------|--------|
| Linux   | `~/Documents/findphotodates/` (follows your XDG Documents folder) |
| macOS   | `~/Documents/findphotodates/` |
| Windows | `Documents\findphotodates\` (the real Documents folder, even if OneDrive has moved it) |
| WSL     | The **Windows** Documents folder, e.g. `/mnt/c/Users/<you>/Documents/findphotodates/`, so WSL and Windows share the same lists |

These lists are the only record of what's on drives that are usually unplugged, so they're kept somewhere visible that normal backups include. You can choose a different folder in the advanced menu.

Each list is named after its drive, for example `Sierra Club (5F61-DDF5).tsv`: the drive's name plus its serial number, so two drives both called "Untitled" don't collide. The first time you run the menu, it offers to move lists made by earlier versions (like `~/f:photo.taken.dates.txt`) into this folder and rename them.

### How drives are recognised

Drive letters and mount points change: a drive that was `L:` on Windows or `/mnt/l` in WSL might be `/run/media/alan/Sierra Club` on Linux or `/Volumes/Sierra Club` on a Mac. The menu matches each list to its drive by, in order:

1. **The drive's serial number** (recorded in the list's header on every whole-drive scan)
2. **The same location** as when the list was made
3. **The contents**: it checks whether files from the list are on the drive with the same sizes. This is how lists made by older versions are recognised. A list that already records a different drive's serial number is never matched this way, so a drive holding copies of another drive's folders gets its own list.

When a drive has moved, the old list is still used as a cache, so an update only reads new or changed files instead of starting over.

### Checking drive health

Choose `h` in the menu, or run `findphotodates.py --health` from the command line, to check your drives. You can check one drive or all of them, with a quick or an extended check:

- **Quick check** (a few seconds): reads the health information each drive keeps about itself (its SMART data): bad or unreadable sectors, read errors, cable or USB connection errors, temperature, age, SSD wear and the result of its last self-test. Each warning comes with a plain explanation of what it means and what to do.
- **Extended check** (hours): does the quick check, then has each drive run its own full-surface self-test, which finds weak spots in places that are rarely read. The test runs inside the drive, so several drives can test at once and you can keep using the computer. Drives that can't test themselves, like SD cards and many USB sticks, have every file read back instead, and any file that can't be read is listed in a `Health checks` folder next to your file lists.

The health check uses `smartctl` from smartmontools, which handles hard drives, SATA SSDs, NVMe SSDs and most USB enclosures. (NVMe self-tests need smartctl 7.3 or newer; nvme-cli isn't needed.) If smartctl isn't installed, the program tells you how to install it:

| System  | Install command |
|---------|-----------------|
| Arch    | `sudo pacman -S smartmontools` |
| Debian / Ubuntu | `sudo apt install smartmontools` |
| Fedora  | `sudo dnf install smartmontools` |
| macOS   | `brew install smartmontools` |
| Windows | `winget install smartmontools` |

Reading health data needs administrator access. On Linux the program asks for your password through `sudo`; on Windows, open the terminal with "Run as administrator". Without `smartctl`, Windows and macOS still report the basic health status the system knows about. Under WSL the physical drives aren't visible, so run the program from Windows instead.

#### Health log

Every check is added to `Drive health log.tsv` in the file lists folder, so you can see the last known health of drives that aren't plugged in and follow how each drive changes over time. It's a tab-separated file that opens in any spreadsheet, with one row per check: date, drive, model, serial number, type of check, result, hours powered on, temperature, replaced/pending/unreadable sectors, read and connection errors, SSD wear and data errors, the last self-test, and notes.

- After each check, the program compares it with that drive's previous entry and points out anything that got worse, for example "Unreadable sectors waiting to be replaced rose from 0 to 8".
- Drives are tracked by serial number, so a drive keeps its history even if its name or mount point changes.
- Before a scan, the program checks the log: if the drive's last check found bad sectors or read errors, or rated it FAILING, it warns you and suggests copying your files off first.
- The health menu lists drives that aren't connected with their last recorded result, and `h` there shows each drive's history.

On the command line, `--health` checks every drive, `--health extended` runs the extended check, and `--health history` shows the log. Add `--directory` to check only the drive holding that folder. The exit code is 1 if any drive shows warning signs, so it can be used in scripts.

### Advanced options

The advanced menu explains each option in plain language (type `?` and a number to read about one without changing it):

| Option | What it's for |
|--------|---------------|
| What to include | All files (default) or photos and videos only |
| File fingerprints | Off / Quick / Full content hashing, for the backup checker's most trustworthy matches |
| Place names from GPS | Look up place names for photos with GPS coordinates (OpenStreetMap, needs internet) |
| Re-check files with no photo date | Re-read undated photos after an interrupted scan or an ExifTool upgrade |
| Photos read at the same time | Parallel ExifTool readers; more doesn't help on a single USB hard drive |
| Skip tiny images | Size below which JPEG/PNG/WebP files are listed without being opened |
| Path style in file lists | Linux-style or Windows-style paths |
| Make or update a list for one folder | For a folder rather than a whole drive |
| Check which files are backed up | Runs `check_photo_backups.py` on a folder against all your lists; reports go in a `Backup checks` folder |
| Import file lists | Move lists from another folder or an older version into the file lists folder |
| Where file lists are kept | Change the folder |
| Show the equivalent command | Print the command line for the current settings, for scripts |

## Desktop GUI

The desktop app follows the same workflow as the menu: select one or more connected drives, then click **Make or update selected lists**. It shows each drive's existing list and last update in a compact table; lists updated today show **Today** and the time. It scans selected drives one after another, displays live progress, and lets you stop a scan while keeping its resumable progress. **Show all lists** includes unplugged drives. **Check drive health** runs a quick or extended check.

**Advanced** contains all menu options: what to include, fingerprints, GPS place names, rechecking undated media, parallel readers, tiny-image threshold, path style, folder scans, backup checks, importing lists, changing the list folder, and copying the equivalent command. Hover over a setting or its `?` button for details; click `?` to keep the explanation open. Settings last until the app closes, as they do in the menu. The window starts compact and requests more height when Advanced opens. Hyprland and other tiling window managers can fill a tile regardless of the requested size; switch the window to floating to use the compact size.

To run from source, first install [ExifTool](#requirements). On Linux, Tk is a system package: install it with `sudo pacman -Syu tk` on Omarchy/Arch or `sudo apt install python3-tk` on Debian/Ubuntu. A virtual environment installs the Python packages but cannot supply a missing system Tk library.

On Linux, macOS, or WSL, create and activate a virtual environment, then start the GUI:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-gui.txt
python findphotodates_gui.py
```

On Windows, use PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
.\.venv\Scripts\python.exe findphotodates_gui.py
```

The command-line program still works without the GUI dependency. The GUI does not move or delete photos; the backup checker writes reports without deleting anything.

Double-click builds are made separately on Linux, macOS, and Windows because PyInstaller does not cross-compile:

```bash
python -m pip install -r requirements-gui.txt -r requirements-build.txt
pyinstaller packaging/findphotodates_gui.spec
```

The output is `dist/FindPhotoDates/` on Linux and Windows, or `dist/FindPhotoDates.app` on macOS. Keep the worker executable beside the GUI executable. ExifTool must be installed and available on `PATH` when scanning. The [build workflow](.github/workflows/build-gui.yml) attaches packaged apps to versioned releases. After updating `__version__` and `CHANGELOG.md` and committing, run `./build-release.sh` to tag and push a release.

## Features

- Interactive menu when run with no options: finds connected drives and their file lists, updates or creates lists, and explains advanced options in plain language
- Recognises drives wherever they're mounted: lists are matched to drives by serial number or contents, and reused as a cache when a drive's letter or mount point changes
- Indexes every file type by default: documents, archives, media and everything else
- Extracts creation dates and GPS from media files via ExifTool (photos, videos, RAW formats)
- Only sends file types that can hold photo dates to ExifTool. Other files are listed using just their name, size and date
- Works with common photo formats (JPG, JPEG, NEF, ORF, CR2, CR3, ARW, DNG, HEIC, HEIF, AVIF, and more)
- Supports popular video formats (MP4, MOV, AVI, MKV, WMV, FLV, WEBM, M4V, 3GP, MTS, etc.)
- Recursively searches directories
- Can look up place names for photos that have GPS coordinates
- Prints a summary of your collection
- Shows an estimated time remaining on large scans
- Writes a TSV file listing every file
- Optional content fingerprints (a quick sample hash or a full-file hash) for checking backups and finding duplicates
- Skips files that haven't changed since the last scan, using the previous list and a SQLite hash cache
- After a rescan, reports what's new, changed, moved or renamed, and removed since the last scan
- Photos moved or copied to another folder keep their dates without being read again (same name, size and modification time)
- Checks drive health (quick SMART check, or an extended full-surface test), with plain explanations of any warnings, and keeps a health log so you can see unplugged drives and changes over time
- Lists made under WSL are reused when scanning from native Windows and vice versa, including filenames with "funny characters" (like quotes) that the two represent differently.
- Saves progress every 15 minutes, and saves again if a drive is disconnected. Running it again picks up where it left off.
- Sends files to ExifTool in batches, which is faster on large scans
- Runs several ExifTool processes at once (`--workers`, default 4)
- Uses ExifTool's faster `-fast2` mode for JPEG/PNG/WebP files, and skips JPEG/PNG/WebP files smaller than `--min-image-size`
- Uses `os.scandir()` instead of `os.walk()` to find files faster
- Remembers place names looked up from GPS coordinates (in SQLite) and shares them between lists
- `--debugperformance` shows where the time went (finding files, ExifTool, hashing and so on)

## Requirements

- Python 3.x
- **ExifTool** must be installed and available on your system PATH. Run `exiftool -ver` to verify. This applies on Linux, macOS, WSL, **and** native Windows.
- Scans that need ExifTool fail fast if the `exiftool` command cannot be started, rather than writing blank EXIF/date fields for every media file.
- (Optional) `blake3` library for faster hashing (`pip install blake3`)
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

`findphotodates.py` can record a fingerprint (hash) of each file's contents, which helps with checking backups and finding duplicates.

### Hashing Modes

- **`off` (Default)**: No hashing during indexing. Fast initial scans. Hashes can be added later with `--add-hashes`.
- **`sample`**: Creates a fast fingerprint by hashing the first/last and evenly spaced chunks of the file (plus the file size). Use `--hash sample` to enable.
- **`full`**: Hashes the entire file. Slower, but the most certain way to tell whether two files are identical.

### Hash Caching

Both scripts use SQLite caches to avoid repeated hashing:

- `findphotodates.py` uses a cache at `~/.cache/findphotodates/hash_cache.sqlite` (configurable via `--hash-cache`).
- `check_photo_backups.py` uses a cache at `~/.cache/check_photo_backups/fingerprints.sqlite` (configurable via `--fingerprint-cache`).

Note: cache entries are keyed on the file's path, so a file is never mistaken for a different file somewhere else. WSL mount paths (`/mnt/f/...`) and Windows drive paths (`F:\...`) are normalized to the same key, so switching between WSL and native Windows does not cause cache misses. However, if you move a file to a different directory or rename it, the file must be re-hashed once. First runs on a collection will be slower while hashes are computed. SQLite caches can grow over time; delete the `.sqlite` file to reclaim space.

### Algorithms

- Defaults to `blake3` if the library is installed (it's the fastest).
- Otherwise, defaults to `blake2b` (128-bit) for sample hashes and `sha256` for full hashes.
- Use `--hash-algo` to explicitly override. Note: `--hash full` requires `blake3` or `sha256`.

## Recommended Workflow

The easiest way is the [menu](#the-menu-easiest-way-to-use-it): run the program with no options. The command-line steps below do the same thing and are useful for scripts.

1.  **Generate Inventories:** Generate a fast inventory for each backup drive (no hashing by default).

    ```bash
    # Linux / WSL
    ./findphotodates.py --directory /mnt/f/Photos -o f_inventory.tsv
    ./findphotodates.py --directory /mnt/o -o o_inventory.tsv

    # Windows (PowerShell)
    python findphotodates.py --directory F:\Photos -o f_inventory.tsv
    python findphotodates.py --directory O:\ -o o_inventory.tsv
    ```

    Inventories are cross-platform: a TSV created under WSL is reused when scanning from native Windows and vice versa.

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
# Linux / WSL: full inventory of an external drive
./findphotodates.py --directory /mnt/o -o o_inventory.tsv --save

# Windows (PowerShell): same drive, same inventory file, unchanged files are reused
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
usage: findphotodates.py [-h] [-i] [--health [{quick,extended,history}]] [--directory DIRECTORY] [-o OUTPUT] [-q] [--debug]
                        [--only-media] [--video] [--only-photos] [--extension EXTENSION]
                        [--locate] [--hash {sample,full,off}] [--add-hashes]
                        [--sample-chunks INT] [--sample-chunk-mib FLOAT]
                        [--hash-algo {blake3,blake2b,sha256}]
                        [--hash-cache PATH] [--no-hash-cache] [--hash-exts LIST]
                        [--location-cache PATH] [--no-location-cache]
                        [--old-format] [--debugperformance] [--workers N]
                        [--min-image-size BYTES] [--linux] [--windows]
                        [--save] [--scan]
```

| Option                    | Description                                                           |
| ------------------------- | --------------------------------------------------------------------- |
| `--directory`             | Directory to search (default: current directory)                      |
| `-o`, `--output`, `--out` | Output file path (default: photo.dates.tsv or .txt)                   |
| `-q`, `--quiet`           | Run quietly without printing progress                                 |
| `--debug`                 | Run in debug mode with verbose output                                 |
| `--only-media`            | Index only media files (photos + videos) instead of all files         |
| `--video`                 | Index only video files                                                |
| `--only-photos`           | Index only photo files                                                |
| `--extension`             | Index only files with a specific extension (e.g., "pdf")              |
| `--locate`                | Try to extract location data (if available)                           |
| `--hash`                  | Hashing mode: `off` (default), `sample`, or `full`                    |
| `--add-hashes`            | Fill in missing hashes for an existing inventory TSV                  |
| `--sample-chunks`         | Number of chunks for sample hash (default: 3)                         |
| `--sample-chunk-mib`      | Size of each chunk in MiB (default: 0.0625 = 64 KiB)                  |
| `--hash-algo`             | Override hash algorithm (`blake3`, `blake2b`, or `sha256`)            |
| `--hash-cache`            | Path to SQLite hash cache                                             |
| `--no-hash-cache`         | Disable hash cache                                                    |
| `--hash-exts`             | Comma-separated extensions to hash (default: hash all)                |
| `--location-cache`        | Path to SQLite reverse-geocoding cache                                |
| `--no-location-cache`     | Disable persistent reverse-geocoding cache                            |
| `--old-format`            | Write legacy output (`./path: YYYY:MM:DD`) without hashes             |
| `--debugperformance`      | Print detailed timing statistics after each scan                      |
| `--workers`               | Number of parallel ExifTool worker threads (default: 4)               |
| `--min-image-size`        | Skip ExifTool for tiny JPG/JPEG/PNG/WebP files (default: 100,000)     |
| `--linux`                 | Force Linux-style paths (/mnt/c/...) in output (default: auto-detect) |
| `--windows`               | Force Windows-style paths (C:\...) in output (default: auto-detect)   |
| `--save`                  | Save current scan configuration for later use with --scan             |
| `--scan`                  | Run all saved scan configurations                                     |

During interactive scans, press the space bar to pause transient progress/status line updates for one minute. The scan continues running.

## Backup Checking (check_photo_backups.py)

`check_photo_backups.py` computes target-side fingerprints and compares them against your backup inventories.

**Important:** If any inventory was generated with `--hash full`, the check script computes full hashes for the target files too, so the two can be compared. Matches on name, size and date alone are not considered "safe to delete" by default unless `--allow-strong-without-hash` is used.

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

| Option                        | Description                                                                                     |
| ----------------------------- | ----------------------------------------------------------------------------------------------- |
| `--target`                    | Folder to verify (staging area)                                                                 |
| `--inventories`               | Comma-separated inventory files from backup drives                                              |
| `--hash-mode`                 | Hashing mode: `auto` (default: compute only when inventories have hashes), `compute`, or `off` |
| `--out-csv`                   | Path to the detailed CSV report                                                                 |
| `--safe-list`                 | List of confirmed safe-to-delete files                                                          |
| `--needs-hash-list`           | Files that matched by metadata but were not hashed                                              |
| `--delete-script`             | Generates a shell script to delete safe files                                                   |
| `--drive-map`                 | Map drive labels to roots (e.g., c=/mnt/c,d=/mnt/d)                                             |
| `--fingerprint-cache`         | Path to the target-side fingerprint cache                                                       |
| `--allow-strong-without-hash` | Mark strong matches (name+size+date) as safe                                                    |
| `--allow-weak-without-hash`   | Mark weak matches (name+size) as safe                                                           |
| `-q`, `--quiet`               | Suppress normal output                                                                          |

## Inventory Output Format (findphotodates.py)

The script generates a TSV file with metadata headers describing the scan parameters. (Note: Columns are separated by tabs; spacing below is for display only).

```text
# inventory_root=/Users/alan/Photos
# volume_label=Sierra Club
# volume_ids=5F61-DDF5
# hash_mode=sample
# content_hash_format=samplehash_v1
# samplehash_v1 algo=blake3 chunks=3 chunk_mib=0.0625
# Generated by findphotodates.py
filepath	date_taken	size_bytes	mtime_ns	gps_lat	gps_lon	location	content_hash
/Users/alan/Photos/IMG_2354.jpg	2023:06:12 15:42:33	2456789	1686582153000000000	37.7749	-122.4194		a3b1c2d3...
```

`volume_label` and `volume_ids` are written only when a whole drive is scanned. They record which drive the list belongs to, so the menu can find the right list even if the drive's letter or mount point changes. `volume_ids` can hold several IDs, because some systems report the same drive's serial number differently.

## Reports Output (check_photo_backups.py)

The detailed CSV report (`backup_check_report.csv`) provides safety and matching information:

- `safety`: Safety status (e.g., `verified_hash`, `strong`, `missing`, `hash_config_mismatch`).
- `match_type`: The level of matching found (e.g., `verified_hash`, `strong`, `weak`).
- `safe_to_delete`: `yes` if the file meets safety criteria.

Output lists are generated for different states:

- `safe_to_delete.txt`: Confirmed files.
- `needs_hash.txt`: Metadata matches that weren't hashed (rerun with `--hash-mode compute`).
- `no_verified_match.txt`: Hashed files where the digest wasn't found in any inventory.

## Troubleshooting

- **ExifTool missing:** Make sure `exiftool` is installed and in your PATH. Run `exiftool -ver` to check. On Windows, make sure the ExifTool directory is in your system PATH environment variable.
- **No dates found:** Some files may lack EXIF data. The script will leave the date blank.
- **Slow performance:** First runs compute hashes. Install the `blake3` library for best performance.
- **Drive disconnects:** If a drive is unplugged, progress is saved. Reconnect and run again to resume. Saved progress keeps the previous list's entries for anything the scan hadn't reached yet, so stopping partway never shrinks the list.
- **A scan crawls or reports read errors:** The drive may be failing. The scan shows unreadable files and folders as it finds them (the first 20, with a running count on the progress line), lists them all at the end or when you press Ctrl-C (the full list is saved in a `Read errors` folder next to your file lists), keeps their entries from the previous list, and after a few errors switches to reading one file at a time and warns you. If the drive holds the only copy of anything, press Ctrl-C (progress is saved), run a health check, and copy your files off it first. The program also warns before scanning a drive whose last health check found bad sectors.
- **Cross-platform cache misses:** Inventory cache keys normalize WSL (`/mnt/f/...`) and Windows (`F:\...`) paths to a shared form. If a drive is now mounted somewhere else (for example `/mnt/l` under WSL, then `/run/media/<you>/<Label>` on Linux), the old paths are mapped to the new location automatically, as long as the old location no longer exists and you scan the whole drive into its existing list. If you still see cache misses, make sure you're scanning the same physical drive.
- **Slow scans under WSL:** Reading Windows drives through `/mnt/<letter>` in WSL is much slower than running natively. For drives attached to Windows, run `python findphotodates.py` from Windows instead; the lists work in both.

## License

This tool is licensed under the GNU General Public License v3.0 (GPL-3.0).

## Contributing

Found a bug? Have a suggestion? Feel free to open an issue or submit a pull request.
