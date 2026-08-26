import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.capcut_tools import _image_folder_groups, _name_prefix


class ImageFolderPrefixTests(unittest.TestCase):
    def test_dotted_directory_name_keeps_text_after_dot(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            folder = Path(temporary_root) / "5.Anh_full_prompt_anh"
            folder.mkdir()

            self.assertEqual(_name_prefix(folder), "5.anh")
            self.assertEqual(
                _image_folder_groups(temporary_root),
                {"5.anh": [folder]},
            )

    def test_timeline_file_removes_extension_before_extracting_prefix(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            timeline = Path(temporary_root) / "5.Anh_full.json"
            timeline.touch()

            self.assertEqual(_name_prefix(timeline), "5.anh")


if __name__ == "__main__":
    unittest.main()
