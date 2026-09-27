import tempfile
import unittest
from pathlib import Path

from beacon.pipeline import count_words, run
from beacon.sources import discover


class BeaconTests(unittest.TestCase):
    def test_pipeline_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            (data / "one.txt").write_text("alpha beta\n", encoding="utf-8")
            report = root / "report.txt"
            rows = run(data, report)
            self.assertEqual(rows, [("one", 2)])
            self.assertEqual(report.read_text(encoding="utf-8"), "one: 2\n")

    def test_discover_is_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "b.txt").write_text("x", encoding="utf-8")
            (root / "a.txt").write_text("x", encoding="utf-8")
            self.assertEqual([item.name for item in discover(root)], ["a.txt", "b.txt"])

    def test_count_words_handles_empty(self) -> None:
        self.assertEqual(count_words(""), 0)
