# API 闭环回归（unittest）：测试 api.adapters 这一框架无关核心层。
#
# 两层覆盖：
#   1) TestResearchApi  —— 直接单测框架无关核心 api/adapters.py（纯函数，无需 web 框架 / HTTP）。
#   2) TestResearchHttp —— 经 starlette.testclient.TestClient 端到端测真实 ASGI app 的 HTTP 路由：
#      离线、不起 uvicorn；装了 fastapi 时 app 即 FastAPI 实例，验证的就是 FastAPI 路由行为。
#      该类需 httpx（见 requirements-api.txt），缺失则 skip，不影响其余测试全绿。
# 仅当本机装有 pandas 时运行（research_service 依赖 pandas）；否则整文件 skip。
import json
import os
import sys
import importlib.util
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAS_PANDAS = importlib.util.find_spec("pandas") is not None
HAS_HTTPX = importlib.util.find_spec("httpx") is not None
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


@unittest.skipUnless(HAS_PANDAS and HAS_HTTPX, "需要 pandas + httpx（TestClient 端到端）")
class TestResearchHttp(unittest.TestCase):
    """经 TestClient 真实走一遍 ASGI app 的 HTTP 路由（离线、不起 uvicorn、可重复）。"""

    @classmethod
    def setUpClass(cls):
        from starlette.testclient import TestClient   # httpx 驱动；对 FastAPI/Starlette app 通用
        from api import main
        cls.client = TestClient(main.app)
        cls.backend = getattr(main, "BACKEND", "?")

    def test_health_http(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})

    def test_research_aapl_http(self):
        r = self.client.get("/api/research", params={"ticker": "AAPL"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/json", r.headers.get("content-type", ""))
        d = r.json()
        self.assertEqual(d["ticker"], "AAPL")
        self.assertIn(d["state"], ("complete", "pending", "insufficient_data"))
        # 成功态：锁定稳定不变量，不锁脆弱长文本
        if d["ok"] and d.get("total_score") is not None:
            self.assertEqual(d["canonical"], "AAPL")
            self.assertTrue(isinstance(d.get("company_name"), str) and d["company_name"])
            self.assertEqual(d["final_score_preview"], d["total_score"])  # AI 不参与正式分

    def test_invalid_ticker_http(self):
        r = self.client.get("/api/research", params={"ticker": "ZZZ!!!"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertFalse(d["ok"])
        self.assertEqual(d["state"], "invalid_ticker")
        self.assertIn("message", d)

    def test_missing_ticker_http(self):
        # 不带 ticker 参数 -> 空串 -> 结构化 invalid_ticker（200，不崩）
        r = self.client.get("/api/research")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["state"], "invalid_ticker")

    def test_lowercase_ticker_normalized_http(self):
        # 小写 ticker 规范化：aapl 若被识别，canonical 应为大写 AAPL（守卫式断言，不脆弱）
        r = self.client.get("/api/research", params={"ticker": "aapl"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        if d.get("ok"):
            self.assertEqual(d["canonical"], "AAPL")

    def test_uses_fastapi_when_available_http(self):
        # 装了 requirements-api.txt 时应跑在 FastAPI（非 Starlette 应急回退）
        import importlib.util
        if importlib.util.find_spec("fastapi") is not None:
            self.assertEqual(self.backend, "fastapi")


@unittest.skipUnless(
    HAS_PANDAS and (importlib.util.find_spec("fastapi") is not None
                    or importlib.util.find_spec("starlette") is not None),
    "需要 pandas + (fastapi 或 starlette) 才能 import api.main")
class TestCorsOrigins(unittest.TestCase):
    """CORS 来源 env 化解析：去空格/空项、去重保序、丢 '*'、空回退本地默认。"""

    def _parse(self, raw):
        from api import main
        return main._parse_allowed_origins(raw)

    _LOCAL = ["http://localhost:5173", "http://127.0.0.1:5173"]

    def test_empty_falls_back_to_local_defaults(self):
        self.assertEqual(self._parse(""), self._LOCAL)
        self.assertEqual(self._parse("   "), self._LOCAL)
        self.assertEqual(self._parse(",, , "), self._LOCAL)

    def test_parses_comma_separated(self):
        self.assertEqual(
            self._parse("https://a.vercel.app,https://b.com"),
            ["https://a.vercel.app", "https://b.com"])

    def test_strips_whitespace_and_empty_items(self):
        self.assertEqual(
            self._parse("  https://a.com , , https://b.com  "),
            ["https://a.com", "https://b.com"])

    def test_drops_wildcard(self):
        # 仅 '*' → 回退本地默认；混入 '*' → 丢弃 '*' 保留其余
        self.assertEqual(self._parse("*"), self._LOCAL)
        self.assertEqual(self._parse("https://a.com, *"), ["https://a.com"])

    def test_dedup_preserves_order(self):
        self.assertEqual(
            self._parse("https://a.com, https://a.com, https://b.com"),
            ["https://a.com", "https://b.com"])

    def test_default_reads_env_when_unset(self):
        # raw=None 时读 os.getenv；测试环境未设 ALLOWED_ORIGINS → 本地默认
        import os
        if not os.getenv("ALLOWED_ORIGINS"):
            from api import main
            self.assertEqual(main._parse_allowed_origins(), self._LOCAL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
