# ============================================================
# web_app.py  —  Streamlit Web MVP（v2.5.0）
#
# 网页入口：输入股票代码 → 调 research_service.run_research → 展示研究卡片。
#
# 原则：
#   - 不复制 main.py 逻辑：核心一律走 research_service.run_research。
#   - 不改评分公式 / research_priority / AI 逻辑 / store 白名单。
#   - 只读模式 WEB_READONLY=1：不抓取、不建骨架、不写盘、不写 ai_*。
#   - 第一版只读展示，无人工字段编辑入口。
#   - 仅研究辅助，不输出买入/卖出/持有建议；缺失=待补录，非公司差。
#
# 说明：网页要展示的核心财务里 gross_margin / debt_to_equity 不在 run_research
#       的 result 中，故用既有的 financial_analyzer.compute_all_metrics（评分同源、
#       只读）补取这 6 个财务比率，不修改 research_service。
#
# 运行：
#   streamlit run web_app.py                  # 可写模式（本地）
#   WEB_READONLY=1 streamlit run web_app.py   # 只读模式
# ============================================================

import os

import pandas as pd
import streamlit as st

import research_service
from research_service import ANNUAL_PATH
from financial_analyzer import compute_all_metrics

READONLY = os.environ.get("WEB_READONLY", "").strip() == "1"

st.set_page_config(page_title="芒格式股票研究助手", page_icon="📊", layout="wide")


# ── 展示小工具 ────────────────────────────────────────────────
def _fmt(value, suffix="", pct=False):
    """空值统一显示为「待补录」；否则展示原值（可带 % 或单位）。"""
    s = str(value).strip()
    if s in ("", "未填写", "None", "nan", "（无）"):
        return "待补录"
    return f"{s}%" if pct else f"{s}{suffix}"


def _num(result, key):
    try:
        return float(result.get(key) or 0)
    except (ValueError, TypeError):
        return 0.0


def _machine_total(result):
    """机器财务分（与 research_card 一致）：生意质量+成长+负债+估值，满分 75。"""
    return sum(_num(result, k) for k in
               ("quality_score", "growth_score", "balance_sheet_score", "valuation_score"))


