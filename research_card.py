# ============================================================
# research_card.py  —  芒格式选股研究引擎 v2.3.0-alpha1
#
# 职责：把单只股票的评分结果渲染成一张「自动研究卡片」。
#
#   关键原则（与项目目标一致）：
#     1. 机器财务评分（客观，75分制）与质化评分（护城河/管理层/风险）分区展示。
#     2. 质化分优先用人工（权威）；人工为空显示"未评估"，绝不把 0 当"差"。
#     3. AI 区域 alpha1 固定显示"未生成（alpha2 启用）"，不接 LLM。
#     4. 数据不足时不输出任何公司质量结论。
#     5. 只输出研究优先级，不输出买入/卖出/持有建议。
#
#   纯展示，不算分（只复用 scorer 已算好的分项相加）、不写文件、无第三方依赖。
# ============================================================

import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_W = 70


def _uf(val):
    if val is None:
        return "未填写"
    s = str(val).strip()
    return s if s else "未填写"


def _market_label(market):
    return {"US": "美股", "CN": "A股"}.get(str(market).strip().upper(), "未知")


def _pending(result):
    """数据不足（待补录）→ 不展示质量结论。"""
    return (str(result.get("final_decision", "")) == "数据不足（待补录）"
            or str(result.get("data_status", "")) == "待补录")


def _f(result, key, default=0.0):
    try:
        return float(result.get(key, default) or default)
    except (ValueError, TypeError):
        return default


def _bar(score, mx, width=16):
    filled = round(score / mx * width) if mx > 0 else 0
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


# ── 机器财务分（客观，满分75）= 已算好的分项相加，不改算法 ──────────
_MACHINE_DIMS = [
    ("生意质量", "quality_score",       30),
    ("成长稳定", "growth_score",         15),
    ("负债安全", "balance_sheet_score",  15),
    ("估值合理", "valuation_score",      15),
]
_MACHINE_MAX = sum(m for _, _, m in _MACHINE_DIMS)   # 75


def _machine_total(result):
    return sum(_f(result, k) for _, k, _ in _MACHINE_DIMS)


def _qual_evaluated(result):
    """人工护城河是否已评：moat_score>0 或有 moat_reason。"""
    try:
        ms = float(str(result.get("moat_score", "")).strip() or 0)
    except ValueError:
        ms = 0.0
    return ms > 0 or bool(str(result.get("moat_reason", "")).strip())


def _priority(machine_total, pending):
    if pending:
        return "数据不足，暂不排序"
    if machine_total >= 60:
        tier = "高"
    elif machine_total >= 45:
        tier = "中"
    else:
        tier = "低"
    return f"{tier}（仅基于客观财务，未含质化，非买卖建议）"


