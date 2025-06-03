import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import tempfile
import json
from datetime import datetime
import subprocess # Needed for CompletedProcess
import argparse # For testing _parse_arguments

# Assuming findphotodates.py is in the same directory or accessible via PYTHONPATH
import findphotodates
from findphotodates import (
    is_supported_file,
    get_photo_data,
    geolocate,
    summarize_results,
    find_files, # Optional to test directly, but good to have
    _parse_arguments, # If we test this helper directly
    # Global caches that might need clearing/managing if not handled by setUp/tearDown
    # geolocation_cache # Will be handled by setUp
)
from collections import Counter # For type hinting and use in summarize_results test


class TestFindPhotoDates(unittest.TestCase):

    def setUp(self):
        """Clear any global state that might affect tests, e.g., caches."""
        findphotodates.geolocation_cache.clear()

    # 4. Test is_supported_file
    def test_is_supported_file(self):
        photo_extensions = {"jpg", "jpeg", "nef"}
        self.assertTrue(is_supported_file("image.jpg", photo_extensions))
        self.assertTrue(is_supported_file("IMAGE.JPEG", photo_extensions))
        self.assertTrue(is_supported_file("photo.nef", photo_extensions))
        self.assertFalse(is_supported_file("document.txt", photo_extensions))
        self.assertFalse(is_supported_file("image.png", photo_extensions))
        self.assertFalse(is_supported_file("image.jpg.backup", photo_extensions))
        self.assertTrue(is_supported_file("archive.tar.gz", {"gz"}))


    # 5. Test get_photo_data
    @patch('subprocess.run')
    def test_get_photo_data_success_datetimeoriginal(self, mock_run):
        mock_stdout = json.dumps([{
            "SourceFile": "test.jpg", 
            "DateTimeOriginal": "2023:01:01 10:00:00"
        }])
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=mock_stdout, stderr=""
        )
        date, lat, lon = get_photo_data("dummy.jpg")
        self.assertEqual(date, "2023:01:01 10:00:00")
        self.assertIsNone(lat)
        self.assertIsNone(lon)
        mock_run.assert_called_once()
        # First element of the first call's args list is the command list
        self.assertIn("-DateTimeOriginal", mock_run.call_args[0][0]) 

    @patch('subprocess.run')
    def test_get_photo_data_fallback_createdate(self, mock_run):
        mock_stdout = json.dumps([{
            "SourceFile": "test.jpg", 
            "CreateDate": "2023:01:02 11:00:00"
        }])
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=mock_stdout, stderr=""
        )
        date, _, _ = get_photo_data("dummy.jpg")
        self.assertEqual(date, "2023:01:02 11:00:00")
        self.assertIn("-CreateDate", mock_run.call_args[0][0])

    @patch('subprocess.run')
    def test_get_photo_data_fallback_mediacreatedate(self, mock_run):
        mock_stdout = json.dumps([{
            "SourceFile": "test.jpg", 
            "MediaCreateDate": "2023:01:03 12:00:00"
        }])
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=mock_stdout, stderr=""
        )
        date, _, _ = get_photo_data("dummy.jpg")
        self.assertEqual(date, "2023:01:03 12:00:00")
        self.assertIn("-MediaCreateDate", mock_run.call_args[0][0])

    @patch('subprocess.run')
    def test_get_photo_data_with_gps(self, mock_run):
        mock_stdout = json.dumps([{
            "SourceFile": "test.jpg", 
            "DateTimeOriginal": "2023:01:01 10:00:00",
            "GPSLatitude": "34 deg 0' 0.00\" N",
            "GPSLongitude": "118 deg 0' 0.00\" W"
        }])
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=mock_stdout, stderr=""
        )
        date, lat, lon = get_photo_data("dummy.jpg", locate=True)
        self.assertEqual(date, "2023:01:01 10:00:00")
        self.assertEqual(lat, "34 deg 0' 0.00\" N")
        self.assertEqual(lon, "118 deg 0' 0.00\" W")
        self.assertTrue(any("-GPSLatitude" in arg for arg in mock_run.call_args[0][0]))
        self.assertTrue(any("-GPSLongitude" in arg for arg in mock_run.call_args[0][0]))


    @patch('subprocess.run')
    def test_get_photo_data_exiftool_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("Exiftool not found")
        with self.assertRaises(FileNotFoundError):
            get_photo_data("dummy.jpg")

    @patch('subprocess.run')
    def test_get_photo_data_exiftool_returns_error(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Exiftool error"
        )
        # Assuming logger is configured, this might log an error. Test return value.
        with patch('logging.error') as mock_log_error: # Suppress error logs during test
             date, lat, lon = get_photo_data("dummy.jpg")
             self.assertIsNone(date)
             self.assertIsNone(lat)
             self.assertIsNone(lon)
             mock_log_error.assert_called()


    @patch('subprocess.run')
    def test_get_photo_data_empty_json(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[]", stderr="" # Exiftool returns list
        )
        with patch('logging.error') as mock_log_error:
            date, lat, lon = get_photo_data("dummy.jpg")
            self.assertIsNone(date)
            self.assertIsNone(lat)
            self.assertIsNone(lon)
            mock_log_error.assert_called() # Should log IndexError due to [0]

    @patch('subprocess.run')
    def test_get_photo_data_invalid_json(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="this is not json", stderr=""
        )
        with patch('logging.error') as mock_log_error:
            date, lat, lon = get_photo_data("dummy.jpg")
            self.assertIsNone(date)
            self.assertIsNone(lat)
            self.assertIsNone(lon)
            mock_log_error.assert_called()


    @patch('subprocess.run')
    def test_get_photo_data_no_date_tags(self, mock_run):
        mock_stdout = json.dumps([{
            "SourceFile": "test.jpg", 
            "Comment": "No date here"
        }])
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=mock_stdout, stderr=""
        )
        with patch('logging.warning') as mock_log_warning: # Should log a warning
            date, lat, lon = get_photo_data("dummy.jpg")
            self.assertIsNone(date)
            self.assertIsNone(lat)
            self.assertIsNone(lon)
            mock_log_warning.assert_called()


    # 6. Test geolocate
    @patch('subprocess.run')
    @patch('tempfile.NamedTemporaryFile')
    @patch('os.unlink') # Mock os.unlink to check if it's called
    def test_geolocate_success_and_cache(self, mock_os_unlink, mock_tempfile, mock_run_geo):
        # Mock NamedTemporaryFile
        mock_temp_file_obj = MagicMock()
        mock_temp_file_obj.name = "dummy_temp.gpx"
        mock_tempfile.return_value.__enter__.return_value = mock_temp_file_obj

        # Mock exiftool call for geolocation
        mock_run_geo.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Los Angeles, California, USA", stderr=""
        )
        
        location1 = geolocate("34.0N", "118.0W")
        self.assertEqual(location1, "Los Angeles, California, USA")
        mock_tempfile.assert_called_once() # tempfile created
        mock_run_geo.assert_called_once()   # exiftool called
        mock_os_unlink.assert_called_with("dummy_temp.gpx") # tempfile deleted

        # Call again, should use cache
        location2 = geolocate("34.0N", "118.0W")
        self.assertEqual(location2, "Los Angeles, California, USA")
        mock_tempfile.assert_called_once() # Not called again
        mock_run_geo.assert_called_once()   # Not called again
        mock_os_unlink.assert_called_once() # Unlink still only called once for the first attempt


    @patch('subprocess.run')
    @patch('tempfile.NamedTemporaryFile')
    @patch('os.unlink')
    def test_geolocate_exiftool_fails(self, mock_os_unlink, mock_tempfile, mock_run_geo):
        mock_temp_file_obj = MagicMock()
        mock_temp_file_obj.name = "dummy_temp_fail.gpx"
        mock_tempfile.return_value.__enter__.return_value = mock_temp_file_obj
        
        mock_run_geo.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Exiftool geo error"
        )
        
        location = geolocate("1.0N", "1.0E")
        self.assertEqual(location, "1.0N, 1.0E")
        mock_os_unlink.assert_called_with("dummy_temp_fail.gpx")


    # 7. Test summarize_results (basic robustness)
    @patch('builtins.print') # Mock print to suppress output during test
    def test_summarize_results_runs_with_mixed_data(self, mock_print):
        sample_photo_data = [
            ("file1.jpg", "2023:01:01 10:00:00", "Location A"),
            ("file2.jpg", "2023:01:01 11:00:00", None),
            ("file3.nef", "2022:12:15 15:30:00", "Location B"),
            ("file4.mp4", "invalid_date_format", "Location C"), # Invalid date
            ("file5.jpg", None, None), # None date
        ]
        file_counts = Counter({"jpg": 3, "nef": 1, "mp4": 1})
        
        try:
            # Test with quiet=False to exercise more code, but mock print
            # debug_mode=True to exercise debug logging paths (which are suppressed by default)
            summarize_results(sample_photo_data, file_counts, quiet=False, debug_mode=True)
            # Test with quiet=True
            summarize_results(sample_photo_data, file_counts, quiet=True, debug_mode=False)
        except Exception as e:
            self.fail(f"summarize_results raised an exception with mixed data: {e}")


    # 8. Test _parse_arguments
    def test_parse_arguments_defaults(self):
        args = _parse_arguments([])
        self.assertEqual(args.directory, ".")
        self.assertEqual(args.output, "photo.dates.txt")
        self.assertFalse(args.quiet)
        self.assertFalse(args.debug)
        self.assertFalse(args.video)
        self.assertFalse(args.only_photos)
        self.assertIsNone(args.extension)
        self.assertFalse(args.locate)

    def test_parse_arguments_custom(self):
        cmd_args = [
            "--directory", "/custom/dir",
            "-o", "my_output.txt",
            "--quiet",
            "--debug", # Note: quiet might override debug's logging level effect
            "--video",
            "--locate"
        ]
        args = _parse_arguments(cmd_args)
        self.assertEqual(args.directory, "/custom/dir")
        self.assertEqual(args.output, "my_output.txt")
        self.assertTrue(args.quiet)
        self.assertTrue(args.debug)
        self.assertTrue(args.video)
        self.assertTrue(args.locate)

    def test_parse_arguments_extension(self):
        args = _parse_arguments(["--extension", "cr2"])
        self.assertEqual(args.extension, "cr2")

    # 9. Test find_files (simplified)
    def test_find_files_basic(self):
        # Create a temporary directory structure for testing find_files
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some dummy files
            os.makedirs(os.path.join(tmpdir, "subdir"))
            with open(os.path.join(tmpdir, "file1.jpg"), "w") as f: f.write("")
            with open(os.path.join(tmpdir, "file2.txt"), "w") as f: f.write("")
            with open(os.path.join(tmpdir, "subdir", "file3.nef"), "w") as f: f.write("")
            with open(os.path.join(tmpdir, "subdir", "file4.mp4"), "w") as f: f.write("")

            extensions = {"jpg", "nef"}
            found_files_list = sorted(list(find_files(tmpdir, extensions)))
            
            expected_files = sorted([
                os.path.join(tmpdir, "file1.jpg"),
                os.path.join(tmpdir, "subdir", "file3.nef")
            ])
            self.assertEqual(found_files_list, expected_files)

            extensions_video = {"mp4"}
            found_video_files = list(find_files(tmpdir, extensions_video))
            self.assertEqual(len(found_video_files), 1)
            self.assertIn(os.path.join(tmpdir, "subdir", "file4.mp4"), found_video_files[0])


if __name__ == '__main__':
    # Configure logging to be off during tests, unless a test specifically wants to check log output
    logging.disable(logging.CRITICAL) 
    unittest.main()

# Note: For more complex find_files testing (e.g., symlinks, permissions),
# a more elaborate setup or patching os.walk might be needed.
# For geolocate's temp file deletion, the current mock_os_unlink covers it.
# A more direct way for temp file in geolocate:
# In test_geolocate_success_and_cache:
# After the first call to geolocate, you could assert that mock_os_unlink was called
# with the specific temporary file name that mock_tempfile.NamedTemporaryFile was set up with.
# The current setup with mock_os_unlink.assert_called_with("dummy_temp.gpx") is good.
# Also, the test for geolocate_exiftool_fails also checks mock_os_unlink.
