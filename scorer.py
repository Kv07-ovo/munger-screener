# ============================================================
# scorer.py  —  芒格式选股评分器：评分引擎  v1.5
# ============================================================

def _safe_float(val, default=0.0):
    """安全转 float，失败或 NaN 返回 default。"""
    try:
        v = float(val)
        return default if v != v else v
    except (ValueError, TypeError):
        return default


# ============================================================
# 一、生意质量（30 分）
# ============================================================

def score_business_quality(row):
    """ROE(9) + 毛利率(7) + 净利率(5) + FCF年数(5) + ROIC(4) = 30"""
    score, detail = 0.0, {}

    roe = _safe_float(row.get("roe_5y_avg"))
    roe_s = 9 if roe >= 20 else 6 if roe >= 15 else 3 if roe >= 10 else 0
    score += roe_s;  detail["ROE评分"] = roe_s

    gm = _safe_float(row.get("gross_margin_5y_avg"))
    gm_s = 7 if gm >= 50 else 4 if gm >= 30 else 2 if gm >= 20 else 0
    score += gm_s;  detail["毛利率评分"] = gm_s

    nm = _safe_float(row.get("net_margin_5y_avg"))
    nm_s = 5 if nm >= 20 else 3 if nm >= 10 else 2 if nm >= 5 else 0
    score += nm_s;  detail["净利率评分"] = nm_s

    fcf_y = max(0, min(int(_safe_float(row.get("fcf_positive_years"))), 5))
    score += fcf_y;  detail["现金流年数评分"] = float(fcf_y)

    roic = _safe_float(row.get("roic_5y_avg"))
    roic_s = 4 if roic >= 25 else 3 if roic >= 20 else 2 if roic >= 15 else 1 if roic >= 10 else 0
    score += roic_s;  detail["ROIC评分"] = roic_s

    return round(score, 2), detail


# ============================================================
# 二、护城河（20 分）——v1.5：优先使用 6 项拆分评分
# ============================================================

_MOAT_SUB_FIELDS = [
    "brand_score", "switching_cost_score", "network_effect_score",
    "scale_advantage_score", "pricing_power_score", "moat_durability_score",
]

def calc_moat_details(row):
    """
    计算护城河详情，返回 (moat_0_to_20, avg_0_to_10, sub_scores_dict)。

    优先路径：6 项拆分字段全部存在 → 取平均值 × 2
    回退路径：使用旧的单一 moat_score × 2

    为什么拆分比单一分数好？
        单一 moat_score 是一个主观综合判断，容易出现"感觉很强"就打满分的偏差。
        拆分后每个维度独立打分，比如可口可乐：
          品牌=10, 但转换成本=3, 网络效应=2 → 平均 6.8 → 更真实。
        这也让你发现护城河的短板在哪里，而不是一个模糊的"强"。
    """
    subs = {}
    vals = []
    for f in _MOAT_SUB_FIELDS:
        v = row.get(f, "")
        if v != "" and v is not None:
            fv = max(0.0, min(10.0, _safe_float(v)))
            subs[f] = round(fv, 1)
            vals.append(fv)

    if len(vals) == len(_MOAT_SUB_FIELDS):
        avg = sum(vals) / len(vals)
        avg = max(0.0, min(10.0, avg))
        return round(avg * 2, 2), round(avg, 2), subs
    else:
        ms = max(0.0, min(10.0, _safe_float(row.get("moat_score"))))
        return round(ms * 2, 2), round(ms, 2), {}


def score_moat(row):
    moat_s, _, _ = calc_moat_details(row)
    return moat_s


# ============================================================
# 三、成长稳定性（15 分）
# ============================================================

