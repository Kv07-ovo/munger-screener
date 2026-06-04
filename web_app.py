# ============================================================
# web_app.py  —  Streamlit Web MVP（v2.5.0）
#
# 网页入口：输入股票代码 → 调 research_service.run_research → 展示研究卡片。
#
# 原则：
#   - 不复制 main.py 逻辑：核心一律走 research_service.run_research。
#   - 不改评分公式 / research_priority / AI 逻辑 / store 白名单。
#   - 默认只读；仅 WEB_WRITABLE=1 时可写。只读：不抓取、不建骨架、不写盘、不写 ai_*。
#   - 第一版只读展示，无人工字段编辑入口。
#   - 仅研究辅助，不输出买入/卖出/持有建议；缺失=待补录，非公司差。
#
# 说明：网页要展示的核心财务里 gross_margin / debt_to_equity 不在 run_research
#       的 result 中，故用既有的 financial_analyzer.compute_all_metrics（评分同源、
#       只读）补取这 6 个财务比率，不修改 research_service。
#
# 运行：
#   streamlit run web_app.py                  # 只读模式（默认）
#   WEB_WRITABLE=1 streamlit run web_app.py   # 可写模式（本地）
# ============================================================

import os

import pandas as pd
import streamlit as st

import research_service
from research_service import ANNUAL_PATH
from financial_analyzer import compute_all_metrics

