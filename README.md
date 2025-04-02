# findphotodates.py

A versatile Python tool for extracting and organizing dates from your media files.

## What is this?

Ever found yourself with thousands of photos and videos scattered across your drives, wondering when they were actually taken? `findphotodates.py` is the solution. This script scans your photos and videos, extracts the dates they were created (from EXIF data), and organizes this information in a neat text file.

Version 1.0 - April 1, 2025 - By Alan Rockefeller

## Features

- 📷 Works with common photo formats (JPG, JPEG, NEF, ORF)
- 🎥 Supports popular video formats (MP4, MOV, AVI, MKV, etc.)
- 🔍 Recursively searches directories
- 📅 Extracts creation dates from EXIF data
- 📍 Optional geolocation data extraction (when GPS coordinates are available)
- 📊 Provides useful summaries of your media collection
- ⌛ Estimates processing time for large collections
- 📋 Creates a detailed log file with a list of your media and when it was created

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
2. Extract dates from found files
3. Write the results to `photo.dates.txt` in the current directory
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
| `-o`, `--output`, `--out` | Output file to save results (default: photo.dates.txt) |
| `-q`, `--quiet` | Run quietly without printing progress |
| `--debug` | Run in debug mode with verbose output |
| `--video` | Search for video files only |
| `--only-photos` | Search for photo files only |
| `--extension` | Specify a custom file extension (e.g., "cr2") |
| `--locate` | Try to extract location data (if available) |

### Examples

Looking for just videos in a specific folder:
```bash
./findphotodates.py --directory "/Users/alan/Vacation/Hawaii" --video -o hawaii_videos.txt
```

Scanning an entire drive for photos with location data:
```bash
./findphotodates.py --directory "/Volumes/MyExternalDrive" --only-photos --locate
```

Finding specific file types:
```bash
./findphotodates.py --extension raw
```

## Output

The script generates a text file (default: `photo.dates.txt`) with entries like:

```
/Users/alan/Photos/IMG_2354.jpg: 2023:06:12 15:42:33
/Users/alan/Videos/VID_20230613_093054.mp4: 2023:06:13 09:30:54
/Users/alan/Photos/IMG_2401.jpg: 2023:06:14 12:23:07 (Los Angeles, CA, USA)
```

If the `--locate` option is used and GPS data is available, location information will be included when possible.

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

