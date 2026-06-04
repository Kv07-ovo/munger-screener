# store 白名单保护回归测试（unittest + tempfile；隔离写盘，绝不碰真实 data/stocks.csv）
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store
from ticker_resolver import resolve


class TestStoreWhitelist(unittest.TestCase):
    def setUp(self):
        # 把模块级 STOCKS_PATH 指向临时目录，备份/写盘都落在 tmp 内
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = store.STOCKS_PATH
        store.STOCKS_PATH = os.path.join(self._tmpdir.name, "stocks.csv")
        store.upsert_skeleton(resolve("AAPL"))

    def tearDown(self):
        store.STOCKS_PATH = self._orig_path
        self._tmpdir.cleanup()

    def test_machine_write_rejects_manual_field(self):
        updated, rejected = store.update_machine_fields(
            "AAPL", {"pe": "20", "moat_score": "9"})
        self.assertIn("pe", updated)
        self.assertIn("moat_score", rejected)
        # 人工字段绝不被机器写入污染
        self.assertEqual(store.get("AAPL")["moat_score"], "")
        self.assertEqual(store.get("AAPL")["pe"], "20")

    def test_ai_write_rejects_manual_and_machine_fields(self):
        updated, rejected = store.update_ai_fields(
            "AAPL", {"ai_moat_score": "7", "moat_score": "9", "pe": "20"})
        self.assertIn("ai_moat_score", updated)
        # AI 既不能写人工字段，也不能写机器字段
        self.assertIn("moat_score", rejected)
        self.assertIn("pe", rejected)
        self.assertEqual(store.get("AAPL")["ai_moat_score"], "7")
        self.assertEqual(store.get("AAPL")["moat_score"], "")


if __name__ == "__main__":
    unittest.main()
