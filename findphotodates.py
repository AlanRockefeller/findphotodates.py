#!/usr/bin/python3

# Find photo / video dates version 1.0 - By Alan Rockefeller April 1, 2025

import os
import subprocess
import argparse
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import tempfile

# Define supported photo file extensions
PHOTO_EXTENSIONS = {"jpg", "jpeg", "nef", "orf"}
# Define common video file extensions
VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "wmv", "flv", "webm", "m4v", "3gp"}
# Default extensions (both photos and videos)
DEFAULT_EXTENSIONS = PHOTO_EXTENSIONS.union(VIDEO_EXTENSIONS)

def is_supported_file(filename, extensions):
    """Check if a file has a supported extension."""
    return any(filename.lower().endswith(f".{ext}") for ext in extensions)

def find_files(directory, extensions):
    """Recursively find all files with the specified extensions in the given directory."""
    for root, _, files in os.walk(directory):
        for file in files:
            if is_supported_file(file, extensions):
                yield os.path.join(root, file)

def get_photo_data(filepath, locate=False):
    """Use exiftool to get the date and optionally GPS data of a photo/video."""
    try:
        # Call exiftool to extract the Date/Time Original tag, fall back to other date fields
        date_tags = ["-DateTimeOriginal", "-CreateDate", "-MediaCreateDate"]
        tags = date_tags.copy()
        
        if locate:
            tags.extend(["-GPSLatitude", "-GPSLongitude"])

        result = subprocess.run(
            ["exiftool", *tags, "-s", "-s", "-s", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            
            # Process date - try each possible date field
            date = None
            for i, line in enumerate(lines):
                if i < len(date_tags) and line and not line.startswith("GPS"):
                    try:
                        # Basic validation that this looks like a date
                        if re.match(r'\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}', line):
                            date = line
                            break
                    except (IndexError, AttributeError) as e:
                        continue
            
            # Process GPS data if requested and available
            gps_lat = None
            gps_lon = None
            
            if locate:
                for line in lines:
                    if line.startswith("GPS"):
                        if "Latitude" in line:
                            gps_lat = line
                        elif "Longitude" in line:
                            gps_lon = line
                            
            return date, gps_lat, gps_lon
        else:
            return None, None, None
    except FileNotFoundError as e:
        raise FileNotFoundError("Exiftool is required but was not found. Please install it.") from e

def geolocate(lat, lon):
    """Convert GPS coordinates to a human-readable location."""
    if not lat or not lon:
        return None
        
    try:
        # Create a temporary file for reverse geocoding
        with tempfile.NamedTemporaryFile(suffix='.gpx', delete=False) as temp:
            temp_path = temp.name
            
        # Write a temporary GPX file with the coordinates
        with open(temp_path, 'w') as f:
            f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1">
<wpt lat="{lat}" lon="{lon}">
</wpt>
</gpx>""")
            
        # Use exiftool's reverse geocoding capability
        result = subprocess.run(
            ["exiftool", "-p", "$gpslatitude, $gpslongitude, $location", temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Cleanup
        import contextlib
        with contextlib.suppress(Exception):
            os.unlink(temp_path)
            pass
            
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        else:
            return f"{lat}, {lon}"
    except Exception:
        return f"{lat}, {lon}"

def write_dates_to_file(output_file, photo_data):
    """Write photo filenames and dates (and optionally locations) to the output file."""
    with open(output_file, "w") as f:
        for filename, date, location in photo_data:
            line = f"{filename}: {date}"
            if location:
                line += f" ({location})"
            line += "\n"
            f.write(line)

def summarize_results(photo_data, file_counts, quiet):
    """Print a summary of results."""
    if quiet:
        return

    print("\nSummary of Results:\n")

    # Print counts by file type
    print("Files found:")
    for ext, count in sorted(file_counts.items()):
        print(f"  {ext.upper()}: {count}")

    # Calculate date summaries
    date_counter = Counter()
    for _, date, _ in photo_data:
        if date:
            try:
                day = date.split(" ")[0]  # Extract just the date portion
                date_counter[day] += 1
            except (IndexError, AttributeError) as e:
                # Skip invalid dates
                print(f"Error processing date '{date}': {str(e)}")
                continue

    # Determine file type label for summary
    file_type_label = "Media" 
    if all(ext in VIDEO_EXTENSIONS for ext in file_counts):
        file_type_label = "Videos"
    elif all(ext in PHOTO_EXTENSIONS for ext in file_counts):
        file_type_label = "Photos"
            
    if len(date_counter) <= 20:
        print(f"\n{file_type_label} Taken per day:")
        for day, count in sorted(date_counter.items()):
            print(f"  {day}: {count}")
    else:
        month_counter = defaultdict(int)
        for day in date_counter:
            try:
                year_month = "-".join(day.split("-")[:2])  # Extract year and month
                month_counter[year_month] += date_counter[day]
            except (IndexError, ValueError) as e:
                if args.debug:
                    print(f"Error extracting year-month from '{day}': {str(e)}")
                continue
        print(f"\n{file_type_label} Taken per Month:")
        for year_month, count in sorted(month_counter.items()):
            print(f"  {year_month}: {count}")

def main():
    # Set up argument parsing
    parser = argparse.ArgumentParser(
        description="Recursively find files with specified extensions and extract the date each file was created using exiftool."
    )
    parser.add_argument(
        "--directory",
        default=".",
        help="The directory to search (default: current directory)."
    )
    parser.add_argument(
        "-o", "--output", "--out",
        default="photo.dates.txt",
        help="The output file to save the results (default: photo.dates.txt)."
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Run the script quietly without printing output unless there is an error."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run the script in debug mode, providing verbose output."
    )
    parser.add_argument(
        "--video",
        action="store_true",
        help="Search for video files (mp4, mov, avi, etc.) only."
    )
    parser.add_argument(
        "--only-photos",
        action="store_true",
        help="Search for photo files (jpg, jpeg, nef, orf) only."
    )
    parser.add_argument(
        "--extension",
        help="Specify a custom file extension to search for (overrides other extensions)."
    )
    parser.add_argument(
        "--locate",
        action="store_true",
        help="Attempt to locate files using GPS coordinates."
    )

    args = parser.parse_args()

    # Determine the extensions to use
    if args.extension:
        extensions = {args.extension.lower()}
    elif args.video:
        extensions = VIDEO_EXTENSIONS
    elif args.only_photos:
        extensions = PHOTO_EXTENSIONS
    else:
        extensions = DEFAULT_EXTENSIONS

    # Ensure the directory exists
    if not os.path.exists(args.directory):
        print(f"Error: Directory '{args.directory}' does not exist.")
        return

    if not args.quiet:
        print(f"Searching for files in '{args.directory}' with extensions: {', '.join(extensions)}...")

    files = list(find_files(args.directory, extensions))

    if not files:
        if not args.quiet:
            print("No files found with the specified extensions.")
        return

    if not args.quiet:
        print(f"Found {len(files)} files.")

    file_counts = Counter(file.split(".")[-1].lower() for file in files)

    # Estimate runtime if more than 10 files are found
    start_time = time.time()
    estimated_finish_time = None
    if len(files) > 10 and not args.quiet:
        test_files = min(5, len(files))
        for file in files[:test_files]:
            get_photo_data(file)
        elapsed_time = time.time() - start_time
        avg_time_per_file = elapsed_time / test_files
        estimated_total_time = avg_time_per_file * len(files)
        estimated_finish_time = datetime.now() + timedelta(seconds=estimated_total_time)

        if args.debug:
            print(f"Timing the processing of the first {test_files} files to estimate total runtime...")
        if estimated_total_time > 3600:
            hours = int(estimated_total_time // 3600)
            minutes = int((estimated_total_time % 3600) // 60)
            print(f"Estimated total runtime: {hours} hours and {minutes} minutes.")
        elif estimated_total_time > 180:
            minutes = int(estimated_total_time // 60)
            seconds = int(estimated_total_time % 60)
            print(f"Estimated total runtime: {minutes} minutes and {seconds} seconds.")
        else:
            print(f"Estimated total runtime: {estimated_total_time:.2f} seconds.")
        print(f"Expected to finish by: {estimated_finish_time.strftime('%Y-%m-%d %H:%M:%S')}.")

    # Redirect warnings to a log file
    error_log_path = os.path.join(tempfile.gettempdir(), "findphotodates.error.log")
    with open(error_log_path, "w") as error_log:
        # Extract dates for each file
        photo_data = []
        for idx, file in enumerate(files):
            date_taken, gps_lat, gps_lon = get_photo_data(file, locate=args.locate)
            location = None
            if args.locate and gps_lat and gps_lon:
                location = geolocate(gps_lat, gps_lon)
            if date_taken:
                photo_data.append((file, date_taken, location))
            else:
                error_log.write(f"Warning: Could not extract date for '{file}'.\n")

            # Update progress bar with better formatting
            if not args.quiet and idx % 10 == 0:  # Update less frequently for better performance
                progress_percentage = (idx + 1) / len(files) * 100
                progress_bar = '[' + '#' * int(progress_percentage // 5) + ' ' * (20 - int(progress_percentage // 5)) + ']'
                print(f"Progress: {progress_percentage:.2f}% {progress_bar}", end="\r")

    if not args.quiet:
        print("\nProcessing complete.                                        ")  # Extra spaces to clear progress bar

    # Write results to the output file
    write_dates_to_file(args.output, photo_data)

    if not args.quiet:
        print(f"Dates written to '{args.output}' ({len(photo_data)} files listed).")

    # Print summary of results
    summarize_results(photo_data, file_counts, args.quiet)

if __name__ == "__main__":
    main()
