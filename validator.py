# ============================================================
# validator.py  —  芒格式选股评分器：数据校验 + 最终决策  v2.0
#
# v2.0 改动：
#   1. validate_data() 新增合并 annual_financials 的 data_warning
#   2. get_final_decision() 新增：年度数据不足时倾向"数据不足"
# ============================================================

REQUIRED_FIELDS = [
    "ticker", "name", "roe_5y_avg", "gross_margin_5y_avg",
    "net_margin_5y_avg", "fcf_positive_years", "debt_to_equity",
    "pe", "fcf_yield", "roic_5y_avg", "moat_score", "management_score",
]

_TREND_FIELDS = ["roe_trend", "roic_trend", "margin_trend", "revenue_trend"]
_TREND_NAMES  = {"roe_trend": "ROE", "roic_trend": "ROIC",
                 "margin_trend": "利润率", "revenue_trend": "营收"}

# ── 行业特殊规则 ──────────────────────────────────────────────────────────────
# 当 industry 字段尚未填写时，用 ticker 做回退判断
_TICKER_INDUSTRY_HINTS: dict[str, str] = {
    "JPM": "银行", "BAC": "银行", "WFC": "银行", "GS": "银行", "C": "银行",
    "BRK-B": "综合控股", "BRK-A": "综合控股",
    "AIG": "保险", "MET": "保险", "PRU": "保险",
}

# REQUIRED_FIELDS 中对特殊行业免检的字段（不产生"关键字段缺失"警告）
_INDUSTRY_REQUIRED_EXEMPT: dict[str, set] = {
    "银行":    {"fcf_yield", "gross_margin_5y_avg", "fcf_positive_years"},
    "保险":    {"gross_margin_5y_avg", "fcf_yield"},
    "综合控股": {"gross_margin_5y_avg"},
}

# _fin_data_warning 中对特殊行业应屏蔽的关键词
_INDUSTRY_FIN_WARN_SUPPRESS: dict[str, set] = {
    "银行":    {"缺少gross_margin", "缺少free_cash_flow"},
    "综合控股": {"缺少gross_margin"},
    "保险":    {"缺少gross_margin"},
}


def effective_industry(row: dict) -> str:
    """返回行业字符串；industry 为空或 Unknown 时按 ticker 回退。"""
    ind = str(row.get("industry", "")).strip()
    if not ind or ind.lower() == "unknown":
        return _TICKER_INDUSTRY_HINTS.get(
            str(row.get("ticker", "")).strip().upper(), "")
    return ind


def _industry_required_exempt(row: dict) -> set:
    ind = effective_industry(row)
    exempt: set = set()
    for kw, fields in _INDUSTRY_REQUIRED_EXEMPT.items():
        if kw in ind:
            exempt |= fields
    return exempt


def filter_fin_warning(row: dict, fin_warn: str) -> str:
    """从 _fin_data_warning 中剔除对该行业无意义的警告片段。"""
    if not fin_warn:
        return fin_warn
    ind = effective_industry(row)
    suppress: set = set()
    for kw, keywords in _INDUSTRY_FIN_WARN_SUPPRESS.items():
        if kw in ind:
            suppress |= keywords
    if not suppress:
        return fin_warn
    parts = [p.strip() for p in fin_warn.split(";") if p.strip()]
    filtered = [p for p in parts if not any(s in p for s in suppress)]
    return "; ".join(filtered)


def _sf(val, default=0.0):
    try:
        v = float(val)
        return default if v != v else v
    except (ValueError, TypeError):
        return default


def _count_declining(row):
    return sum(1 for f in _TREND_FIELDS
               if str(row.get(f, "")).strip().lower() == "declining")