# ── 结果渲染 ──────────────────────────────────────────────────
def render_result(res):
    result    = res["result"]
    canonical = res["canonical"]
    market    = str(result.get("market", "")).upper()
    market_label = {"US": "美股 (US)", "CN": "A股 (CN)"}.get(market, "未知")
    name = result.get("long_name") or result.get("name") or canonical
    pending    = str(result.get("final_decision", "")) == "数据不足（待补录）"
    incomplete = str(result.get("data_status", "")) == "待补录"

    # 标的头部
    st.subheader(f"{result.get('ticker', canonical)} · {name}")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**canonical**：`{canonical}`")
    c2.markdown(f"**市场**：{market_label}")
    c3.markdown(f"**数据来源**：{_fmt(result.get('data_source'))}")
    st.divider()

    # 机器财务评分（客观，满分 75）
    st.markdown("### 机器财务评分（客观，满分 75）")
    if pending:
        st.warning("数据不足（待补录）：缺失关键财务字段，暂不展示机器财务分与质量结论。")
    else:
        mt = _machine_total(result)
        st.metric("机器财务分", f"{mt:.1f} / 75")
        st.progress(min(mt / 75.0, 1.0))
        if incomplete:
            st.caption("◐ 部分机器财务分：部分关键字段缺失（如 ROIC）；缺失=待补录，非公司差。")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("生意质量 /30", f"{_num(result, 'quality_score'):.0f}")
        m2.metric("成长稳定 /15", f"{_num(result, 'growth_score'):.0f}")
        m3.metric("负债安全 /15", f"{_num(result, 'balance_sheet_score'):.0f}")
        m4.metric("估值合理 /15", f"{_num(result, 'valuation_score'):.0f}")
    st.divider()

    # 研究优先级（非买卖建议）
    prio, why = res["research_priority"]
    st.markdown("### 研究优先级")
    st.info(f"**{prio}**")
    st.caption("⚠ 这是「研究优先级」，**不是买卖建议**。研究优先级高 ≠ 可以买入。")
    st.write(why)
    st.divider()

    # AI 初步质化判断（AI 暂定，非人工确认）
    st.markdown("### AI 初步质化判断")
    st.caption("🤖 **AI 暂定，非人工确认**；需人工复核。不构成任何投资建议。")
    if str(result.get("ai_model", "")).strip():
        a1, a2, a3 = st.columns(3)
        a1.metric("护城河（AI暂定）/10", _fmt(result.get("ai_moat_score")))
        a2.metric("管理层（AI暂定）/10", _fmt(result.get("ai_management_score")))
        a3.metric("置信度（confidence）", _fmt(result.get("ai_confidence")))
        st.markdown(f"**AI 判断（ai_reason）**：{_fmt(result.get('ai_reason'))}")
        st.markdown(f"**待补证据（evidence_needed）**：{_fmt(result.get('ai_evidence_needed'))}")
        st.markdown(f"**needs_human_review**：`{_fmt(result.get('needs_human_review'))}`")
    else:
        st.write("未生成（数据不足时不生成 AI 初判）。")
    st.divider()

    # 缺失字段（待补录）
    st.markdown("### 缺失字段")
    mf = str(result.get("missing_fields", "") or "").strip()
    if mf and mf != "（无）":
        st.warning(f"待补录：{mf}")
        st.caption("缺失 = **待补录**，不代表公司差；补齐数据后可重新评估。")
    else:
        st.success("关键量化字段齐全。")
    st.divider()

    # 核心财务数据（6 个比率取自 compute_all_metrics，评分同源、只读）
    st.markdown("### 核心财务数据")
    fin = compute_all_metrics(ANNUAL_PATH).get(str(canonical).upper(), {})
    rows = [
        ("ROE（5年均值）",       _fmt(fin.get("roe_5y_avg"), pct=True)),
        ("ROIC（5年均值）",      _fmt(fin.get("roic_5y_avg"), pct=True)),
        ("毛利率（5年均值）",    _fmt(fin.get("gross_margin_5y_avg"), pct=True)),
        ("净利率（5年均值）",    _fmt(fin.get("net_margin_5y_avg"), pct=True)),
        ("营收增速（5年CAGR）",  _fmt(fin.get("revenue_growth_5y_cagr"), pct=True)),
        ("负债权益比 D/E",       _fmt(fin.get("debt_to_equity"))),
        ("市盈率 PE",            _fmt(result.get("pe"))),
        ("市净率 PB",            _fmt(result.get("pb"))),
        ("市值",                 _fmt(result.get("market_cap"), suffix=("亿" if market == "CN" else ""))),
    ]
    st.table(pd.DataFrame(rows, columns=["指标", "数值"]))
    st.caption("财务比率为机器自动计算（年度数据 5 年口径）；空值表示「待补录」，非公司差。")
    st.divider()

    # 研究卡片全文 + 下载
    st.markdown("### 研究卡片全文")
    card = res.get("card_text") or ""
    st.code(card, language="text")
    st.download_button("⬇ 下载研究卡片（Markdown）", data=card,
                       file_name=f"{canonical}_card.md", mime="text/markdown")

    # 数据校验提示
    if res.get("warnings"):
        with st.expander("数据校验提示（warnings）"):
            for w in res["warnings"]:
                st.write("• " + str(w))


# ── 页面主体 ──────────────────────────────────────────────────
st.title("📊 芒格式股票研究助手")
st.warning("仅作为研究辅助，不构成买入、卖出、持有建议。")

if READONLY:
    st.info("🔒 **只读模式**（WEB_READONLY=1）：不抓取新数据、不建骨架、不写本地文件。"
            "本地暂无的代码会提示「本地暂无数据，当前为只读模式」。")
else:
    st.caption("可写模式：本地无该代码时会自动抓取并建立本地档案"
               "（经 store 白名单，**不覆盖人工字段**）。")

with st.form("query_form"):
    ticker = st.text_input("股票代码", placeholder="AAPL / MSFT / 600519.SH / 000001.SZ")
    submitted = st.form_submit_button("生成研究卡片", type="primary")

if submitted:
    t = (ticker or "").strip()
    if not t:
        st.error("请输入股票代码。")
    else:
        with st.spinner(f"正在生成 {t} 的研究卡片……"):
            res = research_service.run_research(t, readonly=READONLY)
        if not res.get("ok"):
            st.error(res.get("error") or "无法生成研究卡片。")
        else:
            render_result(res)

st.divider()
st.caption("研究优先级仅表示「值得进一步研究的程度」，不是投资建议。"
           "AI 判断为暂定、非人工确认。缺失字段为待补录，不代表公司差。")
