# ============================================================
# web_app.py  —  Streamlit Web（Aura Logic 视觉重构 · 「Kv的选股小猫」）
#
# 网页入口：输入股票代码 → 调 research_service.run_research → 卡片化展示研究卡片。
#
# 原则（不变）：
#   - 不复制 main.py 逻辑：核心一律走 research_service.run_research。
#   - 不改评分公式 / research_priority / AI 逻辑 / store 白名单 / CSV。
#   - 默认只读；仅 WEB_WRITABLE=1 时可写。
#   - 仅研究辅助，不输出买入/卖出/持有建议；缺失=待补录，非公司差。
#
# Aura Logic：白底、Inter 字体栈、#111111 主色、#6E56CF 紫色点缀、#EDEDED 1px 边框、
#   16px 卡片圆角、极轻阴影、移动优先 + 桌面两栏。小猫为内联 SVG（离线、无远程 URL、无新依赖），
#   仅用于搜索提交键 / loading / 空状态，不喧宾夺主。
#
# 运行：
#   streamlit run web_app.py                  # 只读模式（默认）
#   WEB_WRITABLE=1 streamlit run web_app.py   # 可写模式（本地）
# ============================================================

import base64
import os

import pandas as pd
import streamlit as st

import research_service
from research_service import ANNUAL_PATH
from financial_analyzer import compute_all_metrics

READONLY = os.environ.get("WEB_WRITABLE", "").strip() != "1"

st.set_page_config(page_title="Kv的选股小猫", page_icon="🐱", layout="wide")


# ── 内联像素小猫（SVG → data URI；离线、无远程、无新依赖）──────────────
# 低分辨率像素感、克制：tan 身体 + 深色描边 + 紫色小项圈点缀。crispEdges 保持像素硬边。
_CAT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" '
    'shape-rendering="crispEdges">'
    '<g fill="#111111">'  # 耳朵 + 轮廓
    '<rect x="3" y="2" width="2" height="2"/><rect x="11" y="2" width="2" height="2"/>'
    '<rect x="2" y="4" width="2" height="1"/><rect x="12" y="4" width="2" height="1"/>'
    '</g>'
    '<g fill="#cdab86">'  # 头/身（tan）
    '<rect x="3" y="4" width="10" height="8"/>'
    '<rect x="4" y="3" width="8" height="1"/>'
    '<rect x="2" y="6" width="1" height="4"/><rect x="13" y="6" width="1" height="4"/>'
    '</g>'
    '<g fill="#111111">'  # 眼睛 + 鼻子
    '<rect x="5" y="7" width="1" height="2"/><rect x="10" y="7" width="1" height="2"/>'
    '<rect x="7" y="9" width="2" height="1"/>'
    '</g>'
    '<rect x="5" y="11" width="6" height="1" fill="#6E56CF"/>'  # 紫色小项圈
    '</svg>'
)
_CAT_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_CAT_SVG.encode("utf-8")).decode("ascii")


