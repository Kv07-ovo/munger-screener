# ============================================================
# ai_scorer.py  —  AI 动态评分层（Phase 1：纯规则、旁路、可复现）
#
# 职责：基于【已结构化的指标 + 缺失字段标记 + 行业/公司类型 + 规则分上下文】，
#       产出 AI 动态评分（质量/估值/风险/确定性），与 deterministic scorer 完全旁路。
#
# 铁律（沿用 ai_analysis）：
#   1. 只产 ai_* 结构化结论，绝不返回/回写任何人工字段名。
#   2. 数据不足不编分：is_pending 或可用关键指标 < 3 → ai_score=None, rating=数据不足, confidence=0.0。
#   3. 缺失字段不脑补：missing_data_warnings 原样复用上游 validator 结果；
#      key_strengths/key_risks 只引用「输入中实际非空」的指标。
#   4. 置信度封顶 0.5；needs_human_review 恒 True。
#   5. 措辞为「可能/倾向/待证实/需人工复核」，绝不含投资建议。
#
# 设计约束：本模块 **不 import scorer**、不读写文件、不联网。纯函数确定性，
#           主输出固定 8 字段（不含时间戳/模型名）以保证「同输入同输出」可复现。
#
# 失败语义（由调用方区分）：
#   - 数据不足 = 一种「成功的判定」→ score_dynamic 返回合法的「数据不足」dict。
#   - provider 异常 / schema 校验失败 / 内部异常 = 失败 → score_dynamic 返回 None
#     （调用方据此把 res["ai_dynamic"] 置 None，UI 显示「AI 动态评分未生成」）。
# ============================================================

from dataclasses import dataclass, field

import ai_score_schema
from ai_score_schema import CONFIDENCE_CAP, validate_ai_score

MODEL_NAME = "heuristic-dyn-v1"      # 仅内部标识，不放入主输出（保证可复现）
AI_WEIGHT  = 0.0                     # Phase 1：final_score_preview 不吸收 AI 分
_MIN_METRICS = 3                     # 可用关键指标少于此 → 数据不足，不编分

# 关键量化指标（与 validator.KEY_QUANT_FIELDS 同集）
_KEY_METRICS = ["pe", "fcf_yield", "roic_5y_avg", "roe_5y_avg",
                "revenue_growth_5y_cagr", "debt_to_equity"]

_METRIC_LABELS = {
    "roe_5y_avg": "ROE(5年均值)", "roic_5y_avg": "ROIC(5年均值)",
    "gross_margin_5y_avg": "毛利率", "net_margin_5y_avg": "净利率",
    "revenue_growth_5y_cagr": "营收增速", "eps_growth_5y_cagr": "EPS增速",
    "pe": "市盈率PE", "fcf_yield": "自由现金流收益率",
    "debt_to_equity": "负债权益比D/E", "fcf_positive_years": "FCF为正年数",
}


def _sfn(val):
    """安全转 float，空/无效返回 None。"""
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in ("", "nan", "none", "n/a", "null", "unknown", "manual_pending"):
        return None
    try:
        f = float(s)
        return None if f != f else f
    except (ValueError, TypeError):
        return None


# ── 各指标 → 0..100 的确定性映射（仅对非空值；缺失返回 None 不参与）──────────
def _band(v, cuts_scores):
    """cuts_scores: [(阈值, 分), ...] 降序匹配第一个 v>=阈值；末项为兜底。"""
    for thr, sc in cuts_scores:
        if v >= thr:
            return sc
    return cuts_scores[-1][1]


