import tempfile
import unittest
from pathlib import Path

from ledger.attribute import currency_of, total_for
from ledger.cli import main
from ledger.summary import render_summary


class LedgerTests(unittest.TestCase):
    def test_summary_groups_by_entity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            main(["add", "--file", str(path), "--entity", "alpha", "--amount", "5"])
            main(["add", "--file", str(path), "--entity", "alpha", "--amount", "7"])
            self.assertEqual(render_summary(path), "alpha: 12")
            self.assertEqual(total_for(path, "alpha"), 12)

    def test_missing_currency_defaults(self) -> None:
        self.assertEqual(currency_of({"entity": "alpha"}), "CNY")