# ── 全局 Aura Logic 主题（scoped CSS；渐进增强，失效仍可用）──────────────
_CSS = f"""
<style>
:root {{
  --bg:#FFFFFF; --ink:#111111; --muted:#6B7280; --line:#EDEDED;
  --accent:#6E56CF; --warm:#E8A33D; --warm-bg:#FFF7EC;
}}
html, body, [data-testid="stAppViewContainer"] {{ background:var(--bg); }}
.block-container {{ max-width: 1040px; padding-top: 2.0rem; padding-bottom: 4rem; }}
/* 字体栈：Inter / SF Pro / PingFang SC / system-ui */
html, body, [class*="css"], .stMarkdown, .stMetric, button, input {{
  font-family: 'Inter','SF Pro Display','SF Pro Text','PingFang SC','Noto Sans SC',system-ui,-apple-system,sans-serif;
}}
/* 收起 Streamlit 默认 chrome，回归产品感 */
#MainMenu, header[data-testid="stHeader"], footer {{ visibility: hidden; height: 0; }}
/* 卡片：白底 + 轻边框 + 16px 圆角 + 极轻阴影 */
div[data-testid="stVerticalBlockBorderWrapper"] {{
  border-radius: 16px !important; border-color: var(--line) !important;
  box-shadow: 0 1px 2px rgba(0,0,0,0.03);
}}
/* 主分卡片：更强存在感，规则总分是视觉主角 */
.st-key-mg-score-hero {{ border-width: 1.5px !important; }}
.st-key-mg-score-hero div[data-testid="stMetricValue"] {{
  font-size: 3.0rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.05;
}}
.st-key-mg-score-hero label[data-testid="stMetricLabel"] p {{
  font-size: 11px; letter-spacing: 0.05em; color: var(--muted); text-transform: uppercase;
}}
/* 辅助分：弱化一档，不抢主视觉 */
.st-key-mg-aux div[data-testid="stMetricValue"] {{ font-size: 1.15rem; font-weight: 600; }}
/* caption 克制 */
div[data-testid="stCaptionContainer"] p {{ color: var(--muted); }}
/* 输入框做成 pill */
.st-key-mg-cmdbar div[data-baseweb="input"] > div {{
  border-radius: 9999px !important; border:1px solid var(--line) !important;
  background:#fff !important; box-shadow: 0 1px 2px rgba(0,0,0,0.03);
}}
/* 小猫提交按钮：把内联 SVG 作为背景，按钮本身做成圆形 */
.st-key-mg-catbtn button {{
  border-radius: 9999px !important; border:1px solid var(--line) !important;
  background:#fff no-repeat center / 26px 26px; min-height: 46px;
  background-image: url("{_CAT_DATA_URI}");
  color: transparent !important;
}}
.st-key-mg-catbtn button:hover {{ background-color:#F8F7FB !important; border-color:#D9D5EC !important; }}
/* chips（示例 / 最近查询）做成轻量 pill */
.st-key-mg-chips button, .st-key-mg-hist button {{
  border-radius: 9999px !important; border:1px solid var(--line) !important;
  background:#FAFAFA !important; color:var(--ink) !important; font-weight:500;
}}
/* 暖色（待补录）——清楚但不吓人。错误态命令栏暖边由页尾条件性全局 CSS 应用。 */
.st-key-mg-missing {{ border-color:#F0D9A8 !important; background:var(--warm-bg) !important; }}
@media (max-width: 680px) {{
  .block-container {{ padding-left:0.7rem; padding-right:0.7rem; }}
  .st-key-mg-score-hero div[data-testid="stMetricValue"] {{ font-size: 2.4rem; }}
}}
</style>
"""


def _inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


def _cat_img(px=22):
    return f'<img src="{_CAT_DATA_URI}" width="{px}" height="{px}" alt="像素小猫" ' \
           f'style="image-rendering:pixelated;vertical-align:middle;"/>'


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


def _card(key=None):
    """统一卡片容器（白底 + 轻边框 + 16px 圆角）。"""
    return st.container(border=True, key=key)


