# API 闭环回归（unittest）：测试 api.adapters 这一框架无关核心层。
#
# 设计：不依赖 FastAPI / httpx / TestClient（本环境未装 FastAPI、httpx）。
#   真正的 API 逻辑在 api/adapters.py，是纯函数 -> 直接单测即可全面覆盖 Phase 2 要求。
#   api/main.py 只是把这些函数挂到 /health 与 /api/research，故另有一个「app 能构建且路由齐全」的轻量检查。
# 仅当本机装有 pandas 时运行（research_service 依赖 pandas）；否则整文件 skip。
import json
import os
import sys
import importlib.util
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAS_PANDAS = importlib.util.find_spec("pandas") is not None
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STOCKS_CSV = os.path.join(_ROOT, "data", "stocks.csv")


@unittest.skipUnless(HAS_PANDAS, "需要 pandas（api.adapters -> research_service）")
class TestResearchApi(unittest.TestCase):

    def test_health(self):
        from api import adapters
        self.assertEqual(adapters.health_payload(), {"ok": True})

    def test_research_aapl_does_not_crash_and_is_json_safe(self):
        from api import adapters
        payload = adapters.build_research_payload("AAPL")
        # 必须可 JSON 序列化（防 numpy/Decimal 等非序列化对象漏给前端）
        json.dumps(payload)
        self.assertIn(payload.get("state"), ("complete", "pending", "insufficient_data", "invalid_ticker"))
        self.assertEqual(payload.get("ticker"), "AAPL")

    def test_final_score_preview_equals_total(self):
        # 本机 data/ 含 AAPL：成功态下 preview 必须 == total（AI 不参与正式分）
        from api import adapters
        payload = adapters.build_research_payload("AAPL")
        if payload.get("ok") and payload.get("total_score") is not None:
            self.assertEqual(payload["final_score_preview"], payload["total_score"])
        # 无论本地是否有数据，底层不变量都必须成立
        import ai_scorer
        self.assertEqual(ai_scorer.AI_WEIGHT, 0.0)
        self.assertEqual(ai_scorer.compute_final_preview(80.0, 91.0), 80.0)
        self.assertEqual(ai_scorer.compute_final_preview(80.0, None), 80.0)

    def test_invalid_ticker_returns_structured_error(self):
        from api import adapters
        payload = adapters.build_research_payload("ZZZ!!!")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["state"], "invalid_ticker")
        self.assertIn("message", payload)
        json.dumps(payload)

    def test_empty_ticker_is_handled(self):
        from api import adapters
        payload = adapters.build_research_payload("   ")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["state"], "invalid_ticker")

    def test_api_does_not_mutate_stocks_csv(self):
        # 只读保证：调用前后 data/stocks.csv 的 (大小, mtime) 必须完全不变
        from api import adapters
        before = (os.path.getsize(_STOCKS_CSV), os.path.getmtime(_STOCKS_CSV))
        adapters.build_research_payload("AAPL")
        adapters.build_research_payload("MSFT")
        after = (os.path.getsize(_STOCKS_CSV), os.path.getmtime(_STOCKS_CSV))
        self.assertEqual(before, after, "API 调用不得写 data/stocks.csv")

    def test_ai_weight_logic_untouched(self):
        import ai_scorer
        self.assertEqual(ai_scorer.AI_WEIGHT, 0.0)

    @unittest.skipUnless(
        importlib.util.find_spec("fastapi") is not None
        or importlib.util.find_spec("starlette") is not None,
        "需要 fastapi 或 starlette 才能构建 ASGI app")
    def test_app_constructs_with_expected_routes(self):
        from api import main
        self.assertTrue(hasattr(main, "app"))
        paths = set()
        for r in getattr(main.app, "routes", []):
            p = getattr(r, "path", None)
            if p:
                paths.add(p)
        self.assertIn("/health", paths)
        self.assertIn("/api/research", paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