def score_growth(row):
    """营收CAGR(7) + EPS CAGR(7) + 双增长奖励(1) = 15"""
    score, detail = 0.0, {}
    rev_g = _safe_float(row.get("revenue_growth_5y_cagr"))
    eps_g = _safe_float(row.get("eps_growth_5y_cagr"))

    rev_s = 7 if rev_g >= 10 else 4 if rev_g >= 5 else 2 if rev_g >= 0 else 0
    score += rev_s;  detail["营收增长评分"] = rev_s

    eps_s = 7 if eps_g >= 10 else 4 if eps_g >= 5 else 2 if eps_g >= 0 else 0
    score += eps_s;  detail["EPS增长评分"] = eps_s

    bonus = 1 if (rev_g > 0 and eps_g > 0) else 0
    score += bonus;  detail["双增长奖励"] = bonus

    return round(score, 2), detail


# ============================================================
# 四、资产负债表安全性（15 分）
# ============================================================

def score_balance_sheet(row):
    """D/E比率(10) + FCF全正奖励(5) = 15"""
    score, detail = 0.0, {}
    de  = _safe_float(row.get("debt_to_equity"))
    fcy = int(_safe_float(row.get("fcf_positive_years")))

    de_s = 10 if de <= 0.5 else 7 if de <= 1.0 else 3 if de <= 2.0 else 0
    score += de_s;  detail["负债率评分"] = de_s

    bonus = 5 if fcy >= 5 else 0
    score += bonus;  detail["现金流安全奖励"] = bonus

    return round(score, 2), detail


# ============================================================
# 五、估值合理性（15 分）
# ============================================================

def score_valuation(row):
    """PE(6) + FCF Yield(6) + 双合理奖励(3) + PE分位调整(-3~+1) = 0-15"""
    score, detail = 0.0, {}
    pe      = _safe_float(row.get("pe"), default=999.0)
    fcf_yld = _safe_float(row.get("fcf_yield"))
    # pe_percentile_5y 严格解析：空/缺失/不可解析 → 不参与分位调整（不再白送 +1）
    raw_pe_pct = str(row.get("pe_percentile_5y", "")).strip()
    try:
        pe_pct = float(raw_pe_pct) if raw_pe_pct not in ("", "未填写", "None", "nan") else None
    except (ValueError, TypeError):
        pe_pct = None

    if pe <= 0 or pe > 500:
        pe = 999.0

    pe_s = 6 if pe <= 15 else 5 if pe <= 25 else 3 if pe <= 35 else 1 if pe <= 50 else 0
    score += pe_s;  detail["PE评分"] = pe_s

    fcf_s = 6 if fcf_yld >= 5 else 4 if fcf_yld >= 3 else 2 if fcf_yld >= 1 else 0
    score += fcf_s;  detail["FCF收益率评分"] = fcf_s

    bonus = 3 if (pe <= 25 and fcf_yld >= 3) else 0
    score += bonus;  detail["估值合理奖励"] = bonus

    if pe_pct is None:
        adj, adj_str = 0, "0（无分位数据）"
    elif pe_pct <= 25:
        adj, adj_str = 1, "+1（历史低位）"
    elif pe_pct > 80:
        adj, adj_str = -3, f"-3（{pe_pct:.0f}%历史高位）"
    else:
        adj, adj_str = 0, "0（历史正常区间）"
    score += adj;  detail["PE分位调整"] = adj_str

    return round(max(0.0, min(15.0, score)), 2), detail


# ============================================================
# 六、管理层（5 分）
# ============================================================

def score_management(row):
    ms = max(0.0, min(10.0, _safe_float(row.get("management_score"))))
    return round(ms * 0.5, 2)


# ============================================================
# 风险扣分
# ============================================================

def calc_risk_penalty(row):
    note = str(row.get("risk_note", "")).lower()
    penalty, detail = 0, {}

    if "fraud" in note or "造假" in note:
        penalty += 10;  detail["造假风险扣分"] = -10
    if "high valuation" in note or "高估值" in note:
        penalty += 5;   detail["高估值扣分"] = -5
    if "policy risk" in note or "政策风险" in note:
        penalty += 5;   detail["政策风险扣分"] = -5
    if "debt" in note or "高负债" in note:
        penalty += 5;   detail["高负债扣分"] = -5

    return penalty, detail


# ============================================================
# 评级 / 叙述性分析
# ============================================================

