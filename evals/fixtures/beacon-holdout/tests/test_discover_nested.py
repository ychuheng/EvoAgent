import tempfile
import unittest
from pathlib import Path

from beacon.sources import discover


class DiscoverNestedTests(unittest.TestCase):
    def test_finds_nested_text_files(self) -> None:
        # 期望：递归找到子目录里的文本来源。当前实现只匹配一层 glob。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "archive").mkdir()
            (root / "archive" / "old.txt").write_text("x", encoding="utf-8")
            (root / "top.txt").write_text("x", encoding="utf-8")
            self.assertEqual([item.name for item in discover(root)], ["old.txt", "top.txt"])
