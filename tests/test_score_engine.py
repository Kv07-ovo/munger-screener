# 动态评分 MVP 测试（unittest，纯函数、无 I/O、不碰真实 data/）
#
# 覆盖两层：
#   1) TestDebtToEquityFix / TestScoreStockClamp —— scorer.py 的 D/E 合法性修复 + 上限保护。
#      这些只 import scorer，可在「实现前」直接跑出 RED（证明 bug 可复现）。
#   2) TestScoreEngine —— 新增 api/score_engine.py 的 None-aware 重归一化 + data_confidence
#      + score_breakdown（惰性 import，实现前为 ERROR）。
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scorer


# ============================================================
# 1) scorer.py：D/E 合法性修复（缺失/负值不得拿低负债满分）
# ============================================================
class TestDebtToEquityFix(unittest.TestCase):
    def _de_points(self, de):
        _, detail = scorer.score_balance_sheet({"debt_to_equity": de, "fcf_positive_years": "0"})
        return detail["负债率评分"]

    def test_negative_de_not_full_marks(self):
        # MCD：负权益导致 D/E=-30.6，修复前命中 de<=0.5 拿满分 10（bug）
        self.assertEqual(self._de_points("-30.622"), 0)

    def test_blank_de_not_full_marks(self):
        # 空 D/E：修复前 _safe_float('')=0.0 → de<=0.5 → 白送 10（bug）
        self.assertEqual(self._de_points(""), 0)

    def test_zero_de_not_full_marks(self):
        self.assertEqual(self._de_points("0"), 0)

    def test_valid_low_de_keeps_full_marks(self):
        # 真正低负债（0.4）仍应满分 10（不被误伤）
        self.assertEqual(self._de_points("0.4"), 10)

    def test_valid_mid_de_unchanged(self):
        self.assertEqual(self._de_points("1.5"), 3)


class TestScoreStockClamp(unittest.TestCase):
    def test_total_score_upper_clamp_100(self):
        # 满配行（各维度满分 + 人工满分），total 不得 > 100
        row = {
            "roe_5y_avg": "40", "gross_margin_5y_avg": "70", "net_margin_5y_avg": "30",
            "fcf_positive_years": "5", "roic_5y_avg": "30",
            "moat_score": "10", "revenue_growth_5y_cagr": "20", "eps_growth_5y_cagr": "20",
            "debt_to_equity": "0.3", "pe": "10", "fcf_yield": "8", "pe_percentile_5y": "10",
            "management_score": "10",
        }
        out = scorer.score_stock(row)
        self.assertLessEqual(out["total_score"], 100.0)
        self.assertEqual(out["total_score"], 100.0)

    def test_negative_de_no_longer_inflates_balance(self):
        # 集成层面：MCD 式负 D/E 不再让资产负债维拿满分
        row = {"debt_to_equity": "-30.622", "fcf_positive_years": "5"}
        out = scorer.score_stock(row)
        self.assertLess(out["balance_sheet_score"], 15.0)  # 不再 10(D/E)+5(FCF)=15