def get_rating(total_score):
    if total_score >= 85:  return "★★★ 值得深入研究"
    if total_score >= 70:  return "★★  加入观察池"
    if total_score >= 60:  return "★   一般"
    return "    暂时放弃"


# ============================================================
# v1.5 新增：趋势评分
# ============================================================

def calc_trend_score(row):
    """
    根据 roe_trend / roic_trend / margin_trend / revenue_trend 计算趋势分。

    每个字段：improving=3, stable=2, declining=0
    4 个字段原始满分 = 12，换算到 10 分制：trend_score = raw × 10 / 12

    趋势分是额外参考指标，不计入 total_score（保持 100 分体系不变）。
    用途：帮助识别"当前高分但趋势恶化"的陷阱股。
    """
    MAP    = {"improving": 3, "stable": 2, "declining": 0}
    fields = ["roe_trend", "roic_trend", "margin_trend", "revenue_trend"]
    detail = {}
    raw    = 0
    for f in fields:
        v   = str(row.get(f, "stable")).strip().lower()
        pts = MAP.get(v, 2)   # 未知值当 stable 处理
        raw += pts
        detail[f] = v
    trend_score = round(raw * 10 / 12, 1)
    return trend_score, detail


# ============================================================
# 总控函数
# ============================================================

def score_stock(row):
    """
    计算单只股票的完整芒格评分，返回包含所有字段的字典。
    industry_rank / industry_avg_score / score_vs_industry 由 main.py 注入。
    warning_note / final_decision                         由 main.py 注入。
    """
    q_score,   q_detail  = score_business_quality(row)
    moat_s, calc_moat, moat_subs = calc_moat_details(row)
    g_score,   g_detail  = score_growth(row)
    bs_score,  bs_detail = score_balance_sheet(row)
    val_score, v_detail  = score_valuation(row)
    mgmt_score            = score_management(row)
    risk_penalty, r_detail = calc_risk_penalty(row)
    trend_score, trend_detail = calc_trend_score(row)

    raw_score   = q_score + moat_s + g_score + bs_score + val_score + mgmt_score
    total_score = max(0.0, round(raw_score - risk_penalty, 2))

    return {
        # 基本信息
        "ticker":               row.get("ticker", ""),
        "name":                 row.get("name", ""),
        "industry":             row.get("industry", ""),
        "data_date":            row.get("data_date", ""),
        # v1.2 透传
        "data_source":          row.get("data_source", ""),
        "confidence_score":     row.get("confidence_score", ""),
        "circle_of_competence": row.get("circle_of_competence", "edge"),
        "warning_note":         "",        # 由 validator 填充
        "final_decision":       "",        # 由 validator 填充
        # 理由
        "moat_reason":          row.get("moat_reason", ""),
        "management_reason":    row.get("management_reason", ""),
        "risk_reason":          row.get("risk_reason", ""),
        "debt_reason":          row.get("debt_reason", ""),
        # 总分和评级
        "total_score":          total_score,
        "rating":               get_rating(total_score),
        # 六维得分
        "quality_score":        q_score,
        "moat_score":           moat_s,
        "growth_score":         g_score,
        "balance_sheet_score":  bs_score,
        "valuation_score":      val_score,
        "management_score":     mgmt_score,
        "risk_penalty":         -risk_penalty,
        # v1.5：护城河拆分
        "brand_score":              moat_subs.get("brand_score", ""),
        "switching_cost_score":     moat_subs.get("switching_cost_score", ""),
        "network_effect_score":     moat_subs.get("network_effect_score", ""),
        "scale_advantage_score":    moat_subs.get("scale_advantage_score", ""),
        "pricing_power_score":      moat_subs.get("pricing_power_score", ""),
        "moat_durability_score":    moat_subs.get("moat_durability_score", ""),
        "calculated_moat_score":    calc_moat,
        # v1.5：趋势
        "trend_score":          trend_score,
        "_trend_detail":        trend_detail,
        # v1.5：行业对比（由 main.py calc_industry_comparison 注入）
        "industry_rank":        0,
        "industry_avg_score":   0.0,
        "score_vs_industry":    0.0,
        # 内部明细
        "_quality_detail":      q_detail,
        "_growth_detail":       g_detail,
        "_balance_detail":      bs_detail,
        "_valuation_detail":    v_detail,
        "_risk_detail":         r_detail,
    }


