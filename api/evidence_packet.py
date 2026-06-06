# ============================================================
# api/evidence_packet.py  —  证据包构造器（ai_evidence_v1 链路第一环）
#
# 职责：把「已合并年度财务的 stocks 行（str 值）」转成 *标准 evidence packet*，
#       供 prompt_builder / AI client / validator / adapter 使用。
#
# 铁律：
#   1. 纯函数：不读写文件、不联网、无第三方依赖（仅 stdlib + 本包 schema）。
#   2. 不编造、不二次缩放：数据层已是 0-100(百分比) / ratio(PE/PB/D&E) / percent(fcf_yield)，
#      本模块只做「str→数值 或 None」与单位*标注*，绝不再 ×100 或 ÷100。
#   3. 缺失字段显式标记（missing_fields），不拿 0 兜底。
#   4. 人工*数值*分（moat_score/management_score）绝不进评分证据，只放 _legacy_reference（debug）。
#      人工*定性理由*（moat_reason/management_reason/notes…）作为事实证据放 human_notes。
#   5. source_dates / stale_fields 由数据层日期（data_date / _fin_updated_at）派生，供 AI
#      据数据新旧调 data_quality_adjustment 与 confidence。
# ============================================================
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from api import ai_scoring_schema as schema

# 单位标注（仅描述，不参与换算）
UNIT_PERCENT = "percent"      # 0-100 百分数（如 ROE=43.2 表示 43.2%）
UNIT_RATIO = "ratio"          # 无量纲倍数（PE=32.4、D/E=0.4）
UNIT_YEARS = "years"          # 年数（fcf_positive_years=5）
UNIT_PERCENTILE = "percentile"  # 0-100 历史分位
UNIT_TREND = "trend"          # 枚举：improving / stable / declining

_FIELD_UNITS: Dict[str, str] = {
    "roe_5y_avg": UNIT_PERCENT, "roic_5y_avg": UNIT_PERCENT,
    "gross_margin_5y_avg": UNIT_PERCENT, "net_margin_5y_avg": UNIT_PERCENT,
    "revenue_growth_5y_cagr": UNIT_PERCENT, "eps_growth_5y_cagr": UNIT_PERCENT,
    "fcf_yield": UNIT_PERCENT,
    "debt_to_equity": UNIT_RATIO, "pe": UNIT_RATIO, "pb": UNIT_RATIO,
    "fcf_positive_years": UNIT_YEARS,
    "pe_percentile_5y": UNIT_PERCENTILE,
    "roe_trend": UNIT_TREND, "roic_trend": UNIT_TREND,
    "margin_trend": UNIT_TREND, "revenue_trend": UNIT_TREND,
}

# 各 rubric 维度参考的*量化*字段（moat/management 用量化代理，绝不用人工分）
DIMENSION_FIELDS: Dict[str, List[str]] = {
    "business_quality": ["roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg",
                         "net_margin_5y_avg", "fcf_positive_years"],
    "growth": ["revenue_growth_5y_cagr", "eps_growth_5y_cagr"],
    "balance_sheet": ["debt_to_equity", "fcf_positive_years"],
    "valuation": ["pe", "fcf_yield", "pe_percentile_5y", "pb"],
    # moat 量化代理：盈利能力的*持续性/水平*（ROIC、毛利、净利、其趋势），非人工 moat_score
    "moat": ["roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg",
             "roic_trend", "margin_trend"],
    # management/governance 量化代理：资本运用与稳健性（FCF 持续、负债、营收趋势），非人工 management_score
    "management_governance": ["fcf_positive_years", "debt_to_equity", "revenue_trend"],
}

# 核心评分字段（用于 missing_fields / data_confidence；与 score_engine._CONFIDENCE_FIELDS 同集）
CORE_NUMERIC_FIELDS = (
    "roe_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg", "fcf_positive_years",
    "roic_5y_avg", "revenue_growth_5y_cagr", "eps_growth_5y_cagr",
    "debt_to_equity", "pe", "fcf_yield",
)

_TREND_VALUES = {"improving", "stable", "declining"}
_NULL_TOKENS = {"", "nan", "none", "n/a", "null", "unknown", "manual_pending", "未填写", "—"}

# 人工*定性*事实字段（作证据，不作分数）
_HUMAN_NOTE_FIELDS = (
    "moat_reason", "management_reason", "circle_of_competence",
    "risk_note", "risk_reason", "debt_reason", "notes",
)


def _num(val: Any) -> Optional[float]:
    """严格 str→float：空/占位/NaN/不可解析 → None（区分「缺失」与「真实 0」）。"""
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return None if val != val else float(val)   # NaN → None
    s = str(val).strip()
    if s.lower() in _NULL_TOKENS:
        return None
    try:
        f = float(s)
        return None if f != f else f
    except (ValueError, TypeError):
        return None


def _str(val: Any) -> str:
    s = "" if val is None else str(val).strip()
    return "" if s.lower() in _NULL_TOKENS else s


def _trend(val: Any) -> Optional[str]:
    s = _str(val).lower()
    return s if s in _TREND_VALUES else None


def _market_cap_unit(market: str) -> Optional[str]:
    """市值单位随市场不同：A股=亿CNY；美股本库未填则为 None（不臆造美元口径）。"""
    if market == "CN":
        return "亿CNY"
    if market == "US":
        return "USD"
    return None


