import tempfile
import unittest
from pathlib import Path

from fieldnotes.cli import main
from fieldnotes.store import load_notes


class FieldnotesTests(unittest.TestCase):
    def test_add_and_list_use_the_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.json"
            self.assertEqual(main(["--file", str(path), "add", "Alpha", "first"]), "saved")
            self.assertEqual(main(["--file", str(path), "list"]), "Alpha: first")
            self.assertEqual(load_notes(path), [{"title": "Alpha", "body": "first"}])