READONLY = os.environ.get("WEB_WRITABLE", "").strip() != "1"

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
    # 首屏状态锚点：数据完整度徽章 + 数据日期/来源（复用已算 pending/incomplete；非 ### 章节）
    _dq = ("🟠 待补录（暂不展示分数）" if pending
           else "🟡 部分（缺 ROIC 等）" if incomplete else "🟢 完整")
    st.caption(f"数据完整度：{_dq}　·　数据日期 {_fmt(result.get('data_date'))}"
               f"　·　来源 {_fmt(result.get('data_source'))}（财务比率为年度 5 年口径，可能非最新季报）")
    st.divider()

    # ── 评分（②规则总分 ③机器财务分 ④AI动态分 ⑤AI置信度 ⑥最终预览分）──
    # pending（数据不足）时整体降级：不展示任何分数，保持口径一致。
    aidyn = result.get("ai_dynamic")
    rb    = _num(result, "total_score")
    mt    = _machine_total(result)
    st.markdown("### 评分")
    if pending:
        st.warning("数据不足（待补录）：缺失关键财务字段，暂不展示规则总分 / 机器财务分 / "
                   "AI 动态分 / 最终预览分等分数；补齐数据后可复评。缺失 = 待补录，非公司差。")
    else:
        # 第 1 行：规则总分（权威主分）/ 机器财务分 / AI 动态分（窄屏不再单行 5 列）
        # 口径解释就近挂到各 metric 的 help（鼠标悬停即见）；安全文案仍保留在下方 caption 正文。
        r1c1, r1c2, r1c3 = st.columns(3)
        r1c1.metric("规则总分", f"{rb:.0f} / 100",
                    help="含质化+风险的权威口径（满分 100），比机器财务分多 25 分质化——这是该看的主分。")
        r1c2.metric("机器财务分", f"{mt:.0f} / 75",
                    help="纯客观四维（生意质量/成长/负债/估值），满分 75；不含护城河/管理层/风险等人工质化。")
        if not aidyn:
            r1c3.metric("AI 动态分", "未生成",
                        help="实验·AI 暂定·非人工确认；不参与正式评分与排序，不构成投资建议。")
        else:
            _as = aidyn.get("ai_score")
            r1c3.metric("AI 动态分", "数据不足" if _as is None else f"{_as} / 100",
                        help="实验·AI 暂定·非人工确认；不参与正式评分与排序，不构成投资建议。")
        # 第 2 行为辅助/实验口径（非权威主分）
        st.caption("以下为辅助与实验口径（非权威主分，仅供参考）：")
        r2c1, r2c2 = st.columns(2)
        if not aidyn:
            r2c1.metric("AI 置信度", "—", help="AI 动态评分未生成时无置信度。")
        else:
            _as = aidyn.get("ai_score")
            r2c1.metric("AI 置信度",
                        "0.0（数据不足）" if _as is None else f"{_fmt(aidyn.get('confidence'))}（上限 0.50）",
                        help="AI 置信度上限 0.50；达上限表示数据覆盖度/一致性已满，仍需人工复核。")
        fsp = result.get("final_score_preview")
        try:
            fsp_s = f"{float(fsp):.0f} / 100"
        except (TypeError, ValueError):
            fsp_s = f"{rb:.0f} / 100"
        r2c2.metric("最终预览分（实验）", fsp_s,
                    help="实验字段；当前 AI 权重 = 0.0，AI 动态分不计入该分、不影响排序。")

        st.caption("🤖 AI 动态分为**实验·AI 暂定，不参与正式评分与排序，不构成投资建议**；needs_human_review 恒为 true。")
        st.caption("⚠ 最终预览分（实验）：当前 **AI 权重 = 0.0**，AI 动态分**不计入该分、不影响排序**；非正式总分、不构成投资建议。")
        if aidyn:
            st.markdown(f"**AI 评级**：{_fmt(aidyn.get('ai_rating'))}　|　需人工复核：是")
            st.markdown(f"**AI 分析**：{_fmt(aidyn.get('ai_reasoning'))}")
        else:
            st.info("AI 动态评分未生成（provider 异常 / 校验失败 / 内部异常时显示此项；不影响规则评分与展示）。")
        with st.expander("机器财务分项（满分 75）"):
            if incomplete:
                st.caption("◐ 部分机器财务分：部分关键字段缺失（如 ROIC）；缺失 = 待补录，非公司差。")
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("生意质量 /30", f"{_num(result, 'quality_score'):.0f}")
            d2.metric("成长稳定 /15", f"{_num(result, 'growth_score'):.0f}")
            d3.metric("负债安全 /15", f"{_num(result, 'balance_sheet_score'):.0f}")
            d4.metric("估值合理 /15", f"{_num(result, 'valuation_score'):.0f}")
    st.divider()

    # ── ⑦关键优势 / ⑧关键风险（来自 ai_dynamic；三态兜底，绝不崩）──
    def _render_points(title, items_key):
        st.markdown(f"### {title}")
        if not aidyn:
            st.caption("AI 动态评分未生成。")
            return
        items = aidyn.get(items_key) or []
        if not items:
            st.caption("暂无（数据不足或本次未生成相关条目）。")
            return
        for it in items:
            em = str(it.get("evidence_metric", "")).strip()
            tail = f"（依据：{em}）" if em else ""
            st.markdown(f"- {_fmt(it.get('point'))}{tail}")

    _render_points("关键优势", "key_strengths")
    _render_points("关键风险", "key_risks")
    st.divider()

    # ── ⑨缺失字段（待补录） + AI missing_data_warnings（同源）──
    st.markdown("### 缺失字段")
    mf = str(result.get("missing_fields", "") or "").strip()
    if mf and mf != "（无）":
        st.warning(f"待补录：{mf}")
        st.caption("数据不足 = **待补录**，不代表公司差；补齐数据后可重新评估。")
    else:
        st.success("关键量化字段齐全。")
    if pending:
        st.caption("严重程度：数据不足（待补录）——已整体降级、暂不展示分数，补齐后可复评。")
    elif incomplete:
        st.caption("严重程度：部分待补录——分数已出，但部分维度（如 ROIC）未计入。")
    if aidyn:
        mdw = aidyn.get("missing_data_warnings") or []
        if mdw:
            st.caption("AI 标注的缺失（同源于数据校验，仅回显不重判）：" + "、".join(str(x) for x in mdw))
    st.divider()

    # ── ⑩研究优先级（非买卖建议）──
    prio, why = res["research_priority"]
    st.markdown("### 研究优先级")
    st.info(f"**{prio}**")
    st.warning("⚠ 「研究优先级」只表示**值得花多少研究精力**；研究优先级高 ≠ 好公司，≠ 可买入；非买卖建议。")
    st.write(why)
    st.divider()

    # ── 核心财务数据（保留，不折叠；放在研究优先级之后、研究卡片之前）──
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

    # ── 旧 AI 初步质化判断（持久化初判，折叠；与本次动态评分区分降权）──
    with st.expander("AI 初步质化判断（持久化初判，与本次动态评分不同）"):
        st.caption("ℹ️ 此为持久化的旧版初判（仅可写模式由 generate_ai_for 写入），"
                   "与上方「AI 动态分（本次实验）」**不是同一来源**；二者均为 **AI 暂定·非人工确认**，"
                   "护城河/管理层/能力圈以**人工复核**为准，AI 永不权威。")
        if str(result.get("ai_model", "")).strip():
            a1, a2, a3 = st.columns(3)
            a1.metric("护城河（AI暂定）/10", _fmt(result.get("ai_moat_score")))
            a2.metric("管理层（AI暂定）/10", _fmt(result.get("ai_management_score")))
            a3.metric("置信度（confidence）", _fmt(result.get("ai_confidence")))
            st.markdown(f"**AI 判断（ai_reason）**：{_fmt(result.get('ai_reason'))}")
            st.markdown(f"**待补证据（evidence_needed）**：{_fmt(result.get('ai_evidence_needed'))}")
            st.markdown("**需人工复核**：是")
        else:
            st.write("未生成（数据不足时不生成 AI 初判）。")
    st.divider()

    # ── ⑪完整研究卡片（折叠；复制/导出用）──
    st.markdown("### 完整研究卡片")
    card = res.get("card_text") or ""
    with st.expander("展开完整研究卡片（复制 / 导出用）", expanded=False):
        st.code(card, language="text")
        st.caption("💡 点击代码框右上角的复制图标可一键复制全文。")
    st.download_button("⬇ 下载研究卡片（Markdown）", data=card,
                       file_name=f"{canonical}_card.md", mime="text/markdown")

    # ── ⑫数据校验提示 ──
    if res.get("warnings"):
        with st.expander("数据校验提示（warnings）"):
            for w in res["warnings"]:
                st.write("• " + str(w))


