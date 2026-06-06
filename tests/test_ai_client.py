# AI client 抽象回归（unittest，纯内存、不联网、不需要 anthropic SDK / key）
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import ai_scoring_schema as schema
from api.ai_client import (MockScoringClient, AnthropicScoringClient, get_client,
                           MOCK_CONFIDENCE_CAP, _metric_quality)
from api.ai_output_validator import validate_ai_output
from api.evidence_packet import build_evidence_packet

_ROW = {
    "ticker": "MSFT", "canonical_ticker": "MSFT", "market": "US",
    "roe_5y_avg": "43.2", "gross_margin_5y_avg": "69.5", "net_margin_5y_avg": "35.8",
    "fcf_positive_years": "5", "roic_5y_avg": "30.2",
    "revenue_growth_5y_cagr": "14", "eps_growth_5y_cagr": "18",
    "debt_to_equity": "0.4", "pe": "32.4", "fcf_yield": "2.8",
    "data_date": "2026-06-01", "_fin_updated_at": "2026-06-03",
}
_PACKET = build_evidence_packet(_ROW, as_of_date="2026-06-06")


class TestGetClient(unittest.TestCase):
    def test_default_is_mock(self):
        self.assertIsInstance(get_client(""), MockScoringClient)
        self.assertIsInstance(get_client("mock"), MockScoringClient)

    def test_none_provider_returns_none(self):
        self.assertIsNone(get_client("none"))
        self.assertIsNone(get_client("off"))

    def test_anthropic_provider(self):
        self.assertIsInstance(get_client("anthropic"), AnthropicScoringClient)

    def test_unknown_falls_back_to_mock(self):
        self.assertIsInstance(get_client("definitely-not-a-provider"), MockScoringClient)


class TestMockClient(unittest.TestCase):
    def test_mock_output_passes_validator(self):
        out = MockScoringClient().score(_PACKET)
        status, _validated, errs = validate_ai_output(out, _PACKET)
        self.assertIn(status, (schema.VALID_PASSED, schema.VALID_REPAIRED), errs)

    def test_mock_confidence_capped_low(self):
        out = MockScoringClient().score(_PACKET)
        self.assertLessEqual(out["confidence"], MOCK_CONFIDENCE_CAP)

    def test_mock_deterministic(self):
        self.assertEqual(MockScoringClient().score(_PACKET), MockScoringClient().score(_PACKET))

    def test_mock_evidence_only_present_metrics(self):
        out = MockScoringClient().score(_PACKET)
        present = {f for f, info in _PACKET["metrics"].items() if info["value"] is not None}
        for item in out["strengths"] + out["risks"]:
            self.assertIn(item["evidence_metric"], schema.EVIDENCE_METRICS)
            self.assertIn(item["evidence_metric"], present)

    def test_mock_no_advice_wording(self):
        out = MockScoringClient().score(_PACKET)
        self.assertFalse(schema.has_advice(out["summary"]))
        for it in out["strengths"] + out["risks"]:
            self.assertFalse(schema.has_advice(it["point"]))

    def test_negative_roe_roic_quality_zero(self):
        # 负权益致负 ROE/ROIC：质量应为 0，而非地板分 20
        self.assertEqual(_metric_quality("roe_5y_avg", -244.0), 0)
        self.assertEqual(_metric_quality("roic_5y_avg", -10.0), 0)
        self.assertEqual(_metric_quality("roe_5y_avg", 43.2), 100)


class TestAnthropicClientNoKey(unittest.TestCase):
    def test_unavailable_without_key(self):
        saved = {k: os.environ.pop(k, None) for k in ("AI_SCORING_API_KEY", "ANTHROPIC_API_KEY")}
        try:
            self.assertFalse(AnthropicScoringClient()._has_key())
            self.assertFalse(AnthropicScoringClient().available())
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_default_model_id(self):
        self.assertEqual(AnthropicScoringClient().model_name, "claude-opus-4-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