def _parse_iso(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def _days_between(d1: Optional[date], d2: Optional[date]) -> Optional[int]:
    if d1 is None or d2 is None:
        return None
    return abs((d2 - d1).days)


def build_evidence_packet(row: Dict[str, Any], as_of_date: Optional[str] = None) -> Dict[str, Any]:
    """由合并后的 stocks 行构造标准 evidence packet。

    row：load_score_compare() 产出的合并行（含年度财务覆盖字段 + _fin_* 元信息 + data_date）。
    as_of_date：评估基准日（ISO，默认今天）；显式传入以保证测试可复现。
    """
    row = row or {}
    as_of = (as_of_date or date.today().isoformat())[:10]
    as_of_d = _parse_iso(as_of)

    market = _str(row.get("market"))
    canonical = _str(row.get("canonical_ticker")) or _str(row.get("ticker"))
    company_name = _str(row.get("long_name")) or _str(row.get("name")) or canonical

    # ── metrics：name -> {value, unit, present}（数值/趋势/None，单位仅标注）──
    metrics: Dict[str, Dict[str, Any]] = {}
    for field, unit in _FIELD_UNITS.items():
        if unit == UNIT_TREND:
            value: Any = _trend(row.get(field))
        else:
            value = _num(row.get(field))
        metrics[field] = {"value": value, "unit": unit, "present": value is not None}

    # market_cap 单独处理（单位随市场）
    mcap = _num(row.get("market_cap"))
    metrics["market_cap"] = {"value": mcap, "unit": _market_cap_unit(market),
                             "present": mcap is not None}

    # ── 缺失 / 数据完整度（仅核心数值字段）──
    missing_fields = [f for f in CORE_NUMERIC_FIELDS if metrics[f]["value"] is None]
    present_n = len(CORE_NUMERIC_FIELDS) - len(missing_fields)
    data_confidence = round(present_n / len(CORE_NUMERIC_FIELDS), 2)

    # ── source_dates（按逻辑组）+ stale_fields ──
    valuation_date = _str(row.get("data_date"))                 # 估值/价格/人工字段口径日
    fundamentals_date = _str(row.get("_fin_updated_at")) or _str(row.get("fin_updated_at"))
    financial_years = _str(row.get("financial_data_years"))
    source_dates = {
        "valuation_as_of": valuation_date or None,              # pe / pb / fcf_yield / market_cap
        "fundamentals_as_of": fundamentals_date or None,        # roe/roic/margins/cagr/de（年度财务）
        "financial_years": financial_years or None,             # 如 "2021-2025"
    }

    _VAL_FIELDS = ("pe", "pb", "fcf_yield", "market_cap", "pe_percentile_5y")
    _FUND_FIELDS = ("roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg",
                    "revenue_growth_5y_cagr", "eps_growth_5y_cagr", "debt_to_equity",
                    "fcf_positive_years")
    stale_fields: List[str] = []
    val_age = _days_between(_parse_iso(valuation_date), as_of_d)
    fund_age = _days_between(_parse_iso(fundamentals_date or valuation_date), as_of_d)
    if val_age is not None and val_age > schema.SOURCE_DATE_STALE_DAYS:
        stale_fields += [f for f in _VAL_FIELDS if metrics[f]["value"] is not None]
    if fund_age is not None and fund_age > schema.SOURCE_DATE_STALE_DAYS:
        stale_fields += [f for f in _FUND_FIELDS if metrics[f]["value"] is not None]

    # ── data_warnings：透传年度表警示 + 结构性提示（不改数据，仅暴露）──
    warnings: List[str] = []
    for raw in (_str(row.get("_fin_data_warning")), _str(row.get("fin_data_warning"))):
        for part in raw.split(";"):
            p = part.strip()
            if p and p not in warnings:
                warnings.append(p)
    if market == "CN" and metrics["roic_5y_avg"]["value"] is None:
        warnings.append("A股 ROIC 缺失（AKShare 无稳定口径），business_quality/moat 证据受限")
    if metrics["market_cap"]["present"] and market == "CN":
        warnings.append("market_cap 单位为 亿CNY，跨市场比较前需统一口径")
    # 负权益线索：D/E 为负（合并后真实负权益）或绝对值异常大，且年度表含负权益警示时，
    # 提醒 ROE/D&E 可能为负权益失真值（如 MCD 回购致权益为负）。
    de_val = metrics["debt_to_equity"]["value"]
    if de_val is not None and (de_val < 0 or de_val > 5) and any("权益" in w or "<0" in w for w in warnings):
        warnings.append("debt_to_equity 为负或绝对值异常偏大且年度表标记负权益，ROE/D&E 可能为负权益失真值")

    # ── 人工定性事实（作证据，绝不作分）──
    human_notes = {f: _str(row.get(f)) for f in _HUMAN_NOTE_FIELDS if _str(row.get(f))}

    # ── legacy 人工数值 / 旁路分：仅 debug 参考，绝不进 prompt、绝不作主分 ──
    legacy_reference = {
        "human_moat_score": _num(row.get("moat_score")),
        "human_management_score": _num(row.get("management_score")),
        "note": "人工主观分仅作参考/调试，不得作为 AI 评分依据或最终主分来源",
    }

    return {
        "evidence_packet_id": f"{canonical}@{as_of}@{schema.SCORING_RUBRIC_VERSION}",
        "scoring_rubric_version": schema.SCORING_RUBRIC_VERSION,
        "as_of_date": as_of,
        "ticker": _str(row.get("ticker")) or canonical,
        "canonical_ticker": canonical,
        "company_name": company_name,
        "market": market or None,
        "currency": _str(row.get("currency")) or None,
        "sector": _str(row.get("sector")) or None,
        "industry": _str(row.get("industry")) or None,
        "metrics": metrics,
        "dimension_fields": DIMENSION_FIELDS,
        "human_notes": human_notes,
        "source_dates": source_dates,
        "missing_fields": missing_fields,
        "stale_fields": sorted(set(stale_fields)),
        "data_warnings": warnings,
        "data_confidence": data_confidence,
        "_legacy_reference": legacy_reference,
    }
