# ticker_resolver 规范化测试（unittest，纯函数、无 I/O）
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ticker_resolver import resolve


class TestTickerResolver(unittest.TestCase):
    def test_ashare_numeric_to_sh(self):
        r = resolve("600519")
        self.assertEqual(r.canonical, "600519.SH")
        self.assertEqual(r.market, "CN")

    def test_us_ticker(self):
        r = resolve("AAPL")
        self.assertEqual(r.canonical, "AAPL")
        self.assertEqual(r.market, "US")

    def test_unknown_input(self):
        r = resolve("12AB!")
        self.assertEqual(r.market, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
