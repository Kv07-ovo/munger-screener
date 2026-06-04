# ============================================================
# ai_score_schema.py  —  AI 动态评分层（Phase 1）输出契约与纯 Python 校验器
#
# 职责：定义 AI 动态评分输出的固定结构、取值范围、枚举、合规黑名单，
#       并提供 validate_ai_score(out) -> (ok, errors) 供落盘/注入前强校验。
#       纯函数、无第三方依赖、无 I/O、不联网。
#
# Phase 1 固定输出结构（8 个键，不含 ai_model / ai_generated_at —— 保证可复现）：
#   ai_score: number | None        # 0–100；数据不足时必须为 None
#   ai_rating: 枚举                  # 质优/质良/中性/偏弱/数据不足
#   ai_reasoning: str               # 仅质量/估值/风险/确定性，禁投资建议措辞
#   key_strengths: list[{point, evidence_metric}]   # evidence_metric 必属允许指标
#   key_risks:     list[{point, evidence_metric}]
#   missing_data_warnings: list[str]
#   confidence: float               # 0.0–0.5（封顶 0.5）
#   needs_human_review: True        # 恒 True
# ============================================================

CONFIDENCE_CAP = 0.5

AI_RATING_ENUM = ["质优", "质良", "中性", "偏弱", "数据不足"]

# key_strengths / key_risks 的 evidence_metric 只能引用这些（输入 metrics 字段名）
EVIDENCE_METRICS = {
    "roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg",
    "fcf_positive_years", "debt_to_equity", "revenue_growth_5y_cagr",
    "eps_growth_5y_cagr", "pe", "fcf_yield", "pe_percentile_5y", "pb",
    "market_cap", "roe_trend", "roic_trend", "margin_trend", "revenue_trend",
}

# 投资建议措辞黑名单（命中即判输出无效）。英文按小写子串匹配。
ADVICE_BLACKLIST_CN = ["买入", "卖出", "持有", "加仓", "减仓", "目标价", "建仓", "抄底"]
ADVICE_BLACKLIST_EN = ["buy", "sell", "hold", "overweight", "underweight", "target price"]

REQUIRED_KEYS = [
    "ai_score", "ai_rating", "ai_reasoning", "key_strengths", "key_risks",
    "missing_data_warnings", "confidence", "needs_human_review",
]

_NUM = (int, float)


def _has_advice(text) -> bool:
    s = str(text)
    low = s.lower()
    return (any(w in s for w in ADVICE_BLACKLIST_CN)
            or any(w in low for w in ADVICE_BLACKLIST_EN))


def validate_ai_score(out) -> tuple:
    """
    校验 AI 动态评分输出。返回 (ok: bool, errors: list[str])。
    严格按 Phase 1 的 8 字段契约；bool 在 Python 中是 int 子类，故对数值字段显式排除 bool。
    """
    errors = []

    if not isinstance(out, dict):
        return False, ["输出不是 dict"]

    for k in REQUIRED_KEYS:
        if k not in out:
            errors.append(f"缺少必填键：{k}")
    if errors:
        return False, errors

    # ai_score: None 或 0–100 数值（排除 bool）
    sc = out["ai_score"]
    if sc is not None:
        if isinstance(sc, bool) or not isinstance(sc, _NUM):
            errors.append("ai_score 必须为 number 或 None")
        elif not (0 <= sc <= 100):
            errors.append(f"ai_score 超范围(0-100)：{sc}")

    # ai_rating: 枚举
    rating = out["ai_rating"]
    if rating not in AI_RATING_ENUM:
        errors.append(f"ai_rating 不在枚举内：{rating!r}")

    # ai_reasoning: str
    if not isinstance(out["ai_reasoning"], str):
        errors.append("ai_reasoning 必须为 str")

    # key_strengths / key_risks: list[{point:str, evidence_metric: 允许集合}]
    for key in ("key_strengths", "key_risks"):
        items = out[key]
        if not isinstance(items, list):
            errors.append(f"{key} 必须为 list")
            continue
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                errors.append(f"{key}[{i}] 必须为 dict")
                continue
            if not isinstance(it.get("point"), str) or not it.get("point").strip():
                errors.append(f"{key}[{i}].point 必须为非空 str")
            em = it.get("evidence_metric")
            if em not in EVIDENCE_METRICS:
                errors.append(f"{key}[{i}].evidence_metric 非法或不在允许指标内：{em!r}")
            if _has_advice(it.get("point", "")):
                errors.append(f"{key}[{i}].point 含投资建议措辞")

    # missing_data_warnings: list[str]
    mdw = out["missing_data_warnings"]
    if not isinstance(mdw, list) or any(not isinstance(x, str) for x in mdw):
        errors.append("missing_data_warnings 必须为 list[str]")

    # confidence: 0.0–CAP（排除 bool）
    conf = out["confidence"]
    if isinstance(conf, bool) or not isinstance(conf, _NUM):
        errors.append("confidence 必须为 number")
    elif not (0.0 <= conf <= CONFIDENCE_CAP):
        errors.append(f"confidence 超范围(0-{CONFIDENCE_CAP})：{conf}")

    # needs_human_review 恒 True
    if out["needs_human_review"] is not True:
        errors.append("needs_human_review 必须恒为 True")

    # 合规：reasoning / rating 不得含投资建议措辞
    if _has_advice(out["ai_reasoning"]):
        errors.append("ai_reasoning 含投资建议措辞")
    if _has_advice(rating):
        errors.append("ai_rating 含投资建议措辞")

    # 一致性：数据不足 ⇔ ai_score is None 且 confidence==0.0
    if rating == "数据不足":
        if sc is not None:
            errors.append("ai_rating=数据不足 时 ai_score 必须为 None")
        if conf not in (0, 0.0):
            errors.append("ai_rating=数据不足 时 confidence 必须为 0.0")
    else:
        if sc is None:
            errors.append("ai_score 为 None 时 ai_rating 必须为 数据不足")

    return (len(errors) == 0), errors
