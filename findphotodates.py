#!/usr/bin/python3

# Find photo / video dates version 1.1 - By Alan Rockefeller April 2024

import os
import subprocess
import argparse
import re
import time
from collections import Counter, defaultdict # Counter can be imported directly for type hints
from datetime import datetime, timedelta
import tempfile
import json
import sys
import logging
from typing import (
    List, Set, Dict, Tuple, Optional, Iterable, Any, Callable, Union
)

# Define supported photo file extensions
PHOTO_EXTENSIONS: Set[str] = {"jpg", "jpeg", "nef", "orf"}
# Define common video file extensions
VIDEO_EXTENSIONS: Set[str] = {"mp4", "mov", "avi", "mkv", "wmv", "flv", "webm", "m4v", "3gp"}
# Default extensions (both photos and videos)
DEFAULT_EXTENSIONS: Set[str] = PHOTO_EXTENSIONS.union(VIDEO_EXTENSIONS)

# Cache for geolocation results: (lat_str, lon_str) -> location_str
geolocation_cache: Dict[Tuple[str, str], str] = {} 

def is_supported_file(filename: str, extensions: Set[str]) -> bool:
    """Check if a file has a supported extension."""
    return any(filename.lower().endswith(f".{ext}") for ext in extensions)

def find_files(directory: str, extensions: Set[str]) -> Iterable[str]:
    """Recursively find all files with the specified extensions in the given directory."""
    for root, _, files_in_dir in os.walk(directory): # Renamed files to files_in_dir for clarity
        for file_item in files_in_dir: # Renamed file to file_item
            if is_supported_file(file_item, extensions):
                yield os.path.join(root, file_item)