def validate_data(row):
    """
    检查数据异常，返回 (warnings_list, warning_note_str)。
    发现问题只记录，不崩溃。

    v2.0 新增：
      - 合并 row["_fin_data_warning"]（来自 annual_financials 的 data_warning）
      - 若年度数据不足（_fin_years_count < 3），补充提示
    v2.1 新增：
      - 银行 / 保险 / 综合控股行业特殊规则，豁免不适用的指标缺失警告
    """
    warnings = []
    exempt   = _industry_required_exempt(row)    # 行业豁免字段集合
    ind      = effective_industry(row)

    # ── 1. 关键字段缺失 ──────────────────────────────────────
    for f in REQUIRED_FIELDS:
        if f not in exempt and not row.get(f, ""):
            warnings.append(f"关键字段缺失: {f}")

    # ── 2. 数值范围异常 ──────────────────────────────────────
    roe     = _sf(row.get("roe_5y_avg"))
    gm      = _sf(row.get("gross_margin_5y_avg"))
    nm      = _sf(row.get("net_margin_5y_avg"))
    roic    = _sf(row.get("roic_5y_avg"))
    pe      = _sf(row.get("pe"), default=999)
    fcf_yld = _sf(row.get("fcf_yield"))
    de      = _sf(row.get("debt_to_equity"))
    moat_s  = _sf(row.get("moat_score"))
    mgmt_s  = _sf(row.get("management_score"))
    conf    = _sf(row.get("confidence_score"), default=10)
    coc     = str(row.get("circle_of_competence", "edge")).strip().lower()
    fcf_yrs = int(_sf(row.get("fcf_positive_years")))

    if roe > 100:
        warnings.append(f"ROE异常({roe:.0f}%)：可能因大量回购导致股东权益极低，分数虚高")
    if gm > 100:
        warnings.append(f"毛利率异常({gm:.0f}%)：超过100%不合理")
    if nm > 100:
        warnings.append(f"净利率异常({nm:.0f}%)：超过100%不合理")
    if roic > 100:
        warnings.append(f"ROIC异常({roic:.0f}%)：超过100%不合理")
    if 0 < pe < 1:
        warnings.append(f"PE异常({pe:.2f})：PE<1 非常罕见，请核实")
    if pe < 0:
        warnings.append(f"PE为负({pe:.1f})：公司亏损，PE无参考意义")
    if fcf_yld > 30:
        warnings.append(f"FCF Yield异常({fcf_yld:.1f}%)：超过30%罕见，请核实")

    if de > 3:
        debt_reason = str(row.get("debt_reason", "")).strip()
        reason_note = f"（{debt_reason}）" if debt_reason else ""
        warnings.append(
            f"D/E过高({de:.1f}){reason_note}：请检查是否由回购/轻资产/股东权益过低导致，"
            f"建议结合interest_coverage和net_debt_to_EBITDA综合评估"
        )
    if not (0 <= moat_s <= 10):
        warnings.append(f"moat_score={moat_s:.0f} 超出[0,10]范围")
    if not (0 <= mgmt_s <= 10):
        warnings.append(f"management_score={mgmt_s:.0f} 超出[0,10]范围")
    if conf < 6:
        warnings.append(f"数据可信度偏低({conf:.0f}/10)：请用更可靠的数据来源核实")
    if coc == "outside":
        warnings.append("超出能力圈(outside)：对该公司/行业理解不足，评分参考价值有限")
    if fcf_yrs < 3 and "fcf_positive_years" not in exempt:
        warnings.append(f"FCF正数年数不足({fcf_yrs}年)：现金流不稳定或数据不足")

    note_str = str(row.get("risk_note", "")).lower()
    if "fraud" in note_str or "造假" in note_str:
        warnings.append("风险标记含'fraud/造假'：高风险，请极度谨慎")

    # ── 3. 趋势恶化警告 ──────────────────────────────────────
    declining_count = _count_declining(row)
    if declining_count >= 2:
        declining_names = [_TREND_NAMES.get(f, f) for f in _TREND_FIELDS
                          if str(row.get(f, "")).strip().lower() == "declining"]
        warnings.append(f"财务趋势恶化，需要谨慎（{'、'.join(declining_names)}趋势下滑）")

    # ── 4. v2.0：合并 annual_financials 的 data_warning ───────
    # 对特殊行业（银行/综合控股/保险）先过滤掉不适用的警告片段
    fin_warn = filter_fin_warning(
        row, str(row.get("_fin_data_warning", "")).strip()
    )
    if fin_warn:
        warnings.append(f"[财务数据] {fin_warn}")

    # ── 5. v2.0：年度数据不足提示 ────────────────────────────
    fin_years_count = int(_sf(row.get("_fin_years_count", 0)))
    data_mode       = str(row.get("data_mode", "")).strip()
    if data_mode == "annual_financials" and 0 < fin_years_count < 3:
        warnings.append(f"年度历史数据不足（仅{fin_years_count}年，建议≥5年）")

    # ── 6. v2.1：行业特殊规则提示 ─────────────────────────────
    if "银行" in ind:
        warnings.append(
            "银行业不适合用普通企业 FCF Yield / 毛利率评价，"
            "应参考ROE、资本充足率、坏账率、净息差等银行指标。"
        )
    elif "保险" in ind or "综合控股" in ind:
        warnings.append(
            "保险/综合控股公司财务口径特殊，"
            "普通ROIC、毛利率、FCF Yield参考价值有限。"
        )

    warning_note = " | ".join(warnings) if warnings else ""
    return warnings, warning_note


def get_final_decision(result, row):
    """
    综合总分、风险、能力圈、数据质量、趋势，给出最终研究建议。

    v2.0 新增：若 annual_financials 年份 < 3 且使用自动计算，
               倾向于"数据不足"（而非给出正面评级）。

    优先级（高→低）：
        1. 超出能力圈
        2. 造假风险
        3. D/E > 3
        4. FCF 年数 < 3
        5. ROIC < 5
        6. 数据可信度 < 6
        7. [v2.0] 年度数据不足（< 3 年且自动模式）
        8. 按总分打级
        9. 趋势恶化降级（2+ declining → 不能"深入研究"）
    """
    total     = result["total_score"]
    de        = _sf(row.get("debt_to_equity"))
    fcf_years = int(_sf(row.get("fcf_positive_years")))
    roic      = _sf(row.get("roic_5y_avg"))
    conf      = _sf(row.get("confidence_score"), default=10)
    coc       = str(row.get("circle_of_competence", "edge")).strip().lower()
    note_str  = str(row.get("risk_note", "")).lower()
    fin_yrs   = int(_sf(row.get("_fin_years_count", 0)))
    data_mode = str(row.get("data_mode", "")).strip()

    # 硬性过滤
    if coc == "outside":          return "超出能力圈"
    if "fraud" in note_str or "造假" in note_str:  return "风险过高"
    if de > 3:                    return "风险过高"
    if fcf_years < 3:             return "数据不足或风险过高"
    if roic < 5:                  return "暂时放弃"
    if conf < 6:                  return "数据不足"
    # v2.0：自动模式下年度数据过少
    if data_mode == "annual_financials" and 0 < fin_yrs < 3:
        return "数据不足"

    # 按总分
    if total >= 85:   decision = "深入研究"
    elif total >= 70: decision = "加入观察池"
    elif total >= 60: decision = "一般，暂不研究"
    else:             decision = "暂时放弃"

    # 趋势恶化降级（2+ declining → 不能"深入研究"）
    if decision == "深入研究" and _count_declining(row) >= 2:
        decision = "加入观察池"

    return decision