# ── 结果渲染（移动单栏堆叠 / 桌面两栏：左主栏 + 右辅栏）─────────────────
def render_result(res):
    result    = res["result"]
    canonical = res["canonical"]
    market    = str(result.get("market", "")).upper()
    market_label = {"US": "美股 (US)", "CN": "A股 (CN)"}.get(market, "未知")
    name = result.get("long_name") or result.get("name") or canonical
    pending    = str(result.get("final_decision", "")) == "数据不足（待补录）"
    incomplete = str(result.get("data_status", "")) == "待补录"

    aidyn = result.get("ai_dynamic")
    rb    = _num(result, "total_score")
    mt    = _machine_total(result)

    # 标的头部（非 ### 章节）
    st.markdown(f"## {result.get('ticker', canonical)}　<span style='color:#6B7280;font-weight:500;font-size:15px'>"
                f"{name} · {market_label}</span>", unsafe_allow_html=True)

    main, side = st.columns([1.55, 1], gap="large")

    # ===== 左主栏：评分主角 + 优势/风险 + 研究优先级 =====
    with main:
        st.markdown("### 评分")
        if pending:
            st.warning("数据不足（待补录）：缺失关键财务字段，暂不展示规则总分 / 机器财务分 / "
                       "AI 动态分 / 最终预览分等分数；补齐数据后可复评。缺失 = 待补录，非公司差。")
        else:
            # 主卡片：规则总分（权威主分，CSS 放大）—— 第 1 个 metric
            with _card(key="mg-score-hero"):
                st.metric("规则总分", f"{rb:.0f} / 100",
                          help="含质化+风险的权威口径（满分 100），比机器财务分多 25 分质化——这是该看的主分。")
                st.caption("基于财务质量、估值、安全边际与成长性规则评分。")
            # 辅助评分（弱化一档）—— 第 2–5 个 metric，全部在左栏，保证 metric 顺序
            with _card(key="mg-aux"):
                st.caption("辅助评分（非权威主分，仅供参考）")
                a1, a2 = st.columns(2)
                a1.metric("机器财务分", f"{mt:.0f} / 75",
                          help="纯客观四维（生意质量/成长/负债/估值），满分 75。")
                if not aidyn:
                    a2.metric("AI 动态分", "未生成",
                              help="实验·AI 暂定·非人工确认；不参与正式评分与排序，不构成投资建议。")
                else:
                    _as = aidyn.get("ai_score")
                    a2.metric("AI 动态分", "数据不足" if _as is None else f"{_as} / 100",
                              help="实验·AI 暂定·非人工确认；不参与正式评分与排序，不构成投资建议。")
                a3, a4 = st.columns(2)
                if not aidyn:
                    a3.metric("AI 置信度", "—", help="AI 动态评分未生成时无置信度。")
                else:
                    _as = aidyn.get("ai_score")
                    a3.metric("AI 置信度",
                              "0.0（数据不足）" if _as is None else f"{_fmt(aidyn.get('confidence'))}（上限 0.50）",
                              help="AI 置信度上限 0.50；仍需人工复核。")
                fsp = result.get("final_score_preview")
                try:
                    fsp_s = f"{float(fsp):.0f} / 100"
                except (TypeError, ValueError):
                    fsp_s = f"{rb:.0f} / 100"
                a4.metric("最终预览分（实验）", fsp_s,
                          help="实验字段；当前 AI 权重 = 0.0，AI 动态分不计入该分、不影响排序。")
                st.caption("🤖 AI 动态分为**实验·AI 暂定，不参与正式评分与排序，不构成投资建议**；needs_human_review 恒为 true。")
                st.caption("⚠ 最终预览分（实验）：当前 **AI 权重 = 0.0**，AI 动态分**不计入该分、不影响排序**；非正式总分。")
                if aidyn:
                    st.markdown(f"**AI 评级**：{_fmt(aidyn.get('ai_rating'))}　|　需人工复核：是")
                    st.caption(f"AI 分析：{_fmt(aidyn.get('ai_reasoning'))}")
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

        # 关键优势 / 主要风险（三态兜底）
        def _points(title, key):
            st.markdown(f"### {title}")
            with _card():
                if not aidyn:
                    st.caption("AI 动态评分未生成。")
                    return
                items = aidyn.get(key) or []
                if not items:
                    st.caption("暂无（数据不足或本次未生成相关条目）。")
                    return
                for it in items:
                    em = str(it.get("evidence_metric", "")).strip()
                    tail = f"（依据：{em}）" if em else ""
                    st.markdown(f"- {_fmt(it.get('point'))}{tail}")

        _points("关键优势", "key_strengths")
        _points("主要风险", "key_risks")

        # 研究优先级
        prio, why = res["research_priority"]
        st.markdown("### 研究优先级")
        with _card():
            st.markdown(f"#### {prio}")
            st.caption("⚠ 「研究优先级」只表示**值得花多少研究精力**；研究优先级高 ≠ 好公司，≠ 可买入；非买卖建议。")
            st.write(why)

    # ===== 右辅栏：数据状态 + 核心财务 + 缺失字段 + 折叠详情 =====
    with side:
        with _card():
            _dq = ("🟠 部分待补录" if (pending or incomplete) else "🟢 完整")
            st.markdown("**数据状态**")
            st.caption(f"{_dq}　·　{_fmt(result.get('data_date'))}")
            st.caption(f"来源：{_fmt(result.get('data_source'))}（年度 5 年口径，可能非最新季报）")

        st.markdown("### 核心财务数据")
        with _card():
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
            st.caption("机器自动计算（年度 5 年口径）；空值表示「待补录」，非公司差。")

        st.markdown("### 缺失字段")
        mf = str(result.get("missing_fields", "") or "").strip()
        with _card(key=("mg-missing" if (mf and mf != "（无）") else None)):
            if mf and mf != "（无）":
                st.markdown(f"**待补录**：{mf}")
                st.caption("数据不足 = **待补录**，不代表公司差；补齐数据后可重新评估。")
            else:
                st.success("关键量化字段齐全。")
            if pending:
                st.caption("严重程度：数据不足（待补录）——已整体降级、暂不展示分数。")
            elif incomplete:
                st.caption("严重程度：部分待补录——分数已出，但部分维度（如 ROIC）未计入。")
            if aidyn:
                mdw = aidyn.get("missing_data_warnings") or []
                if mdw:
                    st.caption("AI 标注的缺失（同源于数据校验，仅回显不重判）：" + "、".join(str(x) for x in mdw))

        # 旧 AI 初步质化判断（持久化初判，折叠降权）
        with st.expander("AI 初判详情（持久化初判，与本次动态评分不同）"):
            st.caption("ℹ️ 持久化旧版初判（仅可写模式由 generate_ai_for 写入），与上方「AI 动态分（本次实验）」"
                       "**不是同一来源**；均为 **AI 暂定·非人工确认**，护城河/管理层以**人工复核**为准，AI 永不权威。")
            if str(result.get("ai_model", "")).strip():
                b1, b2, b3 = st.columns(3)
                b1.metric("护城河（AI暂定）/10", _fmt(result.get("ai_moat_score")))
                b2.metric("管理层（AI暂定）/10", _fmt(result.get("ai_management_score")))
                b3.metric("置信度", _fmt(result.get("ai_confidence")))
                st.markdown(f"**AI 判断**：{_fmt(result.get('ai_reason'))}")
                st.markdown(f"**待补证据**：{_fmt(result.get('ai_evidence_needed'))}")
            else:
                st.write("未生成（数据不足时不生成 AI 初判）。")

        st.markdown("### 完整研究卡片")
        card = (res.get("card_text") or "").strip()
        with st.expander("展开完整研究卡片（复制 / 导出用）", expanded=False):
            if card:
                st.code(card, language="text")
                st.caption("💡 点击代码框右上角复制图标可一键复制全文。")
            else:
                st.caption("研究卡片正文暂未生成。")
        st.download_button("⬇ 下载研究卡片（Markdown）", data=card or "（暂无内容）",
                           file_name=f"{canonical}_card.md", mime="text/markdown",
                           use_container_width=True)

        if res.get("warnings"):
            with st.expander(f"数据校验提示（{len(res['warnings'])} 条）"):
                for w in res["warnings"]:
                    st.write("• " + str(w))