# ============================================================
# 2) api/score_engine.py：None-aware 重归一化 + data_confidence + score_breakdown
# ============================================================
class TestScoreEngine(unittest.TestCase):
    def _compute(self, row):
        from api import score_engine
        return score_engine.compute(row)

    def _dim(self, out, name):
        for d in out["score_breakdown"]:
            if d["dimension"] == name:
                return d
        self.fail(f"维度 {name} 不在 score_breakdown 中")

    # ---- 完整美股（MSFT-like）：引擎与 legacy 同分，confidence=1.0 ----
    def _msft_row(self):
        return {
            "roe_5y_avg": "38", "gross_margin_5y_avg": "69", "net_margin_5y_avg": "34",
            "fcf_positive_years": "5", "roic_5y_avg": "28",
            "revenue_growth_5y_cagr": "16", "eps_growth_5y_cagr": "20",
            "debt_to_equity": "0.6", "pe": "32", "fcf_yield": "2.8", "pe_percentile_5y": "72",
            "moat_score": "8", "management_score": "8",
        }

    def test_complete_stock_parity_with_legacy(self):
        row = self._msft_row()
        out = self._compute(row)
        legacy = scorer.score_stock(row)["total_score"]
        # 完整数据：重归一化不改变结果 → 引擎总分 == legacy 总分
        self.assertEqual(out["total_score"], legacy)
        self.assertEqual(out["data_confidence"], 1.0)

    def test_breakdown_structure(self):
        out = self._compute(self._msft_row())
        for d in out["score_breakdown"]:
            for key in ("dimension", "score", "max_score", "used_fields", "missing_fields", "notes"):
                self.assertIn(key, d)

    # ---- MCD 负 D/E 回归：资产负债维不得满分 ----
    def test_mcd_negative_de_balance_not_max(self):
        row = {
            "roe_5y_avg": "20", "gross_margin_5y_avg": "40", "net_margin_5y_avg": "25",
            "fcf_positive_years": "5", "roic_5y_avg": "15",
            "revenue_growth_5y_cagr": "6", "eps_growth_5y_cagr": "6",
            "debt_to_equity": "-30.622", "pe": "26", "fcf_yield": "3",
            "moat_score": "8", "management_score": "7",
        }
        out = self._compute(row)
        bs = self._dim(out, "balance_sheet")
        self.assertLess(bs["score"], 15.0)              # 不满分
        self.assertEqual(bs["score"], 5.0)              # (D/E 0/10 + FCF 5/5) 重归一化到 15 → 5
        self.assertIn("debt_to_equity", bs["used_fields"])   # 有值（但异常），非缺失
        self.assertNotIn("debt_to_equity", bs["missing_fields"])
        self.assertTrue(any("D/E" in n or "debt" in n.lower() for n in bs["notes"]))

    # ---- 空 D/E：不得白送满分；进入缺失 ----
    def test_blank_de_no_phantom_points(self):
        row = {"debt_to_equity": "", "fcf_positive_years": "0"}
        out = self._compute(row)
        bs = self._dim(out, "balance_sheet")
        self.assertEqual(bs["score"], 0.0)              # 没有任何低负债白送分
        self.assertIn("debt_to_equity", bs["missing_fields"])

    # ---- A 股 ROIC 缺失：不按 0 惩罚，从质量维分母中排除 ----
    def test_ashare_missing_roic_not_penalized(self):
        row = {
            "roe_5y_avg": "25", "gross_margin_5y_avg": "55", "net_margin_5y_avg": "40",
            "fcf_positive_years": "5",  # roic 缺失
        }
        out = self._compute(row)
        q = self._dim(out, "quality")
        self.assertIn("roic_5y_avg", q["missing_fields"])
        # 已有四项均满分 → 重归一化后质量维仍满分 30（缺失不拖累）
        self.assertEqual(q["score"], 30.0)

    # ---- 全空 ticker：total=0，data_confidence 低 ----
    def test_all_empty_zero_and_low_confidence(self):
        out = self._compute({})
        self.assertEqual(out["total_score"], 0.0)
        self.assertEqual(out["data_confidence"], 0.0)

    # ---- data_confidence：6/10 字段 ----
    def test_data_confidence_six_of_ten(self):
        row = {
            "roe_5y_avg": "20", "gross_margin_5y_avg": "40", "net_margin_5y_avg": "20",
            "fcf_positive_years": "5", "pe": "20", "fcf_yield": "3",
            # 缺：roic_5y_avg, revenue_growth_5y_cagr, eps_growth_5y_cagr, debt_to_equity
        }
        out = self._compute(row)
        self.assertEqual(out["data_confidence"], 0.6)

    # ---- 上限：引擎总分不得 > 100 ----
    def test_engine_total_upper_bound(self):
        row = {
            "roe_5y_avg": "40", "gross_margin_5y_avg": "70", "net_margin_5y_avg": "30",
            "fcf_positive_years": "5", "roic_5y_avg": "30",
            "revenue_growth_5y_cagr": "20", "eps_growth_5y_cagr": "20",
            "debt_to_equity": "0.3", "pe": "10", "fcf_yield": "8", "pe_percentile_5y": "10",
            "moat_score": "10", "management_score": "10",
        }
        out = self._compute(row)
        self.assertLessEqual(out["total_score"], 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
