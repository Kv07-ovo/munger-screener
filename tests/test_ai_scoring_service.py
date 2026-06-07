# AI 证据评分编排（service）回归（unittest，纯内存、默认 mock、不联网）
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import ai_scoring_schema as schema
from api.ai_scoring_service import score_with_ai
from api.ai_client import MockScoringClient

_ROW = {
    "ticker": "MSFT", "canonical_ticker": "MSFT", "name": "Microsoft", "market": "US",
    "roe_5y_avg": "43.2", "gross_margin_5y_avg": "69.5", "net_margin_5y_avg": "35.8",
    "fcf_positive_years": "5", "roic_5y_avg": "30.2",
    "revenue_growth_5y_cagr": "14", "eps_growth_5y_cagr": "18",
    "debt_to_equity": "0.4", "pe": "32.4", "fcf_yield": "2.8", "pe_percentile_5y": "70",
    "roe_trend": "stable", "roic_trend": "improving", "margin_trend": "stable",
    "revenue_trend": "improving",
    "data_date": "2026-06-01", "_fin_updated_at": "2026-06-03", "financial_data_years": "2021-2025",
}

_FIXED_TS = "2026-06-06T00:00:00+00:00"


class _UnavailClient:
    method = schema.METHOD_LLM
    model_name = "x"
    def available(self): return False
    def score(self, packet, prompt): raise AssertionError("不应被调用")


class _BadClient:
    method = schema.METHOD_LLM
    model_name = "x"
    def available(self): return True
    def score(self, packet, prompt): return {"total_score": 999, "rating": "优质"}  # 缺键


class _BoomClient:
    method = schema.METHOD_LLM
    model_name = "x"
    def available(self): return True
    def score(self, packet, prompt): raise RuntimeError("provider boom")


class _LLMClient:
    """模拟通过校验的真实 LLM：复用 mock 的合法输出形态。"""
    method = schema.METHOD_LLM
    model_name = "fake-llm"
    def available(self): return True
    def score(self, packet, prompt): return MockScoringClient().score(packet)


class TestMockDefault(unittest.TestCase):
    def setUp(self):
        self.r = score_with_ai(_ROW, client=MockScoringClient(), generated_at=_FIXED_TS)

    def test_method_and_flags(self):
        self.assertEqual(self.r["scoring_method"], schema.METHOD_MOCK)
        self.assertFalse(self.r["ai_generated"])      # mock 非真实 AI
        self.assertEqual(self.r["validator_status"], schema.VALID_PASSED)

    def test_total_consistent_with_breakdown(self):
        self.assertIsNotNone(self.r["total_score"])
        self.assertLessEqual(abs(self.r["total_score"] - round(sum(self.r["breakdown"].values()), 2)),
                             schema.TOTAL_BREAKDOWN_TOLERANCE + 0.01)

    def test_metadata_present(self):
        for k in ("scoring_rubric_version", "generated_at", "evidence_packet_id",
                  "source_dates", "missing_fields", "stale_fields", "data_confidence", "disclaimer"):
            self.assertIn(k, self.r)
        self.assertEqual(self.r["generated_at"], _FIXED_TS)
        self.assertIn("不构成", self.r["disclaimer"])

    def test_confidence_capped(self):
        self.assertLessEqual(self.r["confidence"], schema.CONFIDENCE_CAP)


class TestUnavailable(unittest.TestCase):
    def test_no_provider_via_env(self):
        old = os.environ.get("AI_SCORING_PROVIDER")
        os.environ["AI_SCORING_PROVIDER"] = "none"
        try:
            r = score_with_ai(_ROW)   # client=None → 按 env 选择 → None → 不可用
        finally:
            if old is None:
                os.environ.pop("AI_SCORING_PROVIDER", None)
            else:
                os.environ["AI_SCORING_PROVIDER"] = old
        self.assertEqual(r["scoring_method"], schema.METHOD_UNAVAILABLE)
        self.assertIsNone(r["total_score"])
        self.assertEqual(r["rating"], schema.RATING_UNAVAILABLE)
        self.assertFalse(r["ai_generated"])

    def test_unavailable_client_injected(self):
        r = score_with_ai(_ROW, client=_UnavailClient())
        self.assertEqual(r["scoring_method"], schema.METHOD_UNAVAILABLE)
        self.assertIsNone(r["total_score"])


class TestFailurePaths(unittest.TestCase):
    def test_validation_failure_becomes_unavailable(self):
        r = score_with_ai(_ROW, client=_BadClient())
        self.assertEqual(r["scoring_method"], schema.METHOD_UNAVAILABLE)
        self.assertEqual(r["validator_status"], schema.VALID_FAILED)
        self.assertIsNone(r["total_score"])
        self.assertFalse(r["ai_generated"])

    def test_provider_exception_becomes_unavailable(self):
        r = score_with_ai(_ROW, client=_BoomClient())
        self.assertEqual(r["scoring_method"], schema.METHOD_UNAVAILABLE)
        self.assertIsNone(r["total_score"])


class TestRealLLMShape(unittest.TestCase):
    def test_valid_llm_marks_ai_generated(self):
        r = score_with_ai(_ROW, client=_LLMClient())
        self.assertEqual(r["scoring_method"], schema.METHOD_LLM)
        self.assertTrue(r["ai_generated"])
        self.assertEqual(r["validator_status"], schema.VALID_PASSED)
        self.assertIsNotNone(r["total_score"])


class TestDeterminism(unittest.TestCase):
    def test_same_input_same_total(self):
        a = score_with_ai(_ROW, client=MockScoringClient(), generated_at=_FIXED_TS)
        b = score_with_ai(_ROW, client=MockScoringClient(), generated_at=_FIXED_TS)
        self.assertEqual(a["total_score"], b["total_score"])
        self.assertEqual(a["breakdown"], b["breakdown"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
