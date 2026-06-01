# ============================================================
# ai_analysis.py  —  芒格式选股研究引擎 v2.3.0-alpha2
#
# 职责：用纯规则 HeuristicProvider 生成「AI 初步质化判断」。
#       不接 LLM、无第三方依赖。为 alpha4 的 LLM 后端预留同一接口。
#
# 铁律（与项目目标一致）：
#   1. 只产出 ai_* 字段，结构上不返回任何人工字段名。
#   2. 数据不足时不编分：ai_confidence=0，moat/management 留空。
#   3. 所有判断措辞为"可能 / 待证实 / 需人工复核"，绝不写确定结论。
#   4. 置信度封顶 0.5——启发式再"好"也不声称高确定性。
#   5. needs_human_review 恒为 true（AI 永不权威）。
#
# ai_moat_score / ai_management_score 均为 0–10（与人工原始录入同尺度）。
# 风险用文本标记写入 ai_risk_flags（ai_risk_score 保留备用，不在此写）。
# ============================================================

import sys
from dataclasses import dataclass, field
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL_NAME       = "heuristic-v1"
CONFIDENCE_CAP   = 0.5      # 启发式置信度上限
_KEY_METRICS     = ["roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg",
                    "net_margin_5y_avg", "revenue_growth_5y_cagr", "debt_to_equity"]
_MIN_METRICS     = 3        # 少于这么多关键指标 → 判定数据不足，不编分

# 周期性行业关键词（中英文）
_CYCLICAL_HINTS  = ["energy", "材料", "material", "industrial", "工业", "auto", "汽车",
                    "semiconductor", "半导体", "钢", "煤", "化工", "地产", "real estate"]


@dataclass
class AIQualitative:
    ai_moat_score: str = ""
    ai_management_score: str = ""
    ai_risk_flags: str = ""
    ai_confidence: str = "0.0"
    ai_reason: str = ""
    ai_evidence_needed: str = ""
    ai_model: str = MODEL_NAME
    ai_generated_at: str = ""
    needs_human_review: str = "true"

    def to_store_dict(self):
        """返回写入 store 的 ai_* 字典（不含 ai_risk_score，保留备用）。"""
        return {
            "ai_moat_score":       self.ai_moat_score,
            "ai_management_score": self.ai_management_score,
            "ai_risk_flags":       self.ai_risk_flags,
            "ai_confidence":       self.ai_confidence,
            "ai_reason":           self.ai_reason,
            "ai_evidence_needed":  self.ai_evidence_needed,
            "ai_model":            self.ai_model,
            "ai_generated_at":     self.ai_generated_at,
            "needs_human_review":  self.needs_human_review,
        }


def _sfn(val):
    """安全转 float，空/无效返回 None。"""
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in ("", "nan", "none", "n/a", "null"):
        return None
    try:
        f = float(s)
        return None if f != f else f
    except (ValueError, TypeError):
        return None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class HeuristicProvider:
    """纯规则质化初判。确定性、低置信度、证据导向。"""
    name = MODEL_NAME

    def analyze(self, metrics: dict) -> AIQualitative:
        present = {k: _sfn(metrics.get(k)) for k in _KEY_METRICS}
        n_present = sum(1 for v in present.values() if v is not None)
        is_pending = bool(metrics.get("is_pending"))

        # ── 数据不足 → 不编分 ───────────────────────────────────
        if is_pending or n_present < _MIN_METRICS:
            return AIQualitative(
                ai_moat_score="", ai_management_score="", ai_risk_flags="",
                ai_confidence="0.0",
                ai_reason="数据不足，无法生成质化初判，需人工复核。",
                ai_evidence_needed="先补齐财务数据与年报（运行 fetcher / 人工导入）后再评估。",
                ai_model=self.name, ai_generated_at=_now(), needs_human_review="true",
            )

        gm   = present["gross_margin_5y_avg"]
        roic = present["roic_5y_avg"]
        nm   = present["net_margin_5y_avg"]
        de   = present["debt_to_equity"]
        fcf_yrs = _sfn(metrics.get("fcf_positive_years")) or 0
        pe   = _sfn(metrics.get("pe"))

        # ── 护城河（0–10，财务可给"可能"信号）──────────────────
        if (gm or 0) >= 50 and (roic or 0) >= 20:
            moat = 7
        elif (gm or 0) >= 30 or (roic or 0) >= 15:
            moat = 5
        else:
            moat = 3
        # 利润率趋势下滑则下调 1
        if str(metrics.get("margin_trend", "")).strip().lower() == "declining":
            moat = max(0, moat - 1)

        # ── 管理层（0–10，财务证据很弱，仅给中性偏保守）────────
        mgmt = 6 if (fcf_yrs >= 4 and de is not None and 0 <= de <= 2) else 4

        # ── 风险标记（文本）────────────────────────────────────
        flags = []
        if de is not None and de > 3:                  flags.append("高杠杆")
        if pe is not None and pe > 40:                 flags.append("估值偏高")
        if nm is not None and nm < 0:                  flags.append("盈利能力弱")
        declining = sum(1 for t in ("roe_trend", "roic_trend", "margin_trend", "revenue_trend")
                        if str(metrics.get(t, "")).strip().lower() == "declining")
        if declining >= 2:                             flags.append("趋势恶化")
        sct = (str(metrics.get("sector", "")) + str(metrics.get("industry", ""))).lower()
        if any(h in sct for h in _CYCLICAL_HINTS):     flags.append("周期性")

        # ── 置信度：按数据完整度，封顶 0.5 ─────────────────────
        confidence = round(min(CONFIDENCE_CAP, n_present / len(_KEY_METRICS) * CONFIDENCE_CAP), 2)

        reason = (
            f"高毛利率({gm}%)/ROIC({roic}%) 等财务特征"
            f"{'可能存在定价权或护城河' if moat >= 6 else '显示护城河可能一般'}"
            f"（待证实，需看竞争格局与转换成本）；"
            f"管理层质量仅凭财务无法判断，需人工复核年报与资本配置历史。"
        )
        evidence = (
            "护城河：竞争格局/转换成本/市占率趋势/年报护城河描述；"
            "管理层：资本配置历史/股东回报记录/高管诚信。"
        )

        return AIQualitative(
            ai_moat_score=str(moat),
            ai_management_score=str(mgmt),
            ai_risk_flags="; ".join(flags),
            ai_confidence=str(confidence),
            ai_reason=reason,
            ai_evidence_needed=evidence,
            ai_model=self.name,
            ai_generated_at=_now(),
            needs_human_review="true",
        )


def analyze(metrics: dict, provider=None) -> AIQualitative:
    """顶层入口。默认 HeuristicProvider；alpha4 可传入 LLM provider。"""
    provider = provider or HeuristicProvider()
    return provider.analyze(metrics)


# ── 自检入口 ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 70)
    print("  ai_analysis 自检（HeuristicProvider，纯规则）")
    print("=" * 70)
    good = {
        "roe_5y_avg": 43, "roic_5y_avg": 30, "gross_margin_5y_avg": 69,
        "net_margin_5y_avg": 35, "revenue_growth_5y_cagr": 14, "debt_to_equity": 0.4,
        "fcf_positive_years": 5, "pe": 32, "sector": "Technology", "margin_trend": "improving",
    }
    insufficient = {"is_pending": True}
    print("\n>>> 财务充分（示例科技股）：")
    print("   ", analyze(good).to_store_dict())
    print("\n>>> 数据不足（A股待补录）：")
    print("   ", analyze(insufficient).to_store_dict())
