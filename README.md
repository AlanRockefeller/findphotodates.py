# findphotodates.py

A versatile Python tool for extracting and organizing dates from your media files.

## What is this?

Ever found yourself with thousands of photos and videos scattered across your drives, wondering when they were actually taken? `findphotodates.py` is the solution. This script scans your photos and videos, extracts the dates they were created (from EXIF data), and organizes this information in a neat text file.

Version 1.2 - January 6, 2026 - By Alan Rockefeller

## Features

- Works with common photo formats (JPG, JPEG, NEF, ORF)
- Supports popular video formats (MP4, MOV, AVI, MKV, etc.)
- Recursively searches directories
- Extracts creation dates from EXIF data
- Optional geolocation data extraction (when GPS coordinates are available)
- Provides useful summaries of your media collection
- Estimates processing time for large collections
- Creates a detailed TSV file with a list of your media and when it was created
- **Smart caching** - Uses filesystem metadata to cache results and skip re-processing unchanged files
- Saves progress every 5 minutes or 100 files - and handles errors from disconnecting drives gracefully.    Picks up where it left off.

## Requirements

- Python 3.x
- ExifTool (must be installed and available in your PATH)

## Installation

1. Make sure you have Python 3.x installed
2. Install ExifTool:
   - For macOS: `brew install exiftool`
   - For Ubuntu/Debian: `sudo apt-get install libimage-exiftool-perl`
   - For Windows: Download from [ExifTool website](https://exiftool.org/) and add to PATH
3. Download `findphotodates.py`
4. Make it executable: `chmod +x findphotodates.py`

## Usage

### Basic Usage

```bash
./findphotodates.py --directory "/path/to/your/media"
```

This will:
1. Scan the specified directory for media files (both photos and videos)
2. Extract dates from found files (using cached results when available)
3. Write the results to `photo.dates.tsv` in the current directory
4. Show a summary of results

### Command Line Options

```
usage: findphotodates.py [-h] [--directory DIRECTORY] [-o OUTPUT] [-q] [--debug]
                        [--video] [--only-photos] [--extension EXTENSION] [--locate]
```

#### Options Explained

| Option | Description |
|--------|-------------|
| `--directory` | Directory to search (default: current directory) |
| `-o`, `--output`, `--out` | Output TSV file to save results (default: photo.dates.tsv) |
| `-q`, `--quiet` | Run quietly without printing progress |
| `--debug` | Run in debug mode with verbose output |
| `--video` | Search for video files only |
| `--only-photos` | Search for photo files only |
| `--extension` | Specify a custom file extension (e.g., "cr2") |
| `--locate` | Try to extract location data (if available) |
| `--save` | Save current scan configuration for later use with --scan |
| `--scan` | Run all saved scan configurations |

### Examples

Looking for just videos in a specific folder:
```bash
./findphotodates.py --directory "/Users/alan/Vacation/Hawaii" --video -o hawaii_videos.tsv
```

Scanning an entire drive for photos with location data:
```bash
./findphotodates.py --directory "/Volumes/MyExternalDrive" --only-photos --locate
```

Finding specific file types:
```bash
./findphotodates.py --extension raw
```

### Saving and Automating Scans

For external drives or folders you scan regularly, you can save scan configurations and run them automatically:

**Saving a scan configuration:**
```bash
# Save configuration for an external drive (also runs the scan)
./findphotodates.py --directory "/Volumes/MyPassport/Photos" --locate --save -o ~/my_passport_dates.tsv

# Save configuration for a specific folder (also runs the scan)
./findphotodates.py --directory "/path/to/vacation/photos" --only-photos --save -o ~/vacation_dates.tsv
```

Note: `--save` saves the configuration and then runs the scan immediately (unless `--scan` is also specified, in which case it runs all saved scans). The configuration is stored for future use with `--scan`. Paths with `~` are automatically expanded to your home directory.

**Running all saved scans:**
```bash
# Scan all saved configurations (useful when drives are plugged in)
./findphotodates.py --scan
```

The `--scan` command will:
- Load all saved scan configurations from `~/.findphotodates.config.json`
- Attempt to resolve each saved directory (handles remounted drives)
- Skip directories that aren't currently available (unmounted drives)
- Run each scan with its saved settings (extensions, locate, output file, etc.)

**How it works:**
- Configurations are saved as JSON in your home directory (e.g., `~/.findphotodates.config.json` on Unix, `C:\Users\YourName\.findphotodates.config.json` on Windows)
- Each saved scan includes the directory path, output file, and all flags
- The directory must exist when using `--save` (invalid directories are rejected)
- Drive detection is best-effort: the script tries to match saved drive hints (like volume names) with currently mounted volumes
- On Linux, handles `/media/<user>/<drive>`, `/run/media/<user>/<drive>`, and `/mnt/<drive>` mount patterns
- If a drive isn't found, it falls back to the saved absolute path (useful for temporarily unmounted drives)
- The script automatically skips missing/unmounted drives and continues with available ones
- Duplicate configurations are prevented by matching on drive hint + relative path + output file

**Note on location caching:**
- Location data is cached in the TSV output file - once computed, locations persist across runs
- In-memory coordinate caching during a single run avoids duplicate API calls for the same coordinates
- If you need to re-geolocate coordinates (e.g., after a temporary API outage), you can delete the location column from the TSV or re-run with `--locate` on files that have GPS but no location

## Output

The script generates a TSV (Tab-Separated Values) file (default: `photo.dates.tsv`) with the following columns:

- `filepath`: Full path to the media file
- `date_taken`: Date and time the photo/video was taken (from EXIF data)
- `size_bytes`: File size in bytes
- `mtime_ns`: File modification time in nanoseconds (used for caching)
- `gps_lat`: GPS latitude in decimal degrees (if available)
- `gps_lon`: GPS longitude in decimal degrees (if available)
- `location`: Human-readable location (if `--locate` is used and GPS data is available). Location values persist once computed, so they will appear in the TSV even on subsequent runs without `--locate`.

Example output (tab-separated, shown with `→` representing tabs):
```
# Generated by findphotodates.py
filepath→date_taken→size_bytes→mtime_ns→gps_lat→gps_lon→location
/Users/alan/Photos/IMG_2354.jpg→2023:06:12 15:42:33→2456789→1686582153000000000→37.7749→-122.4194→
/Users/alan/Videos/VID_20230613_093054.mp4→2023:06:13 09:30:54→12345678→1686669054000000000→→→
/Users/alan/Photos/IMG_2401.jpg→2023:06:14 12:23:07→3456789→1686756187000000000→34.0522→-118.2437→Los Angeles, CA, USA
```

Note: Columns are separated by tabs (`\t`), not spaces. In the example above, `→` represents tab characters. Empty fields (like missing GPS or location data) appear as empty between tabs.

## Caching

The script uses intelligent caching to speed up subsequent runs:

- **Automatic caching**: Results are cached in the TSV output file using filesystem metadata (file size and modification time)
- **Skip unchanged files**: If a file's size and modification time match the cached entry, the script reuses the cached date and location data without running exiftool
- **Process only new/changed files**: Only files that are new or have been modified since the last run will be processed with exiftool
- **Atomic updates**: The TSV file is written atomically (using a temporary file) to prevent corruption if the script is interrupted

This means that re-running the script on the same directory will be much faster, as it only needs to process new or changed files!

## Summary Output

After processing, the script provides a summary showing:

1. Count of files by extension
2. Timeline of when photos/videos were taken (by day or month)

For example:
```
Summary of Results:

Files found:
  JPG: 1532
  MP4: 243
  MOV: 78

Media Taken per Month:
  2022-01: 45
  2022-02: 67
  2022-03: 113
  ...
```

## Tips and Tricks

- For large collections, the script will estimate processing time
- The `--debug` option shows more detailed progress information
- An error log is created in your system's temp directory to track any issues
- Some older media files may not have proper EXIF data and will be skipped
- Processing videos is generally slower than photos

## Troubleshooting

If you see warning messages about missing dates, this usually means:

1. The file doesn't contain valid EXIF data
2. The date information is stored in a non-standard format
3. The file might be corrupted

## License

This tool is licensed under the GNU General Public License v3.0 (GPL-3.0). 

This means you are free to:
- Use the software for any purpose
- Change the software to suit your needs
- Share the software with your friends and neighbors
- Share the changes you make

For more details, see the [GNU GPL v3.0 license text](https://www.gnu.org/licenses/gpl-3.0.en.html).

## Contributing

Found a bug? Have a suggestion? Feel free to:
1. Open an issue 
2. Submit a pull request with improvements
3. Contact Alan Rockefeller via gmail, Facebook, Instagram, LinkedIn, etc.

---