# ── 命令栏（搜索框 + 小猫提交键）+ 示例 chips（共用）─────────────────
def _command_bar():
    # 始终用 mg-cmdbar 容器；错误态的暖色边框由页尾条件性全局 CSS 回溯应用（同屏生效）
    with st.container(key="mg-cmdbar"):
        with st.form("query_form", clear_on_submit=False, border=False):
            ci, cb = st.columns([8, 1], vertical_alignment="center")
            tk = ci.text_input("股票代码", placeholder="输入股票代码，例如 AAPL / 600519.SH",
                               label_visibility="collapsed")
            with cb.container(key="mg-catbtn"):
                sub = st.form_submit_button("分析", use_container_width=True)
    return tk, sub


def _local_examples():
    """从本地 stocks.csv 取前几个代码作示例（只读、try/except 降级）。"""
    try:
        import csv as _csv
        out = []
        if os.path.exists(research_service.INPUT_PATH):
            with open(research_service.INPUT_PATH, encoding="utf-8-sig", newline="") as f:
                for row in _csv.DictReader(f):
                    t = str(row.get("canonical_ticker") or row.get("ticker") or "").strip()
                    if t:
                        out.append(t)
                    if len(out) >= 3:
                        break
        return out or ["AAPL", "MSFT", "600519.SH"]
    except Exception:
        return ["AAPL", "MSFT", "600519.SH"]


def _chips(tickers, key_prefix):
    with st.container(key="mg-chips"):
        cols = st.columns(len(tickers))
        for i, t in enumerate(tickers):
            if cols[i].button(t, key=f"{key_prefix}_{i}_{t}", use_container_width=True):
                st.session_state["_requery"] = t
                st.rerun()


# ── 页面主体 ──────────────────────────────────────────────────
_inject_css()

# 顶部 Header：左产品名 + 右 说明/设置（克制）
_h_left, _h_right = st.columns([3, 1], vertical_alignment="center")
_h_left.markdown("**Kv的选股小猫**")
with _h_right:
    _n1, _n2 = st.columns(2)
    with _n1.popover("说明", use_container_width=True):
        st.caption("Kv的选股小猫：芒格式规则评分 + AI 动态评分（实验）的研究辅助。仅研究优先级排序，"
                   "不构成买入 / 卖出 / 持有建议；缺失字段=待补录，非公司差。")
    with _n2.popover("设置", use_container_width=True):
        st.caption(("当前：只读模式（默认）🔒 不抓取、不写盘。"
                    if READONLY else "当前：可写模式（WEB_WRITABLE=1）✍️ 可能抓取并更新本地 CSV。")
                   + " 切换可写：`WEB_WRITABLE=1 streamlit run web_app.py`。")

# 决定本次查询代码（表单提交 或 chips / 最近查询）
requery = st.session_state.pop("_requery", None)

# 首页 Hero：始终渲染在 mg-hero 容器；本会话提交过查询后由页尾全局 CSS 隐藏（同屏不残留）
with st.container(key="mg-hero"):
    st.markdown("<div style='height:6vh'></div>", unsafe_allow_html=True)
    st.markdown("# 你好，User")
    st.caption("这里是 Kv的选股小猫")