def generate_narrative(result):
    """叙述性分析（亮点 + 风险 + 免责声明）。"""
    strengths, risks = [], []
    q   = result["quality_score"]
    m   = result["moat_score"]
    g   = result["growth_score"]
    bs  = result["balance_sheet_score"]
    v   = result["valuation_score"]
    rp  = result["risk_penalty"]
    ts  = result["trend_score"]
    moat_reason = result.get("moat_reason", "")
    mgmt_reason = result.get("management_reason", "")
    risk_reason = result.get("risk_reason", "")

    if q >= 27:
        strengths.append(f"生意质量接近满分（{q:.0f}/30）：ROE、ROIC、毛利率、净利率均优异")
    elif q >= 22:
        strengths.append(f"生意质量良好（{q:.0f}/30）")
    elif q >= 15:
        risks.append(f"生意质量偏弱（{q:.0f}/30）：可能不符合芒格【好生意】标准")
    else:
        risks.append(f"生意质量很差（{q:.0f}/30）：亏损或盈利极不稳定")

    if m >= 16:
        moat_str = f"：{moat_reason}" if moat_reason else ""
        strengths.append(f"护城河深厚（{m:.0f}/20）{moat_str}")
    elif m >= 12:
        strengths.append(f"护城河中等（{m:.0f}/20）")
    else:
        risks.append(f"护城河薄弱（{m:.0f}/20）：竞争优势不明显，利润容易被侵蚀")

    if g >= 13:
        strengths.append(f"成长稳健（{g:.0f}/15）：营收与EPS双双高增长")
    elif g >= 8:
        strengths.append(f"成长平稳（{g:.0f}/15）")
    else:
        risks.append(f"成长乏力（{g:.0f}/15）：需关注业务天花板")

    if bs >= 13:
        strengths.append(f"资产负债表健康（{bs:.0f}/15）：负债低、现金流持续为正")
    elif bs <= 5:
        risks.append(f"财务安全性偏低（{bs:.0f}/15）：负债较重或现金流不稳定")

    if v >= 12:
        strengths.append(f"估值合理（{v:.0f}/15）")
    elif v <= 4:
        risks.append(f"估值偏高（{v:.0f}/15）：当前价格可能已透支未来增长")

    # 趋势
    if ts >= 8:
        strengths.append(f"财务趋势健康（趋势分{ts}/10）：多项指标持续改善")
    elif ts <= 3:
        risks.append(f"财务趋势恶化（趋势分{ts}/10）：多项指标持续下滑，需格外谨慎")

    if rp < 0:
        reason_str = f"（{risk_reason}）" if risk_reason else ""
        risks.append(f"存在风险标记{reason_str}，共扣 {abs(rp):.0f} 分")
    elif risk_reason:
        risks.append(f"注意：{risk_reason}")

    ms = result["management_score"]
    if ms >= 4 and mgmt_reason:
        strengths.append(f"管理层优质（{ms:.1f}/5）：{mgmt_reason}")
    elif ms <= 2:
        risks.append(f"管理层评分偏低（{ms:.1f}/5）")

    if not risks:
        risks.append("当前无明显重大风险（任何投资仍存在不确定性）")

    disclaimer = (
        "★ 重要提示：评分高 ≠ 建议立即买入 ★\n"
        "  本工具是【研究优先级筛选器】，帮你找到值得深入了解的公司。\n"
        "  在实际投资前，还需要：\n"
        "    1. 阅读最新年报和季报\n"
        "    2. 研究当前竞争格局和行业趋势\n"
        "    3. 结合个人风险承受能力\n"
        "    4. 本工具不构成任何投资建议，盈亏自负"
    )
    return {"strengths": strengths, "risks": risks, "disclaimer": disclaimer}