# ── 页面主体 ──────────────────────────────────────────────────
st.title("📊 芒格式股票研究助手")
st.warning("仅作为研究辅助，不构成买入、卖出、持有建议。")

# 顶部运行模式横幅（Web Alpha）
if READONLY:
    st.info("**当前模式：只读模式（默认）**　🔒\n\n"
            "只读模式**不会写盘**：不抓取新数据、不建骨架、不写任何本地文件。"
            "本地暂无的代码会提示「本地暂无数据，当前为只读模式」。"
            "如需抓取并写盘，请用 `WEB_WRITABLE=1 streamlit run web_app.py`。")
else:
    st.warning("**当前模式：可写模式（WEB_WRITABLE=1）**　✍️\n\n"
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

# 首次进入（未查询且无历史）：给一句定位 + 示例代码按钮（只读取本地代码，try/except 降级）
if not run_ticker and not st.session_state.get("history"):
    st.info("输入股票代码生成研究卡片：美股如 `AAPL`，A股如 `600519.SH`。"
            "默认只读模式，只查本地已有数据（查新代码会提示「本地暂无数据」）。")
    try:
        import csv as _csv
        _examples = []
        if os.path.exists(research_service.INPUT_PATH):
            with open(research_service.INPUT_PATH, encoding="utf-8-sig", newline="") as _f:
                for _row in _csv.DictReader(_f):
                    _t = str(_row.get("canonical_ticker") or _row.get("ticker") or "").strip()
                    if _t:
                        _examples.append(_t)
                    if len(_examples) >= 3:
                        break
        if not _examples:
            _examples = ["AAPL", "600519.SH"]
    except Exception:
        _examples = ["AAPL", "600519.SH"]
    st.caption("试试这些本地已有的示例：")
    _ex_cols = st.columns(len(_examples))
    for _i, _ex in enumerate(_examples):
        if _ex_cols[_i].button(_ex, key=f"ex_{_i}_{_ex}", use_container_width=True):
            st.session_state["_requery"] = _ex
            st.rerun()

if run_ticker:
    with st.spinner(f"正在生成 {run_ticker} 的研究卡片……"):
        # READONLY 始终透传：只读模式下点击最近查询也不会写盘
        res = research_service.run_research(run_ticker, readonly=READONLY)
    if not res.get("ok"):
        if res.get("canonical") is None:
            st.error("无法识别该股票代码，请检查格式，例如 AAPL、MSFT、600519.SH、000001.SZ。")
        elif res.get("error") == "本地暂无数据，当前为只读模式":
            st.error("本地暂无数据，当前为只读模式（不会抓取、不会写盘）。可改用可写模式，或用 CLI 先生成数据：")
            st.code(f"WEB_WRITABLE=1 streamlit run web_app.py\npython main.py {res.get('canonical') or run_ticker}",
                    language="bash")
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
    _PER_ROW = 4
    for _start in range(0, len(hist), _PER_ROW):
        _chunk = hist[_start:_start + _PER_ROW]
        cols = st.columns(_PER_ROW)   # 固定列数回绕，窄屏不再被等分挤爆
        for _j, h in enumerate(_chunk):
            if cols[_j].button(h, key=f"hist_{_start + _j}_{h}", use_container_width=True):
                st.session_state["_requery"] = h
                st.rerun()

st.divider()
st.caption("研究优先级仅表示「值得进一步研究的程度」，不是投资建议。"
           "AI 判断为暂定、非人工确认。缺失字段为待补录，不代表公司差。")