def get_photo_data(filepath: str, locate: bool = False) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Use exiftool to get the date and optionally GPS data of a photo/video.
    
    Returns:
        A tuple (date_str, gps_lat_str, gps_lon_str).
        Each element is None if not found or an error occurred.
    """
    try:
        # Tags to extract. -json flag means we specify tag names directly (without leading '-')
        # However, when passing to command line, exiftool expects them with leading '-'
        exif_tag_names = ["DateTimeOriginal", "CreateDate", "MediaCreateDate"]
        if locate:
            exif_tag_names.extend(["GPSLatitude", "GPSLongitude"])

        # Construct the command for exiftool.
        # e.g., ["exiftool", "-json", "-DateTimeOriginal", "-CreateDate", ..., filepath]
        command_parts: List[str] = ["exiftool", "-json"] 
        for tag_name in exif_tag_names:
            command_parts.append(f"-{tag_name}") # Add '-' prefix for each tag for command line
        command_parts.append(filepath)
        
        result = subprocess.run(
            command_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False # Ensure check is False to handle non-zero exits manually
        )

        if result.stderr:
            # Log exiftool's stderr output, could be warnings or errors
            logging.debug(f"Exiftool stderr for {filepath}: {result.stderr.strip()}")

        if result.returncode == 0 and result.stdout:
            try:
                metadata = json.loads(result.stdout)[0] # exiftool -json output is a list with one item
                date_str = metadata.get("DateTimeOriginal") or \
                           metadata.get("CreateDate") or \
                           metadata.get("MediaCreateDate")
                
                gps_lat = metadata.get("GPSLatitude")
                gps_lon = metadata.get("GPSLongitude")
                
                if not date_str:
                    logging.warning(f"No primary date tags found for {filepath}. Available metadata: {metadata}")
                return date_str, gps_lat, gps_lon
            except json.JSONDecodeError as e:
                logging.error(f"Error parsing JSON output for {filepath}: {e}. Raw output: '{result.stdout[:200]}...'")
                return None, None, None
            except IndexError as e:
                logging.error(f"Error accessing metadata for {filepath}: {e}. JSON was: '{result.stdout[:200]}...'")
                return None, None, None
        elif result.returncode != 0:
            logging.error(f"Exiftool failed for {filepath} with return code {result.returncode}. Stderr: {result.stderr.strip()}")
            return None, None, None
        else: # No output but return code 0
            logging.warning(f"Exiftool returned successfully but with no output for {filepath}.")
            return None, None, None
            
    except FileNotFoundError: # This exception is for exiftool itself not being found
        # This specific error is critical and should be handled at a higher level (e.g., in main)
        # For now, we re-raise it as it's a setup issue, not a per-file processing issue.
        # The main function will catch this if it's the first call, or it will be raised per file.
        # A check in main() is better.
        logging.critical("Exiftool command not found. Please ensure it is installed and in your PATH.")
        raise # Re-raise to be caught by the caller, or by main's initial check.
    except Exception as e: # Catch any other unexpected errors during subprocess execution
        logging.error(f"An unexpected error occurred while processing {filepath} with exiftool: {e}")
        return None, None, None

def geolocate(lat: str, lon: str) -> str:
    """Convert GPS coordinates to a human-readable location.
    Returns the location string or the original lat, lon string if lookup fails.
    """
    if not lat or not lon: # Should ideally not happen if called with valid strings
        logging.warning("Geolocate called with empty or None lat/lon: lat='%s', lon='%s'", lat, lon)
        return f"{lat or 'N/A'}, {lon or 'N/A'}" # Return input if invalid, providing placeholder

    # Key for cache should be a tuple of strings
    cache_key: Tuple[str, str] = (lat, lon)
    if cache_key in geolocation_cache:
        return geolocation_cache[cache_key]

    temp_path: Optional[str] = None
    try:
        # Create a temporary GPX file for reverse geocoding
        with tempfile.NamedTemporaryFile(mode='w', suffix='.gpx', delete=False) as temp_file:
            temp_file.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1">
<wpt lat="{lat}" lon="{lon}">
</wpt>
</gpx>""")
            temp_path = temp_file.name
        
        # Use exiftool's reverse geocoding capability
        # Attempting with a common format string, might need adjustment based on exiftool's actual output field names
        # Common fields for location are City, State, Country.
        # Exiftool's -p option format string for these is typically "$City, $State, $Country"
        # Using "$location" as a general fallback, but specific fields are more reliable.
        # Let's try a more specific and commonly available set of tags.
        # If these specific tags are not found, exiftool usually just prints them as is (e.g. "$City")
        # or an empty string if the tag doesn't exist at all.
        # A more robust approach might be to check multiple potential location tags.
        # For now, using a simple approach.
        result = subprocess.run(
            ["exiftool", "-p", "$City, $State, $Country", temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False # Do not raise exception on non-zero exit
        )
            
        if result.returncode == 0 and result.stdout.strip() and not result.stdout.startswith("$"):
            location = result.stdout.strip()
            geolocation_cache[(lat, lon)] = location
            return location
        else:
            # If exiftool fails or returns an empty/default string, return original coordinates
            # and cache this "failure" to avoid repeated calls for the same coordinates.
            coords_str = f"{lat}, {lon}"
            # geolocation_cache[(lat, lon)] = coords_str # Optionally cache failures too
            return coords_str
            
    except FileNotFoundError: # Specifically if exiftool is not found during geolocate call
        logging.error("Exiftool not found during geocoding attempt. Please ensure it's installed.")
        return f"{lat}, {lon}" # Return coords as fallback
    except subprocess.CalledProcessError as e: # If exiftool command fails
        logging.error(f"Exiftool error during geocoding for {lat}, {lon}: {e}. Stderr: {e.stderr}")
        return f"{lat}, {lon}"
    except Exception as e: # Catch other broad exceptions
        logging.error(f"Error during geocoding for {lat}, {lon}: {e}")
        coords_str = f"{lat}, {lon}"
        # geolocation_cache[(lat, lon)] = coords_str # Optionally cache failures too
        return coords_str
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError as e:
                logging.warning(f"Could not delete temp file {temp_path}: {e}")

def write_dates_to_file(output_file: str, data_to_write: List[Tuple[str, str, Optional[str]]]) -> None:
    """Write photo filenames and dates (and optionally locations) to the output file."""
    try:
        with open(output_file, "w") as f:
            for filename, date_str, location_str in data_to_write:
                line = f"{filename}: {date_str}"
                if location_str:
                    line += f" ({location_str})"
                line += "\n"
                f.write(line)
    except IOError as e:
        logging.error(f"Failed to write to output file {output_file}: {e}")

def summarize_results(
    processed_data: List[Tuple[str, str, Optional[str]]], 
    counts_by_type: Counter[str], 
    is_quiet_mode: bool, 
    is_debug_mode: bool
) -> None:
    """Print a summary of results."""
    if is_quiet_mode: # Parameter renamed for clarity
        return

    print("\nSummary of Results:\n")

    # Print counts by file type
    print("Files found:")
    for ext, count in sorted(counts_by_type.items()): # Parameter renamed
        print(f"  {ext.upper()}: {count}")

    # Calculate date summaries
    date_counter: Counter[str] = Counter()
    for _, date_str_val, _ in processed_data: # Parameter and loop var renamed
        if date_str_val:
            try:
                parsed_date = datetime.strptime(date_str_val, '%Y:%m:%d %H:%M:%S')
                day_str = parsed_date.strftime('%Y-%m-%d') # Renamed loop var
                date_counter[day_str] += 1
            except (ValueError, AttributeError) as e: 
                if is_debug_mode: # Parameter renamed
                    logging.debug(f"Error parsing date '{date_str_val}' for summary: {e}")
                continue

    # Determine file type label for summary
    file_type_label = "Media"
    if all(ext in VIDEO_EXTENSIONS for ext in counts_by_type.keys()):
        file_type_label = "Videos"
    elif all(ext in PHOTO_EXTENSIONS for ext in counts_by_type.keys()):
        file_type_label = "Photos"
            
    if len(date_counter) <= 20:
        print(f"\n{file_type_label} Taken per day:")
        for day_key_str, count_val in sorted(date_counter.items()): # Loop vars renamed
            print(f"  {day_key_str}: {count_val}")
    else:
        month_counter: Dict[str, int] = defaultdict(int) # Type hint added
        for day_key_str, count_val in date_counter.items(): 
            try:
                parsed_day_date = datetime.strptime(day_key_str, '%Y-%m-%d')
                year_month_str = parsed_day_date.strftime('%Y-%m') # Renamed var
                month_counter[year_month_str] += count_val
            except ValueError as e: 
                if is_debug_mode: # Parameter renamed
                    logging.debug(f"Error extracting year-month from '{day_key_str}' for summary: {e}")
                continue
        print(f"\n{file_type_label} Taken per Month:")
        for year_month_str, count_val in sorted(month_counter.items()): # Loop vars renamed
            print(f"  {year_month_str}: {count_val}")

# Type alias for the get_photo_data function signature
GetPhotoDataFunc = Callable[[str, bool], Tuple[Optional[str], Optional[str], Optional[str]]]

def _parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Recursively find files and extract creation dates using Exiftool."
    )
    parser.add_argument(
        "--directory",
        default=".",
        help="The directory to search (default: current directory).",
        type=str
    )
    parser.add_argument(
        "-o", "--output", "--out",
        default="photo.dates.txt",
        help="The output file to save the results (default: photo.dates.txt).",
        type=str
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Run quietly, only logging errors."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for verbose output."
    )
    parser.add_argument(
        "--video",
        action="store_true",
        help="Search only for video files (mp4, mov, avi, etc.)."
    )
    parser.add_argument(
        "--only-photos",
        action="store_true",
        help="Search only for photo files (jpg, jpeg, nef, orf)."
    )
    parser.add_argument(
        "--extension",
        help="Specify a custom file extension (e.g., 'cr2'). Overrides others.",
        type=str
    )
    parser.add_argument(
        "--locate",
        action="store_true",
        help="Attempt to geolocate files using GPS coordinates."
    )
    return parser.parse_args()

