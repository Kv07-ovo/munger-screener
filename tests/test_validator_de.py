# validator 负/异常 D/E 风险门测试（unittest，纯函数、无 I/O、不碰真实 data/）
#
# 修复目标：validator 的 de>3 风险门此前只判 de>3，
#   - de<=0（负权益/异常 D/E，如 MCD=-30.6）会被漏判；
#   - de 缺失不应被误判为高负债，但应保持「关键字段缺失/数据不足」逻辑。
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import validator


def _row(de):
    """一行较完整的输入（仅 debt_to_equity 变化），用于隔离 D/E 判定。"""
    return {
        "ticker": "TST", "name": "Test", "circle_of_competence": "edge",
        "roe_5y_avg": "20", "gross_margin_5y_avg": "40", "net_margin_5y_avg": "20",
        "roic_5y_avg": "15", "pe": "20", "fcf_yield": "3",
        "revenue_growth_5y_cagr": "8", "eps_growth_5y_cagr": "8",
        "fcf_positive_years": "5", "debt_to_equity": de,
        "moat_score": "6", "management_score": "6", "confidence_score": "8",
        "risk_note": "",
    }


def _has(warnings, needle):
    return any(needle in w for w in warnings)


# ============================================================
# 1) validate_data：D/E 警告
# ============================================================
class TestValidateDataDE(unittest.TestCase):
    def _warn(self, de):
        warnings, _note = validator.validate_data(_row(de))
        return warnings

    def test_negative_de_abnormal_warning(self):
        w = self._warn("-30.6")
        self.assertTrue(_has(w, "D/E异常"))         # 负权益异常风险提示
        self.assertFalse(_has(w, "D/E过高"))        # 不是「过高」那条

    def test_zero_de_abnormal_warning(self):
        self.assertTrue(_has(self._warn("0"), "D/E异常"))

    def test_blank_de_no_debt_risk_warning(self):
        w = self._warn("")
        self.assertFalse(_has(w, "D/E异常"))         # 缺失不判异常
        self.assertFalse(_has(w, "D/E过高"))         # 缺失不判高负债
        self.assertTrue(_has(w, "关键字段缺失: debt_to_equity"))  # 仍走缺失逻辑

    def test_high_de_still_warns(self):
        w = self._warn("4")
        self.assertTrue(_has(w, "D/E过高"))
        self.assertFalse(_has(w, "D/E异常"))

    def test_normal_de_no_debt_warning(self):
        for de in ("0.5", "1", "2"):
            w = self._warn(de)
            self.assertFalse(_has(w, "D/E过高"), f"de={de} 不应判高负债")
            self.assertFalse(_has(w, "D/E异常"), f"de={de} 不应判异常")


# ============================================================
# 2) get_final_decision：风险门
# ============================================================
class TestFinalDecisionDE(unittest.TestCase):
    def _decision(self, de):
        return validator.get_final_decision({"total_score": 80}, _row(de))

    def test_negative_de_triggers_risk(self):
        self.assertEqual(self._decision("-30.6"), "风险过高")

    def test_zero_de_triggers_risk(self):
        self.assertEqual(self._decision("0"), "风险过高")

    def test_high_de_triggers_risk(self):
        self.assertEqual(self._decision("4"), "风险过高")

    def test_normal_de_not_risk(self):
        for de in ("0.5", "1", "2"):
            self.assertNotEqual(self._decision(de), "风险过高", f"de={de} 不应判风险过高")

    def test_blank_de_not_high_debt_risk(self):
        # 缺失 D/E：不应被误判为高负债风险（缺失走数据不足/缺失逻辑，而非 风险过高）
        self.assertNotEqual(self._decision(""), "风险过高")


if __name__ == "__main__":
    unittest.main(verbosity=2)
