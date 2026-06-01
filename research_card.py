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
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_W = 70


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


# 重大风险标记：出现则不给"高（待人工复核）"
_MAJOR_RISK = ("高杠杆", "盈利能力弱", "趋势恶化")


def _has_major_risk(result):
    flags = str(result.get("ai_risk_flags", "") or "")
    return any(m in flags for m in _MAJOR_RISK)


def _ai_generated(result):
    return bool(str(result.get("ai_model", "")).strip())


def research_priority(result):
    """
    合成研究优先级（仅研究排序，非买卖建议）。返回 (档位, 理由)。
    人工字段优先；AI 可发现"高（待人工复核）"但必须醒目标注非人工确认。
    """
    # 1) 数据不足门槛（A股骨架/关键字段缺失）→ 不参与排序
    if _pending(result):
        return ("数据不足，待补录",
                "关键财务字段缺失或为 A股骨架，暂不参与高/中/低排序；非买卖建议。")

    # 2) 超出能力圈（人工字段）→ 即使机器分高也提示谨慎
    if str(result.get("circle_of_competence", "")).strip().lower() == "outside":
        return ("超出能力圈",
                "circle_of_competence=outside：即使机器分高也需谨慎，建议暂不深入；非买卖建议。")

    mt    = _machine_total(result)
    human = _qual_evaluated(result)
    tier  = "high" if mt >= 60 else "mid" if mt >= 45 else "low"

    if tier == "high":
        if human:
            # 人工已补录：人工质化是否支持高优先级
            if _f(result, "moat_score") >= 12:
                return ("高研究优先级（人工确认）",
                        f"机器财务分高({mt:.0f}/75)，且人工护城河/管理层已补录并支持；非买卖建议。")
            return ("中研究优先级",
                    f"机器财务分高({mt:.0f}/75)，但人工质化偏弱，降级为中；非买卖建议。")
        # 人工未确认：AI 可发现高候选，但必须标注待人工复核
        if _ai_generated(result) and not _has_major_risk(result):
            return ("高研究优先级（待人工复核）",
                    f"机器财务分高({mt:.0f}/75)、数据完整、AI 暂定无重大风险提示。"
                    "★ AI 暂定，非人工确认，不能作为投资建议；请人工复核护城河/管理层/能力圈 ★")
        return ("中研究优先级",
                f"机器财务分高({mt:.0f}/75)，但 AI 暂定存在风险提示或质化尚未确认；非买卖建议。")

    if tier == "mid":
        return ("中研究优先级", f"机器财务分中等({mt:.0f}/75)；非买卖建议。")
    return ("低研究优先级", f"机器财务分偏弱({mt:.0f}/75)，暂不优先深入；非买卖建议。")


