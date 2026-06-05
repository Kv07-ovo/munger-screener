"""None-aware 动态评分引擎（MVP）。

与 scorer.py（legacy，缺失按 0/兜底）的关键区别：
  - 缺失字段不计入该维度【分母】，并在维度内【重归一化】到名义满分（缺失不拖累、不白送）；
  - D/E 缺失不给分、de<=0 视为异常（不给低负债满分）；PE 无效（<=0 或 >500）按缺失处理；
  - 输出 data_confidence（已用评分字段 / 应有评分字段）与 score_breakdown（逐维度可解释）。

阈值【镜像 scorer.py】，由 tests/test_score_engine.py 的 parity 测试防止漂移
（完整数据股票：引擎总分 == scorer.score_stock 的 total_score）。
moat / management / risk_penalty 复用 scorer.py（人工主观分，本轮不重归一化、不改）。
纯函数：不读写文件、不联网、不依赖 web 框架；total_score 仍为 100 分制。
"""
from __future__ import annotations

import scorer

# data_confidence 的「应有评分字段」（10 项量化财务字段；fcf_positive_years 计一次）。
# 人工字段（moat/management）不计入数据完整度——那是另一类完整度。
_CONFIDENCE_FIELDS = (
    "roe_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg", "fcf_positive_years",
    "roic_5y_avg", "revenue_growth_5y_cagr", "eps_growth_5y_cagr",
    "debt_to_equity", "pe", "fcf_yield",
)


# ---- 单指标 → 分数（镜像 scorer.py 阈值；只对非 None 值调用）----
def _roe_pts(v):    return 9 if v >= 20 else 6 if v >= 15 else 3 if v >= 10 else 0
def _gm_pts(v):     return 7 if v >= 50 else 4 if v >= 30 else 2 if v >= 20 else 0
def _nm_pts(v):     return 5 if v >= 20 else 3 if v >= 10 else 2 if v >= 5 else 0
def _fcfyrs_pts(v): return max(0, min(int(v), 5))
def _roic_pts(v):   return 4 if v >= 25 else 3 if v >= 20 else 2 if v >= 15 else 1 if v >= 10 else 0
def _cagr_pts(v):   return 7 if v >= 10 else 4 if v >= 5 else 2 if v >= 0 else 0
def _pe_pts(v):     return 6 if v <= 15 else 5 if v <= 25 else 3 if v <= 35 else 1 if v <= 50 else 0
def _fcfy_pts(v):   return 6 if v >= 5 else 4 if v >= 3 else 2 if v >= 1 else 0
def _de_pts(v):     return 10 if v <= 0.5 else 7 if v <= 1.0 else 3 if v <= 2.0 else 0


def _sf(row, field):
    return scorer._safe_float_strict(row.get(field))


def _renorm(achieved, possible, nominal_max):
    """维度内重归一化：present 字段决定占比，按名义满分缩放；无 present → 0。"""
    if possible <= 0:
        return 0.0
    return round(achieved / possible * nominal_max, 2)


def _quality(row):
    used, missing, notes = [], [], []
    ach = pos = 0.0
    for field, mx, fn in (
        ("roe_5y_avg", 9, _roe_pts), ("gross_margin_5y_avg", 7, _gm_pts),
        ("net_margin_5y_avg", 5, _nm_pts), ("fcf_positive_years", 5, _fcfyrs_pts),
        ("roic_5y_avg", 4, _roic_pts),
    ):
        v = _sf(row, field)
        if v is None:
            missing.append(field)
        else:
            used.append(field); pos += mx; ach += fn(v)
    return {"dimension": "quality", "score": _renorm(ach, pos, 30), "max_score": 30,
            "used_fields": used, "missing_fields": missing, "notes": notes}


def _growth(row):
    used, missing, notes = [], [], []
    ach = pos = 0.0
    rev = _sf(row, "revenue_growth_5y_cagr")
    eps = _sf(row, "eps_growth_5y_cagr")
    for field, v in (("revenue_growth_5y_cagr", rev), ("eps_growth_5y_cagr", eps)):
        if v is None:
            missing.append(field)
        else:
            used.append(field); pos += 7; ach += _cagr_pts(v)
    if rev is not None and eps is not None:          # 双增长奖励仅在两项都在时计入分母
        pos += 1; ach += 1 if (rev > 0 and eps > 0) else 0
    return {"dimension": "growth", "score": _renorm(ach, pos, 15), "max_score": 15,
            "used_fields": used, "missing_fields": missing, "notes": notes}


