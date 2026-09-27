"""fixture 自检：目录结构与"坏文件"都必须在预期位置。"""

import tempfile
import unittest
from pathlib import Path

from organize_lab.check import classify_file, plan_targets


class OrganizeLabTests(unittest.TestCase):
    def test_classify_by_suffix(self) -> None:
        self.assertEqual(classify_file(Path("a.md")), "notes")
        self.assertEqual(classify_file(Path("a.pdf")), "reports")
        self.assertIsNone(classify_file(Path("a.bin")))

    def test_plan_skips_unknown_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("x", encoding="utf-8")
            (root / "b.bin").write_bytes(b"\x00\x01")
            plan = plan_targets(root)
            self.assertEqual([item.name for item in plan], ["a.md"])