def render(result):
    """打印一张研究卡片。"""
    code     = result.get("ticker", "?")
    name     = result.get("long_name") or result.get("name") or code
    market   = result.get("market", "")
    currency = result.get("currency", "")
    sector   = result.get("sector", "")
    industry = result.get("industry", "")
    pending  = _pending(result)

    print("\n" + "=" * _W)
    print(f"  研究卡片 · {code} · {_uf(name)}    [{_market_label(market)} / {_uf(currency)}]")
    print(f"  板块: {_uf(sector)}   行业: {_uf(industry)}   "
          f"数据日期: {_uf(result.get('data_date'))}")
    print("─" * _W)

    # ── 数据完整度 ──────────────────────────────────────────────
    fin_state  = "·数据不足" if pending else "✓完整"
    qual_state = "✓人工已评" if _qual_evaluated(result) else "·待补录"
    print(f"  数据完整度:  机器财务 {fin_state}   |  AI质化 ·未生成   |  人工 {qual_state}")
    print("─" * _W)

    # ── 机器财务评分（客观，75）─────────────────────────────────
    mt = _machine_total(result)
    print("  【机器财务评分（客观，满分75）】", end="")
    if pending:
        print("      数据不足（待补录）")
        print("    缺失关键财务字段，暂不展示分项与结论。")
    else:
        print(f"      {mt:.1f} / {_MACHINE_MAX}")
        for label, key, mx in _MACHINE_DIMS:
            sc = _f(result, key)
            print(f"    {label} [{_bar(sc, mx)}] {sc:.0f}/{mx}")
    print("─" * _W)

    # ── 质化评分（人工权威 / AI暂定，与机器分分开）──────────────
    human   = _qual_evaluated(result)
    ai_on   = bool(str(result.get("ai_model", "")).strip())
    ai_moat = str(result.get("ai_moat_score", "")).strip()
    ai_mgmt = str(result.get("ai_management_score", "")).strip()
    ai_conf = str(result.get("ai_confidence", "")).strip() or "—"

    print("  【质化评分（人工权威 / AI暂定，与机器分分开）】")
    if human:
        moat = _f(result, "moat_score")
        mgmt = _f(result, "management_score")
        ref_m = f"（参考 AI暂定 {ai_moat}/10）" if ai_moat else ""
        ref_g = f"（参考 AI暂定 {ai_mgmt}/10）" if ai_mgmt else ""
        print(f"    护城河   人工: {moat:.0f}/20  {ref_m}")
        print(f"    管理层   人工: {mgmt:.0f}/5   {ref_g}")
        print(f"    风险标记 人工: {_uf(result.get('risk_note'))}")
        print(f"    → 人工已确认（权威）；AI 仅作旁边参考")
    elif ai_moat or ai_mgmt:
        print(f"    护城河   人工: 未评估   AI暂定: {ai_moat or '—'}/10"
              f"（置信度 {ai_conf}，AI暂定·非人工确认·待证实）")
        print(f"    管理层   人工: 未评估   AI暂定: {ai_mgmt or '—'}/10"
              f"（置信度 {ai_conf}，AI暂定·非人工确认·需读年报）")
        print(f"    风险标记 人工: 未填写   AI暂定: {_uf(result.get('ai_risk_flags'))}")
        print(f"    → needs_human_review: true（AI 暂定，需人工复核）")
    else:
        print("    护城河   人工: 未评估   AI暂定: 数据不足，未生成（需人工复核）   /10")
        print("    管理层   人工: 未评估   AI暂定: 数据不足，未生成（需人工复核）   /10")
        print("    风险标记 人工: 未填写")
        print("    → needs_human_review: true")
    print("─" * _W)

    # ── AI 初步质化判断详情（AI 暂定，非人工确认）───────────────
    print("  【AI 初步质化判断（AI 暂定，非人工确认）】")
    if ai_on:
        print(f"    模型: {_uf(result.get('ai_model'))}   置信度: {ai_conf}"
              f"   生成: {_uf(result.get('ai_generated_at'))}")
        print(f"    判断: {_uf(result.get('ai_reason'))}")
        print(f"    待补证据: {_uf(result.get('ai_evidence_needed'))}")
        print("    （以上为 AI 暂定、非人工确认，需人工复核；不构成投资建议）")
    else:
        print("    未生成（运行 python main.py <代码> 触发；数据不足时不生成）")
    print("─" * _W)

    # ── 研究优先级（非买卖建议）─────────────────────────────────
    print(f"  【研究优先级】{_priority(mt, pending)}")

    # ── 缺失与下一步 ────────────────────────────────────────────
    steps = []
    if pending:
        mf = result.get("missing_fields", "") or ""
        if str(market).strip().upper() == "CN":
            steps.append("A股自动抓取暂未完整支持，当前仅为待补录研究骨架；请人工补录或后续数据源导入。")
        elif str(market).strip().upper() == "US":
            steps.append(f"抓取财务： python fetcher.py {code}")
        if mf and mf != "（无）":
            steps.append(f"缺失财务字段：{mf}")
    if not _qual_evaluated(result):
        steps.append("补录护城河/管理层/能力圈： python manual_review_helper.py --template")
    if steps:
        print("  【缺失与下一步】")
        for s in steps:
            print(f"    · {s}")

    # ── 免责声明 ────────────────────────────────────────────────
    print("─" * _W)
    print("  仅为研究辅助与优先级排序，不构成任何买入/卖出/持有建议。")
    print("=" * _W)


# ── 自检入口（假数据，不联网）────────────────────────────────
def _selftest():
    complete = {
        "ticker": "DEMO", "long_name": "示例完整公司", "name": "示例", "market": "US",
        "currency": "USD", "sector": "Technology", "industry": "软件", "data_date": "2026-06-01",
        "final_decision": "加入观察池", "data_status": "完整",
        "quality_score": 27, "growth_score": 13, "balance_sheet_score": 14, "valuation_score": 4,
        "moat_score": 9, "management_score": 8, "risk_note": "high valuation",
    }
    pending = {
        "ticker": "600519.SH", "name": "600519.SH", "market": "CN", "currency": "CNY",
        "industry": "Unknown", "data_date": "2026-06-01",
        "final_decision": "数据不足（待补录）", "data_status": "待补录",
        "missing_fields": "市盈率PE、ROIC(5年均值)、ROE(5年均值)",
    }
    print(">>> 完整人工已评样例：")
    render(complete)
    print("\n>>> 待补录（A股）样例：")
    render(pending)


if __name__ == "__main__":
    _selftest()
