import unittest

from beacon.report import render


class ReportEdgeTests(unittest.TestCase):
    def test_skips_blank_rows(self) -> None:
        # 期望：名称为空的行不出现在报告里。当前实现会多输出一个空行。
        self.assertEqual(render([("", 0), ("alpha", 3)]), "alpha: 3")
