# AI 动态评分层回归测试（unittest，纯内存、不联网、不碰真实 data/）
import os
import sys
import copy
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_scorer
from ai_score_schema import validate_ai_score, CONFIDENCE_CAP, AI_RATING_ENUM, _has_advice


# 充分数据（6 个关键指标齐全且可评分）
GOOD = {
    "company_type": {"industry": "软件", "sector": "Technology", "is_special_financial": False},
    "metrics": {
        "roe_5y_avg": "30", "roic_5y_avg": "25", "gross_margin_5y_avg": "65",
        "net_margin_5y_avg": "20", "revenue_growth_5y_cagr": "15",
        "eps_growth_5y_cagr": "18", "debt_to_equity": "0.4", "pe": "22",
        "fcf_yield": "4.5", "fcf_positive_years": "5",
        "pe_percentile_5y": "60", "pb": "8", "market_cap": "2000",
        "roe_trend": "stable", "roic_trend": "stable",
        "margin_trend": "improving", "revenue_trend": "stable",
    },
    "missing_fields": {"missing_quant_fields": [], "missing_quant_labels": [],
                       "data_status": "完整", "is_pending": False},
    "rule_based_context": {"rule_based_score": 80, "rule_based_score_max": 100,
                           "dimension_scores": {}},
}

PENDING = {
    "company_type": {"industry": "Unknown", "sector": "", "is_special_financial": False},
    "metrics": {},
    "missing_fields": {
        "missing_quant_fields": ["pe", "fcf_yield", "roic_5y_avg"],
        "missing_quant_labels": ["市盈率PE", "自由现金流收益率", "ROIC(5年均值)"],
        "data_status": "待补录", "is_pending": True,
    },
    "rule_based_context": {"rule_based_score": 0, "rule_based_score_max": 100, "dimension_scores": {}},
}


def _variant_missing_two():
    """从 GOOD 去掉 pe、fcf_yield（仍 >=3 关键指标可评），并把它们标为缺失。"""
    p = copy.deepcopy(GOOD)
    p["metrics"]["pe"] = ""
    p["metrics"]["fcf_yield"] = ""
    p["missing_fields"]["missing_quant_fields"] = ["pe", "fcf_yield"]
    p["missing_fields"]["missing_quant_labels"] = ["市盈率PE", "自由现金流收益率"]
    p["missing_fields"]["data_status"] = "待补录"
    return p


class _BoomProvider:
    def score(self, payload):
        raise RuntimeError("boom")


class _BadOutProvider:
    class _R:
        def to_dict(self):  # 越界 ai_score → schema 校验应拒绝
            return {"ai_score": 999, "ai_rating": "质优", "ai_reasoning": "x",
                    "key_strengths": [], "key_risks": [], "missing_data_warnings": [],
                    "confidence": 0.5, "needs_human_review": True}
    def score(self, payload):
        return self._R()


class TestValidOutput(unittest.TestCase):
    def test_valid_output_passes_schema(self):
        out = ai_scorer.score_dynamic(GOOD)
        self.assertIsNotNone(out)
        ok, errors = validate_ai_score(out)
        self.assertTrue(ok, errors)
        self.assertIsInstance(out["ai_score"], (int, float))
        self.assertTrue(0 <= out["ai_score"] <= 100)
        self.assertIn(out["ai_rating"], AI_RATING_ENUM)
        self.assertLessEqual(out["confidence"], CONFIDENCE_CAP)
        self.assertIs(out["needs_human_review"], True)

    def test_output_has_exactly_eight_keys(self):
        out = ai_scorer.score_dynamic(GOOD)
        self.assertEqual(set(out.keys()), {
            "ai_score", "ai_rating", "ai_reasoning", "key_strengths", "key_risks",
            "missing_data_warnings", "confidence", "needs_human_review"})
        # Phase 1 不得泄露时间戳/模型名进主输出（保证可复现）
        self.assertNotIn("ai_generated_at", out)
        self.assertNotIn("ai_model", out)


class TestInsufficientData(unittest.TestCase):
    def test_is_pending_insufficient(self):
        out = ai_scorer.score_dynamic(PENDING)
        self.assertIsNotNone(out)            # 数据不足是"成功的判定"，非失败
        self.assertIsNone(out["ai_score"])
        self.assertEqual(out["ai_rating"], "数据不足")
        self.assertEqual(out["confidence"], 0.0)
        self.assertIs(out["needs_human_review"], True)
        self.assertEqual(out["key_strengths"], [])
        self.assertEqual(out["key_risks"], [])

    def test_too_few_metrics_insufficient(self):
        p = copy.deepcopy(GOOD)
        p["metrics"] = {"roe_5y_avg": "30"}   # 仅 1 个关键指标
        p["missing_fields"]["is_pending"] = False
        out = ai_scorer.score_dynamic(p)
        self.assertEqual(out["ai_rating"], "数据不足")
        self.assertIsNone(out["ai_score"])
        self.assertEqual(out["confidence"], 0.0)