ticker, submitted = _command_bar()

run_ticker = None
if submitted:
    tv = (ticker or "").strip()
    if tv:
        run_ticker = tv
    else:
        st.error("请输入股票代码。")
if requery:
    run_ticker = requery   # 点击示例/最近查询：仍遵守当前 READONLY，绝不绕过

# 标记「本会话已提交过查询」——一旦提交（完整/部分/错误/数据不足），首页 Hero 不再出现
if run_ticker:
    st.session_state["has_submitted_query"] = True
_submitted_before = bool(st.session_state.get("has_submitted_query"))

# 首页空状态（仅从未提交过查询时）：示例 chips + 弱化大猫
if not _submitted_before:
    st.caption("试试这些本地已有的示例：")
    _chips(_local_examples(), "ex")
    with st.container(key="mg-hero-cat"):
        st.markdown(f"<div style='opacity:.5;margin-top:6vh;text-align:center'>{_cat_img(40)}</div>",
                    unsafe_allow_html=True)

if run_ticker:
    with st.spinner(f"小猫正在分析 {run_ticker}…"):
        # READONLY 始终透传：只读模式下点击示例/最近查询也不会写盘
        res = research_service.run_research(run_ticker, readonly=READONLY)
    if not res.get("ok"):
        if res.get("canonical") is None:
            # 错误态：无法识别代码 → 命令栏暖色边框（页尾全局 CSS 回溯应用）
            st.session_state["cmdbar_error"] = True
            st.markdown(f"{_cat_img(20)} **小猫没找到这个股票代码**", unsafe_allow_html=True)
            st.caption("请检查代码格式，或试试 AAPL / MSFT / 600519.SH")
            _chips(["AAPL", "MSFT", "600519.SH"], "err")
        elif res.get("error") == "本地暂无数据，当前为只读模式":
            # 数据不足 / 本地无数据态（设计里输入框保持常态，不变暖色）
            st.session_state["cmdbar_error"] = False
            with _card():
                st.markdown(f"{_cat_img(28)} **暂时没有足够数据**", unsafe_allow_html=True)
                st.caption("当前为只读模式（不会抓取、不会写盘），本地没有该代码的数据，无法生成可靠评分。")
                st.caption("你可以换一个代码，或用可写模式 / CLI 先生成数据：")
                st.code(f"WEB_WRITABLE=1 streamlit run web_app.py\npython main.py "
                        f"{res.get('canonical') or run_ticker}", language="bash")
            _chips(["AAPL", "MSFT", "600519.SH"], "nodata")
        else:
            st.session_state["cmdbar_error"] = False
            st.error(res.get("error") or "无法生成研究卡片。")
    else:
        st.session_state["cmdbar_error"] = False
        # 更新 session 内最近查询（仅本次会话，不写文件、不持久化）
        canon = res["canonical"]
        hist = st.session_state.setdefault("history", [])
        if canon in hist:
            hist.remove(canon)
        hist.insert(0, canon)
        del hist[8:]
        render_result(res)

# 最近查询（仅本次会话内；点击按当前 READONLY 重查）
hist = st.session_state.get("history", [])
if hist:
    st.divider()
    st.caption(f"最近查询（仅本次会话内，不写文件；按当前{'只读' if READONLY else '可写'}模式重查）")
    with st.container(key="mg-hist"):
        _PER_ROW = 4
        for _start in range(0, len(hist), _PER_ROW):
            _chunk = hist[_start:_start + _PER_ROW]
            cols = st.columns(_PER_ROW)
            for _j, h in enumerate(_chunk):
                if cols[_j].button(h, key=f"hist_{_start + _j}_{h}", use_container_width=True):
                    st.session_state["_requery"] = h
                    st.rerun()

# 同屏视觉修正：全局 CSS 不受发出位置限制，回溯应用到上方已渲染的 mg-hero / mg-cmdbar。
if st.session_state.get("has_submitted_query"):
    st.markdown("<style>.st-key-mg-hero{display:none !important;}</style>", unsafe_allow_html=True)
if st.session_state.get("cmdbar_error"):
    st.markdown(
        '<style>.st-key-mg-cmdbar div[data-baseweb="input"] > div{'
        'border-color:#F0C38A !important; background:#FFF7EC !important;}</style>',
        unsafe_allow_html=True)

st.caption("研究优先级仅表示「值得进一步研究的程度」，不是投资建议。"
           "AI 判断为暂定、非人工确认。缺失字段为待补录，不代表公司差。")
