# ============================================================
# validator.py  —  芒格式选股评分器：数据校验 + 最终决策  v2.1
#
# v2.0 改动：
#   1. validate_data() 新增合并 annual_financials 的 data_warning
#   2. get_final_decision() 新增：年度数据不足时倾向"数据不足"
# v2.1.0-alpha2 改动：
#   3. 明确区分"数据缺失"与"公司质量差"：
#      关键量化字段（PE/FCF Yield/ROIC/ROE/营收增速/D-E）缺失时，
#      不再死扣分判"暂时放弃"，而是给出"数据不足（待补录）"。
#      （评分算法 scorer.py 不变，仅决策层区分缺失 vs 差。）
# ============================================================

REQUIRED_FIELDS = [
    "ticker", "name", "roe_5y_avg", "gross_margin_5y_avg",
    "net_margin_5y_avg", "fcf_positive_years", "debt_to_equity",
    "pe", "fcf_yield", "roic_5y_avg", "moat_score", "management_score",
]

# ── v2.1.0-alpha2：关键量化字段（缺失≠公司差，应进入待补录）──────────────────────
# 这些字段缺失只代表"还没拿到数据"，不应被当作"公司差"而死扣分。
KEY_QUANT_FIELDS = [
    "pe", "fcf_yield", "roic_5y_avg", "roe_5y_avg",
    "revenue_growth_5y_cagr", "debt_to_equity",
]
_QUANT_LABELS = {
    "pe":                     "市盈率PE",
    "fcf_yield":              "自由现金流收益率",
    "roic_5y_avg":            "ROIC(5年均值)",
    "roe_5y_avg":             "ROE(5年均值)",
    "revenue_growth_5y_cagr": "营收增速(5年CAGR)",
    "debt_to_equity":         "负债权益比D/E",
}
# 视为"缺失/空"的取值（不含数字 0：D/E=0 等可能是真实值，不算缺失）
_BLANK_SENTINELS = {"", "nan", "none", "n/a", "null", "unknown", "manual_pending"}

_TREND_FIELDS = ["roe_trend", "roic_trend", "margin_trend", "revenue_trend"]
_TREND_NAMES  = {"roe_trend": "ROE", "roic_trend": "ROIC",
                 "margin_trend": "利润率", "revenue_trend": "营收"}

# ── 行业特殊规则 ──────────────────────────────────────────────────────────────
# 当 industry 字段尚未填写时，用 ticker 做回退判断
_TICKER_INDUSTRY_HINTS: dict[str, str] = {
    "JPM": "银行", "BAC": "银行", "WFC": "银行", "GS": "银行", "C": "银行",
    "BRK-B": "综合控股", "BRK-A": "综合控股",
    "AIG": "保险", "MET": "保险", "PRU": "保险",
    # v2.4.0：A股金融类回退提示（AKShare 行业缺失时兜底）
    "000001.SZ": "银行", "601398.SH": "银行", "601318.SH": "保险", "600030.SH": "证券",
}

# v2.4.0：银行/保险/券商统一作为"特殊金融类"——FCF Yield/毛利率/FCF年数 不适用
_FINANCIAL_EXEMPT = {"fcf_yield", "gross_margin_5y_avg", "fcf_positive_years"}
_FINANCIAL_WARN   = {"缺少gross_margin", "缺少free_cash_flow"}

# REQUIRED_FIELDS 中对特殊行业免检的字段（不产生"关键字段缺失"警告）
_INDUSTRY_REQUIRED_EXEMPT: dict[str, set] = {
    "银行":     _FINANCIAL_EXEMPT,
    "保险":     _FINANCIAL_EXEMPT,
    "证券":     _FINANCIAL_EXEMPT,   # v2.4.0 券商/证券
    "券商":     _FINANCIAL_EXEMPT,
    "资本市场":  _FINANCIAL_EXEMPT,
    "综合控股":  {"gross_margin_5y_avg"},
}

