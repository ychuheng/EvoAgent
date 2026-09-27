import unittest

from beacon.pipeline import average_words


class PipelineStatsTests(unittest.TestCase):
    def test_average_of_empty_is_zero(self) -> None:
        # 期望：没有来源时平均词数为 0。当前实现会抛 ZeroDivisionError。
        self.assertEqual(average_words([]), 0.0)
