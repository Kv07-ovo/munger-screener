# prompt/rubric builder 回归（unittest，纯内存）
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import ai_scoring_schema as schema
from api.prompt_builder import build_scoring_prompt
from api.evidence_packet import build_evidence_packet

_ROW = {
    "ticker": "MSFT", "canonical_ticker": "MSFT", "market": "US",
    "roe_5y_avg": "43.2", "pe": "32.4", "fcf_yield": "2.8", "debt_to_equity": "0.4",
    "moat_score": "9", "management_score": "8", "moat_reason": "网络效应",
    "data_date": "2026-06-01",
}
_PROMPT = build_scoring_prompt(build_evidence_packet(_ROW, as_of_date="2026-06-06"))


class TestPromptBuilder(unittest.TestCase):
    def test_has_system_and_user(self):
        self.assertTrue(_PROMPT["system"].strip())
        self.assertTrue(_PROMPT["user"].strip())
        self.assertEqual(_PROMPT["rubric_version"], schema.SCORING_RUBRIC_VERSION)

    def test_system_encodes_rubric_dims(self):
        for name in schema.DIMENSION_NAMES:
            self.assertIn(name, _PROMPT["system"])

    def test_system_forbids_advice_and_fabrication(self):
        self.assertIn("编造", _PROMPT["system"])
        self.assertIn("投资建议", _PROMPT["system"])

    def test_user_does_not_leak_human_numeric_scores(self):
        # 人工 moat_score / management_score 的数值与 _legacy_reference 绝不进 prompt
        self.assertNotIn("_legacy_reference", _PROMPT["user"])
        self.assertNotIn("human_moat_score", _PROMPT["user"])
        self.assertNotIn("management_score", _PROMPT["user"])

    def test_user_includes_qualitative_note_as_evidence(self):
        self.assertIn("网络效应", _PROMPT["user"])   # 人工定性理由作为证据可入 prompt

    def test_user_lists_allowed_evidence_metrics(self):
        self.assertIn("roe_5y_avg", _PROMPT["user"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
