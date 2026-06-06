# AI 输出校验器回归（unittest，纯内存）
import os
import sys
import copy
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import ai_scoring_schema as schema
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


def _good():
    bd = {"business_quality": 28.0, "growth": 12.0, "balance_sheet": 14.0,
          "valuation": 8.0, "moat": 12.0, "management_governance": 4.0,
          "data_quality_adjustment": 2.0}
    return {
        "total_score": round(sum(bd.values()), 2),
        "rating": "优质", "confidence": 0.5, "breakdown": bd,
        "summary": "高质量公司，盈利能力与现金流稳健。",
        "strengths": [{"point": "ROE 偏强", "evidence_metric": "roe_5y_avg"}],
        "risks": [{"point": "估值不低", "evidence_metric": "pe"}],
        "missing_data_impact": "数据齐全。", "score_drivers": ["business_quality 强"],
        "evidence_used": ["roe_5y_avg", "pe"], "warnings": [],
    }


class TestValidatorPass(unittest.TestCase):
    def test_good_passes(self):
        status, out, errs = validate_ai_output(_good(), _PACKET)
        self.assertEqual(status, schema.VALID_PASSED, errs)
        self.assertAlmostEqual(out["total_score"], 80.0, places=1)
        self.assertEqual(out["rating"], "优质")

    def test_accepts_json_string(self):
        import json
        status, out, _ = validate_ai_output(json.dumps(_good()), _PACKET)
        self.assertEqual(status, schema.VALID_PASSED)


class TestValidatorFail(unittest.TestCase):
    def test_missing_key_fails(self):
        bad = _good(); del bad["breakdown"]
        status, out, errs = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_FAILED)
        self.assertIsNone(out["total_score"])
        self.assertTrue(schema.is_error_output(out))

    def test_non_json_string_fails(self):
        status, out, _ = validate_ai_output("not json at all", _PACKET)
        self.assertEqual(status, schema.VALID_FAILED)

    def test_breakdown_missing_dim_fails(self):
        bad = _good(); del bad["breakdown"]["moat"]
        status, _out, _ = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_FAILED)

    def test_advice_wording_fails(self):
        bad = _good(); bad["summary"] = "建议买入该股票"
        status, out, _ = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_FAILED)

    def test_confidence_non_numeric_fails(self):
        bad = _good(); bad["confidence"] = "high"
        status, _out, _ = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_FAILED)


class TestValidatorRepair(unittest.TestCase):
    def test_total_inconsistent_repaired(self):
        bad = _good(); bad["total_score"] = 5.0   # 与 breakdown 之和(80)严重不符
        status, out, _ = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_REPAIRED)
        self.assertAlmostEqual(out["total_score"], 80.0, places=1)

    def test_dim_out_of_range_clamped(self):
        bad = _good(); bad["breakdown"]["moat"] = 99.0   # 超 [0,15]
        status, out, _ = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_REPAIRED)
        self.assertLessEqual(out["breakdown"]["moat"], 15.0)

    def test_confidence_clamped(self):
        bad = _good(); bad["confidence"] = 5.0
        status, out, _ = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_REPAIRED)
        self.assertLessEqual(out["confidence"], schema.CONFIDENCE_CAP)

    def test_rating_invalid_repaired_from_score(self):
        bad = _good(); bad["rating"] = "强烈推荐"
        status, out, _ = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_REPAIRED)
        self.assertIn(out["rating"], schema.AI_RATING_ENUM)

    def test_hallucinated_metric_name_dropped(self):
        bad = _good()
        bad["strengths"] = [{"point": "看起来很强", "evidence_metric": "made_up_metric"}]
        status, out, _ = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_REPAIRED)
        self.assertEqual(out["strengths"], [])

    def test_unsupported_evidence_dropped(self):
        # market_cap 在 EVIDENCE_METRICS 内，但本证据包中为缺失（US 行无市值）→ 防幻觉丢弃
        bad = _good()
        bad["strengths"] = [{"point": "市值大", "evidence_metric": "market_cap"}]
        status, out, _ = validate_ai_output(bad, _PACKET)
        self.assertEqual(status, schema.VALID_REPAIRED)
        self.assertEqual(out["strengths"], [])

    def test_confidence_capped_by_data_completeness(self):
        # 证据包仅 1 个核心指标 → data_confidence 低 → 即使 AI 自报高 confidence 也被压低
        sparse = build_evidence_packet({"ticker": "Z", "canonical_ticker": "Z",
                                        "market": "US", "roe_5y_avg": "40"},
                                       as_of_date="2026-06-06")
        out_raw = _good(); out_raw["confidence"] = 0.9
        status, out, _ = validate_ai_output(out_raw, sparse)
        self.assertIn(status, (schema.VALID_REPAIRED, schema.VALID_PASSED))
        self.assertLess(out["confidence"], 0.9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
