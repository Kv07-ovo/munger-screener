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

    # 评分并排（实验；AI 动态分不参与正式排序）
    st.markdown("### 评分并排（实验）")
    st.caption("⚠ final_score_preview 为**实验字段**，未经确认**不作为正式排序依据**；非投资建议。")
    aidyn = result.get("ai_dynamic")
    rb    = _num(result, "total_score")
    mt    = _machine_total(result)
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("规则总分", f"{rb:.0f}/100")
    e2.metric("机器财务分", f"{mt:.0f}/75")
    if not aidyn:
        e3.metric("AI 动态分", "未生成")
        e4.metric("AI 置信度", "—")
    else:
        _as = aidyn.get("ai_score")
        e3.metric("AI 动态分", "数据不足" if _as is None else f"{_as}/100")
        e4.metric("AI 置信度", _fmt(aidyn.get("confidence")))
    fsp = result.get("final_score_preview")
    try:
        fsp_s = f"{float(fsp):.0f}/100"
    except (TypeError, ValueError):
        fsp_s = f"{rb:.0f}/100"
    e5.metric("最终预览分", fsp_s)
    if aidyn:
        st.markdown(f"**AI 评级**：{_fmt(aidyn.get('ai_rating'))}　|　"
                    f"**needs_human_review**：`{aidyn.get('needs_human_review')}`")
        st.markdown(f"**AI 分析（ai_reasoning）**：{_fmt(aidyn.get('ai_reasoning'))}")
    else:
        st.info("AI 动态评分未生成（provider 异常 / 校验失败 / 内部异常时显示此项；不影响规则评分与展示）。")
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

    # 研究卡片全文 + 复制 + 下载
    st.markdown("### 研究卡片全文")
    card = res.get("card_text") or ""
    st.code(card, language="text")
    st.caption("💡 点击代码框右上角的复制图标可一键复制全文；或展开下方文本框全选复制。")
    with st.expander("展开纯文本（便于全选复制）"):
        st.text_area("研究卡片文本", value=card, height=320, label_visibility="collapsed")
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

# 顶部运行模式横幅（Web Alpha）
if READONLY:
    st.info("**当前模式：只读模式（WEB_READONLY=1）**　🔒\n\n"
            "只读模式**不会写盘**：不抓取新数据、不建骨架、不写任何本地文件。"
            "本地暂无的代码会提示「本地暂无数据，当前为只读模式」。")
else:
    st.warning("**当前模式：可写模式**　✍️\n\n"
               "可写模式**可能会抓取数据并更新本地 CSV**（`data/stocks.csv`、`data/annual_financials.csv`），"
               "经 store 白名单写入、**不覆盖人工字段**。若只是手测、不想保留数据变化，可用 "
               "`git restore data/stocks.csv data/annual_financials.csv` 丢弃。")

with st.form("query_form"):
    ticker = st.text_input("股票代码", placeholder="AAPL / MSFT / 600519.SH / 000001.SZ")
    submitted = st.form_submit_button("生成研究卡片", type="primary")

# 决定本次要查询的代码：表单提交 或 点击「最近查询」
run_ticker = None
if submitted:
    tv = (ticker or "").strip()
    if tv:
        run_ticker = tv
    else:
        st.error("请输入股票代码。")
requery = st.session_state.pop("_requery", None)
if requery:
    run_ticker = requery   # 点击最近查询：仍遵守当前 READONLY，绝不绕过

if run_ticker:
    with st.spinner(f"正在生成 {run_ticker} 的研究卡片……"):
        # READONLY 始终透传：只读模式下点击最近查询也不会写盘
        res = research_service.run_research(run_ticker, readonly=READONLY)
    if not res.get("ok"):
        if res.get("canonical") is None:
            st.error("无法识别该股票代码，请检查格式，例如 AAPL、MSFT、600519.SH、000001.SZ。")
        elif res.get("readonly") and res.get("canonical"):
            st.error("本地暂无数据，当前为只读模式。请切换可写模式或先用 CLI/可写模式生成数据。")
        else:
            st.error(res.get("error") or "无法生成研究卡片。")
    else:
        # 更新 session 内最近查询（仅本次会话，不写文件、不持久化）
        canon = res["canonical"]
        hist = st.session_state.setdefault("history", [])
        if canon in hist:
            hist.remove(canon)
        hist.insert(0, canon)
        del hist[8:]
        render_result(res)

# 最近查询（仅本次会话内；点击可快速再次查询，遵守当前 READONLY）
hist = st.session_state.get("history", [])
if hist:
    st.divider()
    st.markdown("**最近查询**（仅本次会话内，不写文件）：")
    cols = st.columns(len(hist))
    for i, h in enumerate(hist):
        if cols[i].button(h, key=f"hist_{i}_{h}"):
            st.session_state["_requery"] = h
            st.rerun()

st.divider()
st.caption("研究优先级仅表示「值得进一步研究的程度」，不是投资建议。"
           "AI 判断为暂定、非人工确认。缺失字段为待补录，不代表公司差。")