def _estimate_runtime_and_cache_initial_files(
    files_to_process: List[str], 
    args_ns: argparse.Namespace, 
    cache_dict: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]],
    get_photo_data_func: GetPhotoDataFunc # Use the type alias
) -> None:
    """Estimates runtime and pre-populates cache for initial files if conditions are met."""
    # This check was originally in main, moved here as it's specific to this function's purpose
    if not (len(files_to_process) > 10 and not args_ns.quiet):
        return

    test_files_count = min(5, len(files_to_process)) # Ensure it's not more than available files
    logging.debug(f"Starting runtime estimation with {test_files_count} files (locate=False for timing).")
    
    files_for_timing = files_to_process[:test_files_count]
    estimation_timer_start = time.time()
    try:
        for file_to_time in files_for_timing:
            # Call with locate=False for PURE timing part, consistent with original logic
            get_photo_data_func(file_to_time, locate=False) 
    except FileNotFoundError: 
        logging.error("Exiftool became unavailable during runtime estimation. Aborting.")
        sys.exit(1)
    elapsed_time_estimation = time.time() - estimation_timer_start
    
    if test_files_count > 0 and elapsed_time_estimation > 0:
        avg_time_per_file = elapsed_time_estimation / test_files_count
        estimated_total_time = avg_time_per_file * len(files_to_process)
        estimated_finish_time = datetime.now() + timedelta(seconds=estimated_total_time)
        
        logging.debug(f"Timing of first {test_files_count} files complete. Avg time/file: {avg_time_per_file:.2f}s.")
        if estimated_total_time > 3600:
            hours = int(estimated_total_time // 3600)
            minutes = int((estimated_total_time % 3600) // 60)
            logging.info(f"Estimated total runtime: {hours} hours and {minutes} minutes.")
        elif estimated_total_time > 180:
            minutes = int(estimated_total_time // 60)
            seconds = int(estimated_total_time % 60)
            logging.info(f"Estimated total runtime: {minutes} minutes and {seconds} seconds.")
        else:
            logging.info(f"Estimated total runtime: {estimated_total_time:.2f} seconds.")
        logging.info(f"Expected to finish by: {estimated_finish_time.strftime('%Y-%m-%d %H:%M:%S')}.")
    else:
        logging.debug("Could not reliably estimate runtime (0 files tested or insufficient time elapsed).")

    # Pre-populate cache for these same files, but with full data extraction (locate=args_ns.locate)
    logging.debug(f"Pre-processing and caching first {test_files_count} files with locate={args_ns.locate}...")
    for i in range(test_files_count):
        file_to_cache = files_to_process[i]
        try:
            date_taken_cache, gps_lat_cache, gps_lon_cache = get_photo_data_func(file_to_cache, locate=args_ns.locate)
            if date_taken_cache: # Only cache if a date was found
                cache_dict[file_to_cache] = (date_taken_cache, gps_lat_cache, gps_lon_cache)
                logging.debug(f"Cached data for {file_to_cache}")
        except FileNotFoundError:
             logging.critical("Exiftool was initially found but seems to be unavailable now (during cache population). Aborting.")
             sys.exit(1)
        except Exception as e: # Catch any other error during this specific file's processing for cache
             logging.error(f"Error pre-processing file {file_to_cache} for cache: {e}")

def main() -> None:
    """Main function to orchestrate file searching and data extraction."""
    args = _parse_arguments()

    # Configure Logging
    log_level = logging.INFO
    if args.quiet:
        log_level = logging.ERROR
    elif args.debug:
        log_level = logging.DEBUG
    
    # Ensure logging is configured before any logging messages are emitted.
    logging.basicConfig(
        level=log_level, 
        format='%(asctime)s %(levelname)s: %(message)s', 
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Check exiftool availability early
    try:
        exiftool_check_result = subprocess.run(["exiftool", "-ver"], capture_output=True, text=True, check=True)
        logging.debug(f"Exiftool version: {exiftool_check_result.stdout.strip()}")
    except FileNotFoundError:
        logging.critical("Exiftool (required) not found. Please install it and ensure it's in your PATH.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        logging.critical(f"Exiftool check failed with error: {e}. Stderr: {e.stderr.strip() if e.stderr else 'N/A'}")
        sys.exit(1)

    # Determine the set of file extensions to scan for
    extensions_to_scan: Set[str]
    if args.extension:
        extensions_to_scan = {args.extension.lower().lstrip('.')} # Ensure no leading dot
    elif args.video:
        extensions_to_scan = VIDEO_EXTENSIONS
    elif args.only_photos:
        extensions_to_scan = PHOTO_EXTENSIONS
    else:
        extensions_to_scan = DEFAULT_EXTENSIONS

    # Validate directory
    if not os.path.isdir(args.directory):
        logging.error(f"Specified directory '{args.directory}' does not exist or is not a directory.")
        sys.exit(1)

    logging.info(f"Searching for files in '{args.directory}' with extensions: {', '.join(extensions_to_scan)}...")
    
    # Collect all files first, as it might be iterated multiple times by estimation logic
    files_found: List[str] = list(find_files(args.directory, extensions_to_scan))

    if not files_found:
        logging.info("No files found with the specified extensions.")
        return # Exit gracefully if no files

    logging.info(f"Found {len(files_found)} files.")
    # Generate file counts by type (extension)
    file_type_counts: Counter[str] = Counter(os.path.splitext(f)[1].lstrip('.').lower() for f in files_found)


    # Cache for get_photo_data results: filepath -> (date_str, lat_str, lon_str)
    photo_metadata_cache: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]] = {}
    # List to store final processed data for output: (filepath, date_str, location_str_or_None)
    collected_photo_metadata_list: List[Tuple[str, str, Optional[str]]] = []

    # Call the extracted function for runtime estimation and initial caching
    _estimate_runtime_and_cache_initial_files(files_found, args, photo_metadata_cache, get_photo_data)
    
    logging.info(f"Starting processing of {len(files_found)} files...")
    for idx, file_path in enumerate(files_found):
        # Initialize variables for each file
        date_taken_str: Optional[str] = None
        gps_lat_str: Optional[str] = None
        gps_lon_str: Optional[str] = None
        location_str: Optional[str] = None # For the final human-readable location

        try:
            # Check cache first
            if file_path in photo_metadata_cache:
                logging.debug(f"Using cached data for {file_path}")
                date_taken_str, gps_lat_str, gps_lon_str = photo_metadata_cache[file_path]
            else:
                # If not in cache, process fresh
                logging.debug(f"Processing fresh for {file_path}")
                date_taken_str, gps_lat_str, gps_lon_str = get_photo_data(file_path, locate=args.locate)

            # Attempt to geolocate if requested and GPS data is available
            if args.locate and gps_lat_str and gps_lon_str:
                try:
                    # geolocate function has its own internal cache
                    location_str = geolocate(gps_lat_str, gps_lon_str) 
                except Exception as e: 
                    logging.error(f"Error geolocating {file_path} (Coords: {gps_lat_str}, {gps_lon_str}): {e}")
            
            # If a date was found, add to the list for output
            if date_taken_str:
                collected_photo_metadata_list.append((file_path, date_taken_str, location_str))
            else:
                # Log warning if no date was extracted (either from cache or fresh processing)
                logging.warning(f"Could not extract a valid date for '{file_path}'. It will be skipped.")

        except FileNotFoundError: # Should ideally be caught by initial exiftool check or estimation
            logging.critical("Exiftool was initially found but seems to be unavailable now (during main loop). Aborting.")
            sys.exit(1)
        except Exception as e: # Catch any other unexpected error for a specific file
            logging.error(f"Unhandled error processing file {file_path} in main loop: {e}")

        # Update progress bar
        if not args.quiet and (idx + 1) % 10 == 0 or (idx + 1) == len(files_found) : # Update every 10 or on last file
            progress_percentage = (idx + 1) / len(files_found) * 100
            progress_bar_str = '[' + '#' * int(progress_percentage // 5) + ' ' * (20 - int(progress_percentage // 5)) + ']'
            print(f"Progress: {progress_percentage:.2f}% {progress_bar_str} ({(idx+1)}/{len(files_found)})", end="\r")

    if not args.quiet:
        # Ensure the line is cleared after progress bar
        print("\nProcessing complete.                                                                 ") 

    # Write results to the output file
    write_dates_to_file(args.output, collected_photo_metadata_list)
    
    # Print final confirmation if not in quiet mode
    if not args.quiet:
        print(f"Dates written to '{args.output}' ({len(collected_photo_metadata_list)} files listed).")

    # Print summary of results
    summarize_results(collected_photo_metadata_list, file_type_counts, args.quiet, args.debug)

if __name__ == "__main__":
    main()