def _score_metric(name, v):
    if v is None:
        return None
    if name == "roe_5y_avg":
        return _band(v, [(20, 100), (15, 80), (10, 60), (5, 40), (float("-inf"), 20)])
    if name == "roic_5y_avg":
        return _band(v, [(15, 100), (10, 80), (7, 60), (4, 40), (float("-inf"), 20)])
    if name == "gross_margin_5y_avg":
        return _band(v, [(60, 100), (40, 80), (25, 60), (15, 40), (float("-inf"), 20)])
    if name == "net_margin_5y_avg":
        return _band(v, [(20, 100), (12, 80), (6, 60), (0, 40), (float("-inf"), 0)])
    if name in ("revenue_growth_5y_cagr", "eps_growth_5y_cagr"):
        return _band(v, [(20, 100), (12, 80), (6, 60), (0, 40), (float("-inf"), 20)])
    if name == "pe":
        if v <= 0 or v > 500:           # 无效 PE 不参与
            return None
        return _band(v, [(40.0001, 20), (30.0001, 40), (22.0001, 60),
                         (15.0001, 80), (float("-inf"), 100)])
    if name == "fcf_yield":             # 百分比口径
        return _band(v, [(5, 100), (3, 80), (1, 60), (0, 40), (float("-inf"), 20)])
    if name == "debt_to_equity":
        return _band(v, [(3.0001, 10), (2.0001, 30), (1.0001, 50), (0.5001, 70), (0.3001, 90), (float("-inf"), 100)])
    if name == "fcf_positive_years":
        return _band(v, [(5, 100), (4, 80), (3, 60), (2, 40), (float("-inf"), 20)])
    return None


# 参与 ai_score 与 strengths/risks 的指标（含趋势之外的量化项）
_SCORED_FIELDS = ["roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg",
                  "revenue_growth_5y_cagr", "eps_growth_5y_cagr", "pe", "fcf_yield",
                  "debt_to_equity", "fcf_positive_years"]


@dataclass
class AIDynamicScore:
    ai_score: object = None                 # float | None
    ai_rating: str = "数据不足"
    ai_reasoning: str = ""
    key_strengths: list = field(default_factory=list)
    key_risks: list = field(default_factory=list)
    missing_data_warnings: list = field(default_factory=list)
    confidence: float = 0.0
    needs_human_review: bool = True

    def to_dict(self):
        """Phase 1 固定 8 字段（不含 ai_model / ai_generated_at）。"""
        return {
            "ai_score":              self.ai_score,
            "ai_rating":             self.ai_rating,
            "ai_reasoning":          self.ai_reasoning,
            "key_strengths":         list(self.key_strengths),
            "key_risks":             list(self.key_risks),
            "missing_data_warnings": list(self.missing_data_warnings),
            "confidence":            self.confidence,
            "needs_human_review":    True,
        }


def _rating_of(score):
    if score >= 75:
        return "质优"
    if score >= 60:
        return "质良"
    if score >= 45:
        return "中性"
    return "偏弱"


def _fmt_val(name, v):
    if name in ("pe", "debt_to_equity"):
        return f"{v:g}"
    if name == "fcf_positive_years":
        return f"{int(v)}年"
    return f"{v:g}%"


