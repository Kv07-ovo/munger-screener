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

    # ── ai_evidence_v1：新主分链路 ────────────────────────────────
    def test_ai_evidence_fields_present(self):
        from api import adapters
        p = adapters.build_research_payload("MSFT")
        if not p.get("ok"):
            self.skipTest("本机无 MSFT 数据")
        for k in ("ai_generated", "scoring_method", "scoring_rubric_version",
                  "validator_status", "ai_breakdown", "source_dates",
                  "missing_data_impact", "disclaimer", "generated_at"):
            self.assertIn(k, p)
        self.assertIsInstance(p["ai_breakdown"], dict)
        json.dumps(p)   # 仍须 JSON 安全

    def test_main_score_comes_from_ai_not_legacy(self):
        from api import adapters
        p = adapters.build_research_payload("MSFT")
        if not (p.get("ok") and p.get("total_score") is not None):
            self.skipTest("本机无 MSFT 数据或不可用")
        ai = (p.get("raw") or {}).get("ai_evidence") or {}
        # 响应主分 == AI 证据评分；scoring_method 标注真实来源
        self.assertEqual(p["total_score"], ai.get("total_score"))
        self.assertIn(p["scoring_method"], ("ai_mock", "ai_llm"))
        self.assertEqual(p["scoring_rubric_version"], "ai_evidence_v1")
        # legacy scorer 的分仍存在于 raw（仅参考），但不是响应主分来源
        self.assertIn("total_score", p.get("raw") or {})

    def test_default_scoring_method_is_mock(self):
        if os.getenv("AI_SCORING_PROVIDER"):
            self.skipTest("环境已配置 provider")
        from api import adapters
        p = adapters.build_research_payload("MSFT")
        if not (p.get("ok") and p.get("total_score") is not None):
            self.skipTest("本机无 MSFT 数据")
        self.assertEqual(p["scoring_method"], "ai_mock")
        self.assertFalse(p["ai_generated"])   # mock 非真实 AI

    def test_ai_unavailable_does_not_crash(self):
        from api import adapters
        old = os.environ.get("AI_SCORING_PROVIDER")
        os.environ["AI_SCORING_PROVIDER"] = "none"
        try:
            p = adapters.build_research_payload("MSFT")
        finally:
            if old is None:
                os.environ.pop("AI_SCORING_PROVIDER", None)
            else:
                os.environ["AI_SCORING_PROVIDER"] = old
        json.dumps(p)
        if p.get("ok"):
            self.assertEqual(p["scoring_method"], "unavailable")
            self.assertIsNone(p["total_score"])
            self.assertEqual(p["final_score_preview"], p["total_score"])  # 仍保持不变量

    def test_run_research_exception_returns_state_error(self):
        # 故障注入：run_research 抛异常时，adapters 必须返回结构化 state='error'（不外泄、不崩）。
        import research_service
        from api import adapters
        orig = research_service.run_research

        def _boom(*args, **kwargs):
            raise RuntimeError("injected failure")

        research_service.run_research = _boom
        try:
            p = adapters.build_research_payload("AAPL")
        finally:
            research_service.run_research = orig   # 必须恢复，避免污染其他测试
        self.assertFalse(p["ok"])
        self.assertEqual(p["state"], "error")
        self.assertTrue(str(p.get("message", "")).strip())   # message 存在且非空（不锁全文）
        self.assertEqual(p.get("ticker"), "AAPL")            # ticker 回显
        json.dumps(p)                                        # 仍须 JSON 安全

    def test_success_payload_contract_keys_present(self):
        # 契约守卫：成功态 payload 必须含前端 ResearchResult 消费的最小稳定键集。
        # 只断言键存在，不锁定具体分数 / 长文本；允许后端有额外字段（如 score_breakdown）。
        from api import adapters
        p = None
        for t in ("AAPL", "MSFT"):
            cand = adapters.build_research_payload(t)
            if cand.get("ok") and cand.get("state") in ("complete", "pending"):
                p = cand
                break
        if p is None:
            self.skipTest("本机无 complete/pending 数据（AAPL/MSFT）")
        for k in ("ok", "state", "ticker", "canonical", "company_name",
                  "total_score", "summary",
                  "strengths", "risks", "scoring_method",
                  "scoring_rubric_version", "ai_breakdown", "financials"):
            self.assertIn(k, p)
        self.assertIsInstance(p["financials"], dict)
        json.dumps(p)

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
