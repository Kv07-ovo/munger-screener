# 评分回归测试（unittest，纯函数、无 I/O、不碰真实 data/）
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorer import score_valuation, score_business_quality


class TestFcfYieldPercentage(unittest.TestCase):
    """fcf_yield 百分比口径回归：源头统一 *100 后，scorer 按百分比阈值评分。"""

    def _fcf_score(self, fcf_yield):
        _, detail = score_valuation(
            {"pe": "20", "fcf_yield": fcf_yield, "pe_percentile_5y": ""})
        return detail["FCF收益率评分"]

    def test_high_yield_percentage_full_marks(self):
        # 8.9%（百分比口径）→ 满分 6；修复前小数 0.089 会被判 0
        self.assertEqual(self._fcf_score("8.9"), 6)

    def test_mid_yield_percentage(self):
        self.assertEqual(self._fcf_score("3"), 4)   # >=3 → 4

    def test_sub_one_percent_scores_zero(self):
        # 0.5（百分比即 0.5%）→ 0 分，证明阈值是百分比口径而非小数
        self.assertEqual(self._fcf_score("0.5"), 0)


class TestPePercentileEmpty(unittest.TestCase):
    """pe_percentile_5y 空/缺失/不可解析 → adj=0，不再白送 +1。"""

    def _adj(self, pe_pct):
        _, detail = score_valuation(
            {"pe": "30", "fcf_yield": "0", "pe_percentile_5y": pe_pct})
        return detail["PE分位调整"]

    def test_empty_no_bonus(self):
        self.assertEqual(self._adj(""), "0（无分位数据）")

    def test_unparseable_no_bonus(self):
        self.assertEqual(self._adj("abc"), "0（无分位数据）")

    def test_low_percentile_still_bonus(self):
        self.assertEqual(self._adj("10"), "+1（历史低位）")

    def test_high_percentile_penalty(self):
        self.assertEqual(self._adj("90"), "-3（90%历史高位）")

    def test_empty_total_excludes_the_plus_one(self):
        empty_total, _ = score_valuation(
            {"pe": "30", "fcf_yield": "0", "pe_percentile_5y": ""})
        low_total, _ = score_valuation(
            {"pe": "30", "fcf_yield": "0", "pe_percentile_5y": "10"})
        # 唯一差异是分位调整：空值不再凭空多 1 分
        self.assertEqual(low_total - empty_total, 1)


class TestBusinessQualitySentinel(unittest.TestCase):
    """阈值哨兵：防止 scorer 阈值被误改。"""

    def test_roe_threshold(self):
        _, detail = score_business_quality({"roe_5y_avg": "22"})
        self.assertEqual(detail["ROE评分"], 9)   # >=20 → 9


if __name__ == "__main__":
    unittest.main()