class HeuristicDynamicProvider:
    """纯规则 AI 动态评分：确定性、低置信、证据绑定、不脑补。"""
    name = MODEL_NAME

    def score(self, payload) -> AIDynamicScore:
        payload = payload or {}
        metrics = payload.get("metrics", {}) or {}
        mf      = payload.get("missing_fields", {}) or {}
        labels  = [str(x) for x in (mf.get("missing_quant_labels") or [])]
        is_pending = bool(mf.get("is_pending"))

        present_key = {k: _sfn(metrics.get(k)) for k in _KEY_METRICS}
        n_present = sum(1 for v in present_key.values() if v is not None)

        # ── 数据不足 → 不编分（合法判定，非失败）────────────────────
        if is_pending or n_present < _MIN_METRICS:
            return AIDynamicScore(
                ai_score=None, ai_rating="数据不足",
                ai_reasoning="数据不足，无法生成 AI 动态评分，需补录关键财务数据后复评。",
                key_strengths=[], key_risks=[],
                missing_data_warnings=labels, confidence=0.0, needs_human_review=True,
            )

        # ── 仅对非空指标做确定性映射 ────────────────────────────────
        scored = []   # (field, value, metric_score)
        for fld in _SCORED_FIELDS:
            v = _sfn(metrics.get(fld))
            ms = _score_metric(fld, v)
            if ms is not None:
                scored.append((fld, v, ms))

        if not scored:   # 关键指标够数但可评分指标为空（极端）→ 视为数据不足
            return AIDynamicScore(
                ai_score=None, ai_rating="数据不足",
                ai_reasoning="可用结构化指标不足以生成动态评分，需补录后复评。",
                key_strengths=[], key_risks=[],
                missing_data_warnings=labels, confidence=0.0, needs_human_review=True,
            )

        ai_score = round(sum(s for _, _, s in scored) / len(scored), 1)
        rating = _rating_of(ai_score)

        # 证据绑定：strengths=高分项，risks=低分项；只引用非空指标
        strengths = sorted([x for x in scored if x[2] >= 80], key=lambda t: t[2], reverse=True)[:5]
        risks     = sorted([x for x in scored if x[2] <= 40], key=lambda t: t[2])[:5]
        key_strengths = [{"point": f"{_METRIC_LABELS.get(f, f)} 数据上偏强（{_fmt_val(f, v)}）",
                          "evidence_metric": f} for f, v, _ in strengths]
        key_risks     = [{"point": f"{_METRIC_LABELS.get(f, f)} 数据上偏弱（{_fmt_val(f, v)}）",
                          "evidence_metric": f} for f, v, _ in risks]

        # 置信度：覆盖度 × 口径一致性，封顶 0.5
        missing_list = list(mf.get("missing_quant_fields") or [])
        denom = n_present + len(missing_list)
        coverage = (n_present / denom) if denom else 0.0
        consistency = 1.0
        fy = _sfn(metrics.get("fcf_yield"))
        if fy is not None and fy > 30:
            consistency -= 0.2
        pe = _sfn(metrics.get("pe"))
        if pe is not None and (pe <= 0 or pe > 500):
            consistency -= 0.2
        for fld in ("roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg"):
            mv = _sfn(metrics.get(fld))
            if mv is not None and mv > 100:
                consistency -= 0.2
                break
        consistency = max(0.3, min(1.0, consistency))
        confidence = round(min(CONFIDENCE_CAP, CONFIDENCE_CAP * coverage * consistency), 2)

        reasoning = (
            f"基于 {len(scored)} 项可用结构化指标的确定性映射，综合质量/估值/风险信号得 AI 动态分 "
            f"{ai_score}/100，倾向「{rating}」。缺失指标未参与、未脑补。"
            f"结论为 AI 暂定、待证实，需人工复核，不构成投资建议。"
        )

        return AIDynamicScore(
            ai_score=ai_score, ai_rating=rating, ai_reasoning=reasoning,
            key_strengths=key_strengths, key_risks=key_risks,
            missing_data_warnings=labels, confidence=confidence, needs_human_review=True,
        )


def score_dynamic(payload, provider=None):
    """
    顶层入口。返回经 schema 校验的 8 字段 dict；
    任何 provider 异常 / 内部异常 / 校验失败 → 返回 None（调用方据此显示「未生成」）。
    """
    provider = provider or HeuristicDynamicProvider()
    try:
        out = provider.score(payload).to_dict()
    except Exception:
        return None
    ok, _errors = validate_ai_score(out)
    return out if ok else None


def compute_final_preview(rule_based_score, ai_score, ai_weight=AI_WEIGHT):
    """
    final_score_preview（实验字段）。Phase 1 默认 ai_weight=0.0 → 等于 rule_based_score。
    ai_score 为 None 时也回退 rule_based_score。
    """
    try:
        rb = float(rule_based_score)
    except (TypeError, ValueError):
        return rule_based_score
    if ai_weight == 0.0 or ai_score is None:
        return round(rb, 2)
    try:
        a = float(ai_score)
    except (TypeError, ValueError):
        return round(rb, 2)
    return round(rb * (1 - ai_weight) + a * ai_weight, 2)