class TestNoFabrication(unittest.TestCase):
    def test_missing_warnings_echoed_exactly(self):
        p = _variant_missing_two()
        out = ai_scorer.score_dynamic(p)
        self.assertIsNotNone(out)
        self.assertEqual(out["missing_data_warnings"], ["市盈率PE", "自由现金流收益率"])

    def test_pending_warnings_echoed(self):
        out = ai_scorer.score_dynamic(PENDING)
        self.assertEqual(out["missing_data_warnings"], PENDING["missing_fields"]["missing_quant_labels"])

    def test_evidence_metric_only_nonnull(self):
        out = ai_scorer.score_dynamic(GOOD)
        for item in out["key_strengths"] + out["key_risks"]:
            em = item["evidence_metric"]
            self.assertIsNotNone(ai_scorer._sfn(GOOD["metrics"].get(em)),
                                 f"{em} 被引用但在输入中为空")


class TestConfidence(unittest.TestCase):
    def test_confidence_capped(self):
        out = ai_scorer.score_dynamic(GOOD)
        self.assertLessEqual(out["confidence"], 0.5)
        self.assertGreaterEqual(out["confidence"], 0.0)

    def test_confidence_downgraded_on_missing(self):
        full = ai_scorer.score_dynamic(GOOD)
        partial = ai_scorer.score_dynamic(_variant_missing_two())
        self.assertLess(partial["confidence"], full["confidence"])


class TestCompliance(unittest.TestCase):
    def test_no_investment_advice_wording(self):
        for payload in (GOOD, PENDING, _variant_missing_two()):
            out = ai_scorer.score_dynamic(payload)
            self.assertFalse(_has_advice(out["ai_reasoning"]))
            self.assertFalse(_has_advice(out["ai_rating"]))
            for it in out["key_strengths"] + out["key_risks"]:
                self.assertFalse(_has_advice(it["point"]))


class TestDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        self.assertEqual(ai_scorer.score_dynamic(GOOD), ai_scorer.score_dynamic(GOOD))

    def test_ai_weight_is_zero_phase1(self):
        self.assertEqual(ai_scorer.AI_WEIGHT, 0.0)


class TestGracefulFallback(unittest.TestCase):
    def test_provider_exception_returns_none(self):
        self.assertIsNone(ai_scorer.score_dynamic(GOOD, provider=_BoomProvider()))

    def test_invalid_output_returns_none(self):
        self.assertIsNone(ai_scorer.score_dynamic(GOOD, provider=_BadOutProvider()))

    def test_none_payload_does_not_crash(self):
        # 极端：空 payload 不应抛异常（视为数据不足或失败，但绝不崩）
        out = ai_scorer.score_dynamic(None)
        self.assertTrue(out is None or out["ai_rating"] == "数据不足")


class TestFinalPreview(unittest.TestCase):
    def test_weight_zero_returns_rule_based(self):
        self.assertEqual(ai_scorer.compute_final_preview(80, 91.0), 80.0)

    def test_none_ai_score_returns_rule_based(self):
        self.assertEqual(ai_scorer.compute_final_preview(80, None), 80.0)

    def test_nonzero_weight_blends(self):
        self.assertEqual(ai_scorer.compute_final_preview(80, 90, ai_weight=0.5), 85.0)


class TestSchemaValidator(unittest.TestCase):
    def test_empty_dict_rejected(self):
        ok, errors = validate_ai_score({})
        self.assertFalse(ok)
        self.assertTrue(errors)

    def test_advice_wording_rejected(self):
        out = ai_scorer.score_dynamic(GOOD)
        out["ai_reasoning"] = "建议买入该股票"
        ok, _ = validate_ai_score(out)
        self.assertFalse(ok)

    def test_needs_human_review_must_be_true(self):
        out = ai_scorer.score_dynamic(GOOD)
        out["needs_human_review"] = False
        ok, _ = validate_ai_score(out)
        self.assertFalse(ok)

    def test_rating_score_consistency(self):
        out = ai_scorer.score_dynamic(GOOD)
        out["ai_rating"] = "数据不足"      # 但 ai_score 非 None → 应判不一致
        ok, _ = validate_ai_score(out)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