def _balance_sheet(row):
    used, missing, notes = [], [], []
    ach = pos = 0.0
    de_status, de = scorer.classify_debt_to_equity(row.get("debt_to_equity"))
    if de_status == "missing":
        missing.append("debt_to_equity")
    elif de_status == "invalid":
        used.append("debt_to_equity"); pos += 10; ach += 0
        notes.append(f"debt_to_equity 异常(≤0，疑负权益)，按高风险不给低负债分（原值 {de}）")
    else:
        used.append("debt_to_equity"); pos += 10; ach += _de_pts(de)
    fy = _sf(row, "fcf_positive_years")
    if fy is None:
        missing.append("fcf_positive_years")
    else:
        used.append("fcf_positive_years"); pos += 5; ach += 5 if int(fy) >= 5 else 0
    return {"dimension": "balance_sheet", "score": _renorm(ach, pos, 15), "max_score": 15,
            "used_fields": used, "missing_fields": missing, "notes": notes}


def _valuation(row):
    used, missing, notes = [], [], []
    ach = pos = 0.0
    pe = _sf(row, "pe")
    fcf = _sf(row, "fcf_yield")
    if pe is not None and (pe <= 0 or pe > 500):     # 无效 PE 按缺失处理（不再当 0 分拖累）
        notes.append(f"PE 无效({pe:g})，不计入估值"); pe = None
    if pe is None:
        missing.append("pe")
    else:
        used.append("pe"); pos += 6; ach += _pe_pts(pe)
    if fcf is None:
        missing.append("fcf_yield")
    else:
        used.append("fcf_yield"); pos += 6; ach += _fcfy_pts(fcf)
    if pe is not None and fcf is not None:           # 双合理奖励仅两项都在时计入分母
        pos += 3; ach += 3 if (pe <= 25 and fcf >= 3) else 0
    base = _renorm(ach, pos, 15)
    pct = _sf(row, "pe_percentile_5y")               # PE 分位调整：有则 ±，无则 0；末尾夹 [0,15]
    if pct is not None:
        if pct <= 25:
            base += 1; notes.append("PE 历史低位 +1")
        elif pct > 80:
            base += -3; notes.append(f"PE {pct:.0f}% 历史高位 -3")
    score = round(min(15.0, max(0.0, base)), 2)
    return {"dimension": "valuation", "score": score, "max_score": 15,
            "used_fields": used, "missing_fields": missing, "notes": notes}


def _moat(row):
    moat_s, _calc_avg, subs = scorer.calc_moat_details(row)
    present = bool(subs) or scorer._safe_float_strict(row.get("moat_score")) is not None
    return {"dimension": "moat", "score": round(moat_s, 2), "max_score": 20,
            "used_fields": ["moat(人工)"] if present else [],
            "missing_fields": [] if present else ["moat(人工)"],
            "notes": ["人工主观分（本轮不重归一化）"]}


def _management(row):
    mgmt = scorer.score_management(row)
    present = scorer._safe_float_strict(row.get("management_score")) is not None
    return {"dimension": "management", "score": round(mgmt, 2), "max_score": 5,
            "used_fields": ["management(人工)"] if present else [],
            "missing_fields": [] if present else ["management(人工)"],
            "notes": ["人工主观分（本轮不重归一化）"]}


def compute(row):
    """对一行（已合并年度财务的 stocks row / metrics dict）做 None-aware 动态评分。

    返回 dict：
      - total_score：[0,100] 动态总分（缺失重归一化、D/E 修复后）；
      - data_confidence：已用评分字段 / 应有评分字段（0..1）；
      - score_breakdown：每维 {dimension, score, max_score, used_fields, missing_fields, notes}。
    """
    row = row or {}
    dims = [_quality(row), _growth(row), _balance_sheet(row),
            _valuation(row), _moat(row), _management(row)]
    risk_penalty, _ = scorer.calc_risk_penalty(row)
    total = round(min(100.0, max(0.0, sum(d["score"] for d in dims) - risk_penalty)), 2)

    present = sum(1 for f in _CONFIDENCE_FIELDS
                  if scorer._safe_float_strict(row.get(f)) is not None)
    data_confidence = round(present / len(_CONFIDENCE_FIELDS), 2)

    breakdown = list(dims)
    breakdown.append({
        "dimension": "risk_penalty", "score": round(-risk_penalty, 2), "max_score": 0,
        "used_fields": ["risk_note(人工)"] if risk_penalty else [],
        "missing_fields": [], "notes": [],
    })
    return {"total_score": total, "data_confidence": data_confidence,
            "score_breakdown": breakdown}
