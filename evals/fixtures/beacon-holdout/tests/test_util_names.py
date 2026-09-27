import unittest

from beacon.util import normalize_name


class UtilNameTests(unittest.TestCase):
    def test_strips_surrounding_spaces(self) -> None:
        # 期望：小写化并裁掉前后空格。当前实现只做小写。
        self.assertEqual(normalize_name("  Alpha "), "alpha")
