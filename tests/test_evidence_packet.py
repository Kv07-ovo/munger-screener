# evidence packet builder 回归（unittest，纯内存、不联网、不碰真实 data/）
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.evidence_packet import build_evidence_packet

_AS_OF = "2026-06-06"

_US_FULL = {
    "ticker": "MSFT", "canonical_ticker": "MSFT", "name": "Microsoft",
    "long_name": "Microsoft Corp", "market": "US", "currency": "USD",
    "sector": "Technology", "industry": "Software",
    "roe_5y_avg": "43.2", "gross_margin_5y_avg": "69.5", "net_margin_5y_avg": "35.8",
    "fcf_positive_years": "5", "roic_5y_avg": "30.2",
    "revenue_growth_5y_cagr": "14", "eps_growth_5y_cagr": "18",
    "debt_to_equity": "0.4", "pe": "32.4", "fcf_yield": "2.8", "pe_percentile_5y": "70",
    "pb": "12", "market_cap": "",
    "roe_trend": "stable", "roic_trend": "improving", "margin_trend": "stable",
    "revenue_trend": "improving",
    "moat_score": "9", "management_score": "8", "moat_reason": "网络效应+切换成本",
    "notes": "护城河强",
    "data_date": "2026-06-01", "_fin_updated_at": "2026-06-03",
    "financial_data_years": "2021-2025", "_fin_data_warning": "",
}


class TestPacketNormal(unittest.TestCase):
    def setUp(self):
        self.p = build_evidence_packet(_US_FULL, as_of_date=_AS_OF)

    def test_identity_and_version(self):
        self.assertEqual(self.p["ticker"], "MSFT")
        self.assertEqual(self.p["company_name"], "Microsoft Corp")
        self.assertEqual(self.p["market"], "US")
        self.assertEqual(self.p["scoring_rubric_version"], "ai_evidence_v1")
        self.assertIn("MSFT@2026-06-06", self.p["evidence_packet_id"])

    def test_units_not_rescaled(self):
        # 百分比/比率原样保留，不二次缩放
        self.assertEqual(self.p["metrics"]["roe_5y_avg"]["value"], 43.2)
        self.assertEqual(self.p["metrics"]["roe_5y_avg"]["unit"], "percent")
        self.assertEqual(self.p["metrics"]["debt_to_equity"]["value"], 0.4)
        self.assertEqual(self.p["metrics"]["debt_to_equity"]["unit"], "ratio")
        self.assertEqual(self.p["metrics"]["pe"]["value"], 32.4)

    def test_trends_categorical(self):
        self.assertEqual(self.p["metrics"]["roic_trend"]["value"], "improving")

    def test_full_data_confidence(self):
        self.assertEqual(self.p["missing_fields"], [])
        self.assertEqual(self.p["data_confidence"], 1.0)

    def test_source_dates(self):
        self.assertEqual(self.p["source_dates"]["valuation_as_of"], "2026-06-01")
        self.assertEqual(self.p["source_dates"]["fundamentals_as_of"], "2026-06-03")

    def test_human_numeric_scores_excluded_from_evidence(self):
        # 人工 moat_score / management_score 绝不进 metrics（不作评分证据），只在 _legacy_reference
        self.assertNotIn("moat_score", self.p["metrics"])
        self.assertNotIn("management_score", self.p["metrics"])
        self.assertEqual(self.p["_legacy_reference"]["human_moat_score"], 9.0)
        self.assertEqual(self.p["_legacy_reference"]["human_management_score"], 8.0)

    def test_human_qualitative_notes_as_evidence(self):
        self.assertEqual(self.p["human_notes"].get("moat_reason"), "网络效应+切换成本")
        self.assertEqual(self.p["human_notes"].get("notes"), "护城河强")


class TestPacketMissing(unittest.TestCase):
    def test_missing_fields_marked_not_filled(self):
        row = dict(_US_FULL)
        row["roe_5y_avg"] = ""
        row["pe"] = "manual_pending"
        p = build_evidence_packet(row, as_of_date=_AS_OF)
        self.assertIsNone(p["metrics"]["roe_5y_avg"]["value"])
        self.assertIsNone(p["metrics"]["pe"]["value"])
        self.assertIn("roe_5y_avg", p["missing_fields"])
        self.assertIn("pe", p["missing_fields"])
        self.assertLess(p["data_confidence"], 1.0)


class TestPacketStale(unittest.TestCase):
    def test_old_valuation_date_marks_stale(self):
        row = dict(_US_FULL)
        row["data_date"] = "2025-01-01"   # 距 as_of 2026-06-06 > 400 天
        p = build_evidence_packet(row, as_of_date=_AS_OF)
        self.assertIn("pe", p["stale_fields"])
        self.assertIn("fcf_yield", p["stale_fields"])


class TestPacketAShare(unittest.TestCase):
    def test_ashare_calibre(self):
        row = {
            "ticker": "600519.SH", "canonical_ticker": "600519.SH", "name": "贵州茅台",
            "market": "CN", "currency": "CNY", "industry": "白酒",
            "roe_5y_avg": "30", "gross_margin_5y_avg": "91", "net_margin_5y_avg": "52",
            "fcf_positive_years": "5", "roic_5y_avg": "",   # A股 ROIC 缺失
            "debt_to_equity": "0.1", "pe": "30", "fcf_yield": "2.5",
            "market_cap": "16024.92",
            "data_date": "2026-06-01", "_fin_updated_at": "2026-06-01",
        }
        p = build_evidence_packet(row, as_of_date=_AS_OF)
        self.assertEqual(p["market"], "CN")
        self.assertIsNone(p["metrics"]["roic_5y_avg"]["value"])
        self.assertEqual(p["metrics"]["market_cap"]["unit"], "亿CNY")
        joined = " ".join(p["data_warnings"])
        self.assertIn("ROIC", joined)            # 提示 A股 ROIC 缺失
        self.assertIn("亿CNY", joined)            # 提示市值口径


class TestPacketNegativeEquity(unittest.TestCase):
    def test_negative_de_with_warning_surfaced(self):
        # MCD 类：合并后 D/E 为负 + 年度表标记负权益 → 必须暴露负权益失真提示
        row = dict(_US_FULL)
        row["debt_to_equity"] = "-30.6"
        row["roe_5y_avg"] = "-244"
        row["_fin_data_warning"] = "debt_to_equity<0; ROE异常"
        p = build_evidence_packet(row, as_of_date=_AS_OF)
        joined = " ".join(p["data_warnings"])
        self.assertIn("负权益", joined)
        self.assertEqual(p["metrics"]["debt_to_equity"]["value"], -30.6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