# _fin_data_warning 中对特殊行业应屏蔽的关键词
_INDUSTRY_FIN_WARN_SUPPRESS: dict[str, set] = {
    "银行":     _FINANCIAL_WARN,
    "保险":     _FINANCIAL_WARN,
    "证券":     _FINANCIAL_WARN,
    "券商":     _FINANCIAL_WARN,
    "资本市场":  _FINANCIAL_WARN,
    "综合控股":  {"缺少gross_margin"},
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


def _is_blank(val) -> bool:
    """是否为缺失/空值。注意：数字 0 不算缺失（如 D/E=0 是合理真实值）。"""
    return str(val).strip().lower() in _BLANK_SENTINELS


def missing_key_quant_fields(row: dict) -> list:
    """
    返回该股票"缺失"的关键量化字段（保持 KEY_QUANT_FIELDS 顺序）。
    会跳过该行业不适用的字段（如银行的 fcf_yield），避免误报。
    缺失 = 字段为空，而非"数值差"——用于区分"数据缺失"与"公司差"。
    """
    exempt = _industry_required_exempt(row)
    return [f for f in KEY_QUANT_FIELDS
            if f not in exempt and _is_blank(row.get(f, ""))]


def quant_labels(fields) -> list:
    """把字段名转成中文标签，便于人工阅读。"""
    return [_QUANT_LABELS.get(f, f) for f in fields]


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

    # D/E 风险（口径同 scorer.classify_debt_to_equity）：
    #   缺失 → 交「关键字段缺失」逻辑，不在此判负债风险；
    #   ≤0  → 异常/疑负权益（如 MCD=-30.6），不能视为低负债，按高风险提示；
    #   >3  → 真实高杠杆（原逻辑）。
    raw_de = row.get("debt_to_equity", "")
    debt_reason = str(row.get("debt_reason", "")).strip()
    reason_note = f"（{debt_reason}）" if debt_reason else ""
    if _is_blank(raw_de):
        pass
    elif de <= 0:
        warnings.append(
            f"D/E异常({de:.1f}){reason_note}：≤0 疑似负权益（如回购致股东权益为负），"
            f"不能视为低负债，请按高风险核实"
        )
    elif de > 3:
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
    if "fcf_positive_years" not in exempt:
        if _is_blank(row.get("fcf_positive_years", "")):
            warnings.append("FCF正数年数缺失：数据待补录（非公司质量问题），建议运行 fetcher.py")
        elif fcf_yrs < 3:
            warnings.append(f"FCF正数年数不足({fcf_yrs}年)：现金流不稳定（数据已存在）")

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
    elif any(k in ind for k in ("证券", "券商", "资本市场")):
        warnings.append(
            "证券/券商财务口径特殊，普通 FCF Yield / 毛利率 / ROIC 参考价值有限，"
            "应参考ROE、净资本、自营/经纪/投行收入结构等。"
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
    v2.1.0-alpha2：区分"数据缺失"与"公司差"。关键量化字段缺失走
               "数据不足（待补录）"；ROIC/FCF 的负面判定仅在字段存在时生效。

    优先级（高→低）：
        1. 超出能力圈
        2. 造假风险
        3. D/E > 3（真实高杠杆；缺失不触发）
        4. [alpha2] 关键量化字段缺失 ≥2 项 → 数据不足（待补录）
        5. 数据可信度 < 6
        6. [v2.0] 年度数据不足（< 3 年且自动模式）
        7. FCF 年数 < 3（仅当该字段存在）
        8. ROIC < 5（仅当该字段存在）
        9. 按总分打级
       10. 趋势恶化降级（2+ declining → 不能"深入研究"）
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

    # ── 硬性风险过滤（基于"确实存在"的数值；缺失的 de 默认 0 不会误触发）──
    raw_de = row.get("debt_to_equity", "")
    if coc == "outside":          return "超出能力圈"
    if "fraud" in note_str or "造假" in note_str:  return "风险过高"
    # 异常 D/E（≤0，疑负权益，如 MCD=-30.6）：仅在 present 时触发，缺失不误判为高负债
    if not _is_blank(raw_de) and de <= 0:  return "风险过高"
    if de > 3:                    return "风险过高"

    # ── v2.1.0-alpha2：先区分"数据缺失" vs "公司差" ───────────────────
    # 关键量化字段大面积缺失（≥2 项）→ 数据待补录，而不是判公司差。
    # （补齐后重新评分即可，避免把"没数据"误当"差公司"。）
    missing = missing_key_quant_fields(row)
    if len(missing) >= 2:
        return "数据不足（待补录）"

    if conf < 6:                  return "数据不足"
    # v2.0：自动模式下年度数据过少
    if data_mode == "annual_financials" and 0 < fin_yrs < 3:
        return "数据不足"

    # ── 以下负面判定只在相应字段"存在"时才生效（缺失不死扣分）──────────
    if not _is_blank(row.get("fcf_positive_years", "")) and fcf_years < 3:
        return "数据不足或风险过高"
    if "roic_5y_avg" not in missing and roic < 5:
        return "暂时放弃"

    # 按总分
    if total >= 85:   decision = "深入研究"
    elif total >= 70: decision = "加入观察池"
    elif total >= 60: decision = "一般，暂不研究"
    else:             decision = "暂时放弃"

    # 趋势恶化降级（2+ declining → 不能"深入研究"）
    if decision == "深入研究" and _count_declining(row) >= 2:
        decision = "加入观察池"

    return decision