def _build_lines(result):
    """构建研究卡片的所有文本行（list[str]），供打印与落盘共用。"""
    code     = result.get("ticker", "?")
    name     = result.get("long_name") or result.get("name") or code
    market   = result.get("market", "")
    currency = result.get("currency", "")
    sector   = result.get("sector", "")
    industry = result.get("industry", "")
    pending  = _pending(result)
    L = []
    def out(s=""): L.append(s)

    out("=" * _W)
    out(f"  研究卡片 · {code} · {_uf(name)}    [{_market_label(market)} / {_uf(currency)}]")
    out(f"  板块: {_uf(sector)}   行业: {_uf(industry)}   "
        f"数据日期: {_uf(result.get('data_date'))}")
    out("─" * _W)

    # ── 数据完整度 ──────────────────────────────────────────────
    fin_state  = "·数据不足" if pending else "✓完整"
    ai_state   = "✓已生成" if _ai_generated(result) else "·未生成"
    qual_state = "✓人工已评" if _qual_evaluated(result) else "·待补录"
    out(f"  数据完整度:  机器财务 {fin_state}   |  AI质化 {ai_state}   |  人工 {qual_state}")
    out("─" * _W)

    # ── 机器财务评分（客观，75）─────────────────────────────────
    mt = _machine_total(result)
    if pending:
        out("  【机器财务评分（客观，满分75）】      数据不足（待补录）")
        out("    缺失关键财务字段，暂不展示分项与结论。")
    else:
        out(f"  【机器财务评分（客观，满分75）】      {mt:.1f} / {_MACHINE_MAX}")
        for label, key, mx in _MACHINE_DIMS:
            sc = _f(result, key)
            out(f"    {label} [{_bar(sc, mx)}] {sc:.0f}/{mx}")
    out("─" * _W)

    # ── 质化评分（人工权威 / AI暂定，与机器分分开）──────────────
    human   = _qual_evaluated(result)
    ai_on   = _ai_generated(result)
    ai_moat = str(result.get("ai_moat_score", "")).strip()
    ai_mgmt = str(result.get("ai_management_score", "")).strip()
    ai_conf = str(result.get("ai_confidence", "")).strip() or "—"

    out("  【质化评分（人工权威 / AI暂定，与机器分分开）】")
    if human:
        moat = _f(result, "moat_score")
        mgmt = _f(result, "management_score")
        ref_m = f"（参考 AI暂定 {ai_moat}/10）" if ai_moat else ""
        ref_g = f"（参考 AI暂定 {ai_mgmt}/10）" if ai_mgmt else ""
        out(f"    护城河   人工: {moat:.0f}/20  {ref_m}")
        out(f"    管理层   人工: {mgmt:.0f}/5   {ref_g}")
        out(f"    风险标记 人工: {_uf(result.get('risk_note'))}")
        out(f"    → 人工已确认（权威）；AI 仅作旁边参考")
    elif ai_moat or ai_mgmt:
        out(f"    护城河   人工: 未评估   AI暂定: {ai_moat or '—'}/10"
            f"（置信度 {ai_conf}，AI暂定·非人工确认·待证实）")
        out(f"    管理层   人工: 未评估   AI暂定: {ai_mgmt or '—'}/10"
            f"（置信度 {ai_conf}，AI暂定·非人工确认·需读年报）")
        out(f"    风险标记 人工: 未填写   AI暂定: {_uf(result.get('ai_risk_flags'))}")
        out(f"    → needs_human_review: true（AI 暂定，需人工复核）")
    else:
        out("    护城河   人工: 未评估   AI暂定: 数据不足，未生成（需人工复核）   /10")
        out("    管理层   人工: 未评估   AI暂定: 数据不足，未生成（需人工复核）   /10")
        out("    风险标记 人工: 未填写")
        out("    → needs_human_review: true")
    out("─" * _W)

    # ── AI 初步质化判断详情（AI 暂定，非人工确认）───────────────
    out("  【AI 初步质化判断（AI 暂定，非人工确认）】")
    if ai_on:
        out(f"    模型: {_uf(result.get('ai_model'))}   置信度: {ai_conf}"
            f"   生成: {_uf(result.get('ai_generated_at'))}")
        out(f"    判断: {_uf(result.get('ai_reason'))}")
        out(f"    待补证据: {_uf(result.get('ai_evidence_needed'))}")
        out("    （以上为 AI 暂定、非人工确认，需人工复核；不构成投资建议）")
    else:
        out("    未生成（运行 python main.py <代码> 触发；数据不足时不生成）")
    out("─" * _W)

    # ── 研究优先级（合成；非买卖建议）───────────────────────────
    prio, why = research_priority(result)
    out(f"  【研究优先级】{prio}")
    out(f"    {why}")

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
        out("  【缺失与下一步】")
        for s in steps:
            out(f"    · {s}")

    # ── 免责声明 ────────────────────────────────────────────────
    out("─" * _W)
    out("  仅为研究辅助与优先级排序，不构成任何买入/卖出/持有建议。")
    out("=" * _W)
    return L


def render(result):
    """打印一张研究卡片。"""
    print()
    print("\n".join(_build_lines(result)))


def save_card(result, notes_dir=None):
    """
    将研究卡片保存为 research_notes/<canonical>_card.md（markdown，正文置于代码块以保留对齐）。
    返回保存路径。仅保存机器/AI/展示信息，不写任何人工字段、不改 stocks.csv。
    """
    import os
    base = os.path.dirname(os.path.abspath(__file__))
    notes_dir = notes_dir or os.path.join(base, "research_notes")
    os.makedirs(notes_dir, exist_ok=True)

    canonical = str(result.get("canonical_ticker") or result.get("ticker") or "UNKNOWN").strip()
    safe = canonical.replace("/", "_").replace("\\", "_")   # 文件名安全
    path = os.path.join(notes_dir, f"{safe}_card.md")

    prio, _ = research_priority(result)
    name = result.get("long_name") or result.get("name") or canonical
    md = []
    md.append(f"# 研究卡片 · {canonical} · {name}")
    md.append("")
    md.append(f"- 研究优先级：**{prio}**（仅研究排序，非买卖建议）")
    md.append(f"- 生成时间：{_now()}")
    md.append("- 说明：本卡片含机器财务分与 AI 暂定判断；AI 暂定·非人工确认，需人工复核。")
    md.append("")
    md.append("```text")
    md.extend(_build_lines(result))
    md.append("```")
    md.append("")
    md.append("> 仅为研究辅助，不构成任何买入/卖出/持有建议。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    return path


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
