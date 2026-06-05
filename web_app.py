# ============================================================
# web_app.py  —  Streamlit Web（Aura Logic · 「Kv的选股小猫」）
#   + 本地小猫图（assets/pixel_cat.png，离线 fallback 内联 SVG）
#   + 自适应深色模式（@media prefers-color-scheme: dark；纯 CSS、无 JS）
#   + 中英双语 UI（zh 默认 / en；只翻译固定 UI 文案，不翻译业务数据）
#
# 原则（不变）：核心走 research_service.run_research；不改评分/AI/CSV/store 白名单；
#   默认只读；仅研究辅助，不输出买卖建议；缺失=待补录，非公司差；离线、无远程素材、无新依赖。
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


# ── 小猫素材：本地 assets/pixel_cat.png 优先 → base64 data URI；缺失则 fallback 内联 SVG ──
# 全程离线、无远程 URL、无新依赖。
_CAT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" '
    'shape-rendering="crispEdges">'
    '<g fill="#111111">'
    '<rect x="3" y="2" width="2" height="2"/><rect x="11" y="2" width="2" height="2"/>'
    '<rect x="2" y="4" width="2" height="1"/><rect x="12" y="4" width="2" height="1"/></g>'
    '<g fill="#cdab86">'
    '<rect x="3" y="4" width="10" height="8"/><rect x="4" y="3" width="8" height="1"/>'
    '<rect x="2" y="6" width="1" height="4"/><rect x="13" y="6" width="1" height="4"/></g>'
    '<g fill="#111111">'
    '<rect x="5" y="7" width="1" height="2"/><rect x="10" y="7" width="1" height="2"/>'
    '<rect x="7" y="9" width="2" height="1"/></g>'
    '<rect x="5" y="11" width="6" height="1" fill="#6E56CF"/></svg>'
)
_CAT_SVG_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_CAT_SVG.encode("utf-8")).decode("ascii")

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
# 读取优先级：用户提供的透明抠图 → 旧透明版 → 方底原版 → 内联 SVG
_CAT_PNG_CANDIDATES = ("pixel_cat_cutout.png", "pixel_cat_transparent.png", "pixel_cat.png")
# 向后兼容/便于测试：指向首选（透明）路径
_CAT_PNG_PATH = os.path.join(_ASSETS_DIR, _CAT_PNG_CANDIDATES[0])


def _load_cat_data_uri():
    """按优先级读取本地 PNG → base64 data URI；都不可用则 fallback 内联 SVG。
    返回 (data_uri, asset_name)。绝不联网、无远程 URL。"""
    for _name in _CAT_PNG_CANDIDATES:
        try:
            with open(os.path.join(_ASSETS_DIR, _name), "rb") as f:
                raw = f.read()
            if raw[:8] == b"\x89PNG\r\n\x1a\n":
                return "data:image/png;base64," + base64.b64encode(raw).decode("ascii"), _name
        except Exception:
            continue
    return _CAT_SVG_DATA_URI, "inline_svg"


_CAT_DATA_URI, _CAT_ASSET_NAME = _load_cat_data_uri()
_CAT_IS_LOCAL_PNG = _CAT_DATA_URI.startswith("data:image/png;base64,")


# ── i18n：只翻译固定 UI 文案；业务数据（ticker/公司名/财务值/AI 原文/CLI）不翻译 ──
I18N = {
    "zh": {
        "app_title": "Kv的选股小猫",
        "nav_help": "说明", "nav_settings": "设置",
        "ui_language": "界面语言",
        "help_body": ("Kv的选股小猫：芒格式规则评分 + AI 动态评分（实验）的研究辅助。"
                      "仅研究优先级排序，不构成买入 / 卖出 / 持有建议；缺失字段=待补录，非公司差。"),
        "mode_ro": "当前：只读模式（默认）🔒 不抓取、不写盘。",
        "mode_w": "当前：可写模式（WEB_WRITABLE=1）✍️ 可能抓取并更新本地 CSV。",
        "mode_switch_hint": " 切换可写：`WEB_WRITABLE=1 streamlit run web_app.py`。",
        "hero_hello": "你好，User", "hero_sub": "这里是 Kv的选股小猫",
        "input_placeholder": "输入股票代码，例如 AAPL / 600519.SH",
        "btn_analyze": "分析",
        "examples_hint": "试试这些本地已有的示例：",
        "loading": "小猫正在分析 {t}…",
        "input_empty": "请输入股票代码。",
        "err_title": "小猫没找到这个股票代码",
        "err_hint": "请检查代码格式，或试试 AAPL / MSFT / 600519.SH",
        "nodata_title": "暂时没有足够数据",
        "nodata_l1": "当前为只读模式（不会抓取、不会写盘），本地没有该代码的数据，无法生成可靠评分。",
        "nodata_l2": "你可以换一个代码，或用可写模式 / CLI 先生成数据：",
        "err_generic": "无法生成研究卡片。",
        "recent": "最近查询（仅本次会话内，不写文件；按当前{mode}模式重查）",
        "mode_word_ro": "只读", "mode_word_w": "可写",
        "footer": ("研究优先级仅表示「值得进一步研究的程度」，不是投资建议。"
                   "AI 判断为暂定、非人工确认。缺失字段为待补录，不代表公司差。"),
        # 结果页 section 标题（test 锁定中文默认）
        "sec_score": "评分", "sec_strengths": "关键优势", "sec_risks": "主要风险",
        "sec_priority": "研究优先级", "sec_financials": "核心财务数据",
        "sec_missing": "缺失字段", "sec_fullcard": "完整研究卡片",
        # metric label（test 锁定中文默认，逐字不可改）
        "m_rule": "规则总分", "m_machine": "机器财务分", "m_aidyn": "AI 动态分",
        "m_conf": "AI 置信度", "m_preview": "最终预览分（实验）",
        # 结果页文案
        "score_caption": "基于财务质量、估值、安全边际与成长性规则评分。",
        "aux_title": "辅助评分（非权威主分，仅供参考）",
        "v_not_generated": "未生成", "v_insufficient": "数据不足", "v_tbf": "待补录",
        "v_conf_insufficient": "0.0（数据不足）",
        "conf_cap_suffix": "（上限 0.50）",
        "safety_ai": "🤖 AI 动态分为**实验·AI 暂定，不参与正式评分与排序，不构成投资建议**；needs_human_review 恒为 true。",
        "safety_preview": "⚠ 最终预览分（实验）：当前 **AI 权重 = 0.0**，AI 动态分**不计入该分、不影响排序**；非正式总分。",
        "ai_rating_line": "**AI 评级**：{r}　|　需人工复核：是",
        "ai_reason_line": "AI 分析：{r}",
        "ai_not_generated_info": "AI 动态评分未生成（provider 异常 / 校验失败 / 内部异常时显示此项；不影响规则评分与展示）。",
        "machine_dims_title": "机器财务分项（满分 75）",
        "machine_incomplete": "◐ 部分机器财务分：部分关键字段缺失（如 ROIC）；缺失 = 待补录，非公司差。",
        "dim_quality": "生意质量 /30", "dim_growth": "成长稳定 /15",
        "dim_balance": "负债安全 /15", "dim_valuation": "估值合理 /15",
        "points_not_generated": "AI 动态评分未生成。",
        "points_empty": "暂无（数据不足或本次未生成相关条目）。",
        "evidence_prefix": "（依据：{m}）",
        "priority_safety": "⚠ 「研究优先级」只表示**值得花多少研究精力**；研究优先级高 ≠ 好公司，≠ 可买入；非买卖建议。",
        "pending_warning": ("数据不足（待补录）：缺失关键财务字段，暂不展示规则总分 / 机器财务分 / "
                            "AI 动态分 / 最终预览分等分数；补齐数据后可复评。缺失 = 待补录，非公司差。"),
        "data_status": "数据状态",
        "dq_partial": "🟠 部分待补录", "dq_full": "🟢 完整",
        "source_line": "来源：{s}（年度 5 年口径，可能非最新季报）",
        "fin_caption": "机器自动计算（年度 5 年口径）；空值表示「待补录」，非公司差。",
        "missing_label": "**待补录**：{m}",
        "missing_caption": "数据不足 = **待补录**，不代表公司差；补齐数据后可重新评估。",
        "missing_all_ok": "关键量化字段齐全。",
        "sev_pending": "严重程度：数据不足（待补录）——已整体降级、暂不展示分数。",
        "sev_incomplete": "严重程度：部分待补录——分数已出，但部分维度（如 ROIC）未计入。",
        "ai_missing_prefix": "AI 标注的缺失（同源于数据校验，仅回显不重判）：",
        "old_ai_title": "AI 初判详情（持久化初判，与本次动态评分不同）",
        "old_ai_caption": ("ℹ️ 持久化旧版初判（仅可写模式由 generate_ai_for 写入），与上方「AI 动态分（本次实验）」"
                           "**不是同一来源**；均为 **AI 暂定·非人工确认**，护城河/管理层以**人工复核**为准，AI 永不权威。"),
        "old_ai_moat": "护城河（AI暂定）/10", "old_ai_mgmt": "管理层（AI暂定）/10", "old_ai_conf": "置信度",
        "old_ai_judge": "**AI 判断**：{r}", "old_ai_evidence": "**待补证据**：{r}",
        "old_ai_none": "未生成（数据不足时不生成 AI 初判）。",
        "fullcard_expander": "展开完整研究卡片（复制 / 导出用）",
        "fullcard_copy": "💡 点击代码框右上角复制图标可一键复制全文。",
        "fullcard_empty": "研究卡片正文暂未生成。",
        "download_card": "⬇ 下载研究卡片（Markdown）",
        "warnings_expander": "数据校验提示（{n} 条）",
        "company_suffix": "{name} · {market}",
        "market_us": "美股 (US)", "market_cn": "A股 (CN)", "market_unknown": "未知",
    },
    "en": {
        "app_title": "Kv's Stock Cat",
        "nav_help": "Help", "nav_settings": "Settings",
        "ui_language": "UI language",
        "help_body": ("Kv's Stock Cat: a research aid combining Munger-style rule scoring and "
                      "experimental AI dynamic scoring. Research-priority ranking only — not buy/sell/hold "
                      "advice; missing fields are to-be-filled, not a sign of a bad company."),
        "mode_ro": "Current: read-only (default) 🔒 no fetch, no writes.",
        "mode_w": "Current: writable (WEB_WRITABLE=1) ✍️ may fetch and update local CSV.",
        "mode_switch_hint": " Enable writes: `WEB_WRITABLE=1 streamlit run web_app.py`.",
        "hero_hello": "Hello, User", "hero_sub": "This is Kv's Stock Cat",
        "input_placeholder": "Enter a ticker, e.g. AAPL / 600519.SH",
        "btn_analyze": "Analyze",
        "examples_hint": "Try these locally available examples:",
        "loading": "Cat is analyzing {t}…",
        "input_empty": "Please enter a ticker.",
        "err_title": "Cat couldn't find this ticker",
        "err_hint": "Check the format, or try AAPL / MSFT / 600519.SH",
        "nodata_title": "Not enough data yet",
        "nodata_l1": "Read-only mode (no fetch, no writes); no local data for this ticker, can't produce a reliable score.",
        "nodata_l2": "Try another ticker, or generate data via writable mode / CLI:",
        "err_generic": "Couldn't generate the research card.",
        "recent": "Recent (session only, no file writes; re-queried in current {mode} mode)",
        "mode_word_ro": "read-only", "mode_word_w": "writable",
        "footer": ("Research priority only reflects how much further study is warranted — not investment advice. "
                   "AI judgments are tentative and not human-confirmed. Missing fields are to-be-filled, "
                   "not a sign of a bad company."),
        "sec_score": "Score", "sec_strengths": "Key strengths", "sec_risks": "Key risks",
        "sec_priority": "Research priority", "sec_financials": "Core financials",
        "sec_missing": "Missing fields", "sec_fullcard": "Full research card",
        "m_rule": "Rule score", "m_machine": "Machine score", "m_aidyn": "AI dynamic",
        "m_conf": "AI confidence", "m_preview": "Final preview (exp.)",
        "score_caption": "Rule scoring of quality, valuation, safety margin and growth.",
        "aux_title": "Auxiliary scores (not the authoritative main score)",
        "v_not_generated": "Not generated", "v_insufficient": "Insufficient data", "v_tbf": "to-be-filled",
        "v_conf_insufficient": "0.0 (insufficient)",
        "conf_cap_suffix": " (cap 0.50)",
        "safety_ai": "🤖 The AI dynamic score is **experimental · tentative; it does NOT enter the official score "
                     "or ranking and is not investment advice**; needs_human_review is always true.",
        "safety_preview": "⚠ Final preview (experimental): **AI weight = 0.0**; the AI dynamic score is **not "
                          "counted and does not affect ranking**; not an official total.",
        "ai_rating_line": "**AI rating**: {r}　|　Needs human review: yes",
        "ai_reason_line": "AI note: {r}",
        "ai_not_generated_info": "AI dynamic score not generated (shown on provider error / validation failure / "
                                 "internal error; does not affect the rule score or display).",
        "machine_dims_title": "Machine sub-scores (out of 75)",
        "machine_incomplete": "◐ Partial machine score: some key fields missing (e.g. ROIC); missing = to-be-filled, "
                              "not a bad company.",
        "dim_quality": "Quality /30", "dim_growth": "Growth /15",
        "dim_balance": "Balance sheet /15", "dim_valuation": "Valuation /15",
        "points_not_generated": "AI dynamic score not generated.",
        "points_empty": "None (insufficient data or no items generated this run).",
        "evidence_prefix": " (from: {m})",
        "priority_safety": "⚠ 'Research priority' only reflects how much further study is warranted; high priority "
                           "≠ good company, ≠ buy; not trading advice.",
        "pending_warning": ("Insufficient data (to-be-filled): key financial fields are missing, so the rule score / "
                            "machine score / AI dynamic / final preview are not shown; re-evaluate after backfill. "
                            "Missing = to-be-filled, not a bad company."),
        "data_status": "Data status",
        "dq_partial": "🟠 Partially to-be-filled", "dq_full": "🟢 Complete",
        "source_line": "Source: {s} (5-year annual basis, may not be the latest quarter)",
        "fin_caption": "Machine-computed (5-year annual basis); blank = to-be-filled, not a bad company.",
        "missing_label": "**To-be-filled**: {m}",
        "missing_caption": "Insufficient data = **to-be-filled**, not a bad company; re-evaluate after backfill.",
        "missing_all_ok": "Key quantitative fields are complete.",
        "sev_pending": "Severity: insufficient data (to-be-filled) — fully downgraded, scores hidden.",
        "sev_incomplete": "Severity: partially to-be-filled — scores shown, but some dimensions (e.g. ROIC) excluded.",
        "ai_missing_prefix": "AI-flagged missing (from data validation, echoed only): ",
        "old_ai_title": "AI prelim detail (persisted prelim, different from this dynamic score)",
        "old_ai_caption": ("ℹ️ Persisted legacy prelim (written by generate_ai_for in writable mode); a different "
                           "source from the 'AI dynamic' above. Both are **tentative · not human-confirmed**; "
                           "moat/management defer to human review. AI is never authoritative."),
        "old_ai_moat": "Moat (AI prelim)/10", "old_ai_mgmt": "Mgmt (AI prelim)/10", "old_ai_conf": "Confidence",
        "old_ai_judge": "**AI note**: {r}", "old_ai_evidence": "**Evidence needed**: {r}",
        "old_ai_none": "Not generated (no AI prelim when data is insufficient).",
        "fullcard_expander": "Expand full research card (copy / export)",
        "fullcard_copy": "💡 Click the copy icon at the top-right of the code box to copy all.",
        "fullcard_empty": "Research card body not generated yet.",
        "download_card": "⬇ Download research card (Markdown)",
        "warnings_expander": "Data validation notes ({n})",
        "company_suffix": "{name} · {market}",
        "market_us": "US", "market_cn": "A-share (CN)", "market_unknown": "Unknown",
    },
}


def _lang():
    try:
        v = st.session_state.get("lang", "zh")
    except Exception:
        v = "zh"
    return v if v in I18N else "zh"


def _t(key, lang=None):
    """固定 UI 文案翻译；未知 key 安全 fallback（zh→key 本身），不崩溃。"""
    lang = lang if lang in I18N else (lang or _lang())
    if lang not in I18N:
        lang = "zh"
    d = I18N.get(lang, I18N["zh"])
    if key in d:
        return d[key]
    return I18N["zh"].get(key, key)


# ── 全局 Aura Logic 主题 + 自适应深色模式（scoped CSS；渐进增强，失效仍可用）──────────
_CSS = f"""
<style>
:root {{
  /* Pixel-match token：取自 移动端7/kv_1 + 桌面端6/kv_3 的 code.html 真值（浅色） */
  --bg:#FFFFFF; --surface:#FFFFFF; --surface-2:#F5F3F3;
  --ink:#111111; --muted:#6B7280; --line:#E5E5EA; --line-strong:#D1D1D6;
  --accent:#6E56CF;
  --input-bg:#FFFFFF; --input-border:#E5E5EA; --input-ph:#9CA3AF;
  --chip-bg:#FBF9F9; --chip-border:#E3E2E2; --chip-text:#444748; --button-bg:#FFFFFF;
  --warning-bg:#FBF9F4; --warning-line:#EEEBE3;
  /* 兼容别名：旧规则仍引用这些名字；用 var() 跟随上面取值，深浅自动切换 */
  --card:var(--surface); --chip:var(--chip-bg); --surface2:var(--surface-2);
  --line-hover:var(--line-strong); --warm-bg:var(--warning-bg); --warm-line:var(--warning-line); --warm:#E8A33D;
}}
/* ── 自适应深色模式：示例为浅色（html class="light"），深色无 1:1 参考 → 同结构派生取值 ── */
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:#0F0F10; --surface:#171719; --surface-2:#1D1D20;
    --ink:#F4F4F5; --muted:#A1A1AA; --line:#2A2A2E; --line-strong:#3A3A42; --accent:#8B7CF6;
    --input-bg:#171719; --input-border:#2A2A2E; --input-ph:#71717A;
    --chip-bg:#1D1D20; --chip-border:#2A2A2E; --chip-text:#A1A1AA; --button-bg:#171719;
    --warning-bg:#241C12; --warning-line:#5A4525;
  }}
  html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{ background:var(--bg) !important; }}
  .stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown span,
  [data-testid="stMetricValue"], [data-testid="stMetricLabel"] p,
  h1, h2, h3, h4, label, td, th, .stCode, code {{ color:var(--ink) !important; }}
  div[data-testid="stCaptionContainer"] p {{ color:var(--muted) !important; }}
  div[data-testid="stVerticalBlockBorderWrapper"] {{ background:var(--card) !important; }}
  input, textarea {{ color:var(--ink) !important; }}
  table, .stTable {{ background:var(--card) !important; }}
}}
html, body, [data-testid="stAppViewContainer"] {{ background:var(--bg); }}
.block-container {{ max-width: 1180px; padding-top: 2.0rem; padding-bottom: 4rem; }}
html, body, [class*="css"], .stMarkdown, .stMetric, button, input {{
  font-family: 'Inter','SF Pro Display','SF Pro Text','PingFang SC','Noto Sans SC',system-ui,-apple-system,sans-serif;
}}
#MainMenu, header[data-testid="stHeader"], footer {{ visibility: hidden; height: 0; }}
/* 隐藏 Streamlit 自动给 markdown/heading 生成的锚点链条图标：只隐藏 anchor icon 容器，不影响标题文字 */
[data-testid="stHeaderActionElements"] {{ display: none !important; }}
div[data-testid="stVerticalBlockBorderWrapper"] {{
  border-radius: 16px !important; border-color: var(--line) !important;
  box-shadow: 0 1px 2px rgba(0,0,0,0.03);
}}
.st-key-mg-score-hero {{ border-width: 1.5px !important; }}
.st-key-mg-score-hero div[data-testid="stMetricValue"] {{
  font-size: 3.0rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.05;
}}
.st-key-mg-score-hero label[data-testid="stMetricLabel"] p {{
  font-size: 11px; letter-spacing: 0.05em; color: var(--muted); text-transform: uppercase;
}}
.st-key-mg-aux div[data-testid="stMetricValue"] {{ font-size: 1.15rem; font-weight: 600; }}
div[data-testid="stCaptionContainer"] p {{ color: var(--muted); }}
/* ===== 命令栏 = 单条胶囊（对齐 移动端7/kv_1）：放大镜 + 输入 + 小猫键 同处一条 pill ===== */
.st-key-mg-cmdbar [data-testid="stHorizontalBlock"] {{
  flex-wrap: nowrap !important; align-items: center !important; gap: 6px !important;
  background: var(--input-bg) !important; border: 1px solid var(--input-border) !important;
  border-radius: 9999px !important; box-shadow: 0 2px 8px rgba(0,0,0,0.04) !important;
  padding: 0 6px 0 14px !important; min-height: 50px !important;
}}
.st-key-mg-cmdbar [data-testid="stHorizontalBlock"]:focus-within {{
  border-color: var(--line-strong) !important; box-shadow: 0 4px 16px rgba(0,0,0,0.06) !important;
}}
/* 前置放大镜（#AEAEB2，对齐 kv_1 search 图标）：内联 SVG，作为 row 的第一个 flex 项 */
.st-key-mg-cmdbar [data-testid="stHorizontalBlock"]::before {{
  content: ""; flex: 0 0 20px; width: 20px; height: 20px; margin-right: 2px;
  background: no-repeat center / 20px 20px url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='%23AEAEB2' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cline x1='21' y1='21' x2='16.65' y2='16.65'/%3E%3C/svg%3E");
}}
.st-key-mg-cmdbar [data-testid="stForm"] {{ border: none !important; padding: 0 !important; }}
/* 输入框去边去底融入 pill：必须同时清掉 baseui Root（data-baseweb=input / stTextInputRootElement，
   Streamlit 在此画 1px 边 + 圆角 + min-height）和其内 InputContainer（> div），否则出现「胶囊套胶囊」。 */
.st-key-mg-cmdbar div[data-baseweb="input"],
.st-key-mg-cmdbar [data-testid="stTextInputRootElement"],
.st-key-mg-cmdbar div[data-baseweb="input"] > div {{
  border: none !important; background: transparent !important; box-shadow: none !important;
  border-radius: 0 !important; min-height: 0 !important; height: auto !important;
}}
.st-key-mg-cmdbar input {{ color: var(--ink) !important; -webkit-text-fill-color: var(--ink) !important; caret-color: var(--ink) !important; font-size: 14px !important; }}
.st-key-mg-cmdbar input::placeholder {{ color: var(--input-ph) !important; -webkit-text-fill-color: var(--input-ph) !important; opacity: 1 !important; }}
/* 小猫提交键：pill 内的圆形描边键（对齐 kv_1：白底 + #E5E5EA 边 + 圆 + 28px 猫身），扁平 */
.st-key-mg-catbtn button {{
  border-radius: 9999px !important; border: 1px solid var(--input-border) !important;
  background-color: var(--button-bg) !important;
  background-image: url("{_CAT_DATA_URI}") !important;
  background-repeat: no-repeat !important; background-position: center !important;
  background-size: 28px 28px !important;
  width: 40px !important; height: 40px !important; min-height: 40px !important;
  padding: 0 !important; margin: 0 auto !important; box-shadow: none !important; color: transparent !important;
}}
.st-key-mg-catbtn button:hover {{ border-color: var(--line-strong) !important; }}
/* 示例 chips（对齐 kv_3/kv_4）：pill，--chip-bg 底 + --chip-border 边 + --muted 字 12px；hover 提亮 */
.st-key-mg-chips button, .st-key-mg-hist button {{
  border-radius: 9999px !important; border: 1px solid var(--chip-border) !important;
  background: var(--chip-bg) !important; color: var(--chip-text) !important;
  font-size: 12px !important; font-weight: 500 !important;
}}
.st-key-mg-chips button:hover, .st-key-mg-hist button:hover {{
  border-color: var(--line-strong) !important; color: var(--ink) !important;
}}
/* 缺失/待补录卡：提高特异性盖过通用卡片规则（含深色 background:var(--card)），保证暖色边/底两色都生效 */
div[data-testid="stVerticalBlockBorderWrapper"].st-key-mg-missing {{ border-color:var(--warm-line) !important; background:var(--warm-bg) !important; }}
/* 免责声明页脚：底部居中、低对比；与内容保持固定间距（不用 5vh，避免高屏拉太长）+ iOS 安全区 */
.st-key-mg-footer {{ margin-top: 40px; padding-bottom: max(24px, env(safe-area-inset-bottom, 0px)); }}
.st-key-mg-footer div[data-testid="stCaptionContainer"] p {{ text-align: center; }}

/* ===== Responsive 对齐：移动端→「移动端7」 / 桌面端→「桌面端6」 ===== */
/* Header：单行紧凑；说明/设置为右侧小按钮（移动端不撑成两条全宽按钮） */
.st-key-mg-header [data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap !important; align-items: center !important; gap: 8px !important; }}
.st-key-mg-header [data-testid="stColumn"]:last-child [data-testid="stHorizontalBlock"] {{ justify-content: flex-end !important; gap: 6px !important; }}
.st-key-mg-header [data-testid="stColumn"]:last-child [data-testid="stColumn"] {{ flex: 0 0 auto !important; width: auto !important; min-width: 0 !important; }}
/* 说明/设置：弱化文字键（透明底/透明边，token 色），与命令区同一套语言，不再是默认灰按钮 */
.st-key-mg-header button {{ min-height: 32px !important; padding: 2px 10px !important; font-size: 13px !important; width: auto !important;
  background: transparent !important; border: 1px solid transparent !important; box-shadow: none !important; }}
.st-key-mg-header button p {{ color: var(--muted) !important; margin-bottom: 0 !important; }}
.st-key-mg-header button:hover {{ border-color: var(--line) !important; background: var(--surface2) !important; }}
.st-key-mg-header button:hover p {{ color: var(--ink) !important; }}
/* 说明/设置是 st.popover 触发键：用更高特异性覆盖 baseweb 默认底/边，确保也是扁平弱化（与命令区同族，深浅一致） */
.st-key-mg-header [data-testid="stPopover"] button {{ background: transparent !important; border: 1px solid transparent !important; box-shadow: none !important; }}
.st-key-mg-header [data-testid="stPopover"] button:hover {{ border-color: var(--line) !important; background: var(--surface2) !important; }}
.st-key-mg-header p {{ margin-bottom: 0 !important; }}
/* 覆盖 Streamlit 窄屏下给列设的 min-width:100%（否则 nowrap 会溢出），让 Header 列按内容收缩 */
.st-key-mg-header [data-testid="stColumn"] {{ min-width: 0 !important; }}
/* 产品名（左）：对齐 桌面端6/kv_3 的 20px bold（用 18px 适配 Streamlit 默认行高） */
.st-key-mg-header [data-testid="stColumn"]:first-child p {{ font-size: 18px !important; font-weight: 700 !important; color: var(--ink) !important; margin-bottom: 0 !important; }}

/* Hero：桌面对齐 kv_3（48px bold 居中、限宽 600）；移动对齐 kv_1（32px semibold 左对齐，见 @media） */
.st-key-mg-hero {{ text-align: center; max-width: 600px; margin: 6vh auto 0 auto; }}
.st-key-mg-hero h1 {{ font-size: 3rem !important; font-weight: 700 !important; letter-spacing: -0.02em !important; line-height: 1.1 !important; margin: 0 !important; padding: 0 !important; }}
.st-key-mg-hero div[data-testid="stCaptionContainer"] {{ margin-top: 8px !important; }}
.st-key-mg-hero div[data-testid="stCaptionContainer"] p {{ font-size: 14px !important; }}

/* 命令栏容器：桌面居中限宽 600（对齐 kv_3）；输入列自适应、小猫列固定 40px（pill 内布局见上方 row 规则） */
.st-key-mg-cmdbar {{ max-width: 600px; margin: 28px auto 0 auto; }}
.st-key-mg-cmdbar [data-testid="stColumn"]:first-child {{ flex: 1 1 auto !important; width: auto !important; min-width: 0 !important; }}
.st-key-mg-cmdbar [data-testid="stColumn"]:last-child {{ flex: 0 0 40px !important; width: 40px !important; min-width: 40px !important; }}

/* 示例 chips：横向小 pill（对齐 kv_3，高 ~32px）；桌面居中限宽 600、移动左对齐，过窄横向滚动 */
.st-key-mg-chips {{ max-width: 600px; margin: 16px auto 0 auto; }}
.st-key-mg-chips [data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap !important; gap: 8px !important; justify-content: center !important; overflow-x: auto !important; }}
.st-key-mg-chips [data-testid="stColumn"] {{ flex: 0 0 auto !important; width: auto !important; min-width: 0 !important; }}
.st-key-mg-chips button {{ min-height: 32px !important; padding: 4px 16px !important; width: auto !important; }}

/* 核心财务：无表格线的 2 列「标签/数值」网格；移动端也保持 2 列（对齐 kv_4 / kv_6） */
.st-key-mg-fin [data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap !important; gap: 12px !important; }}
.st-key-mg-fin [data-testid="stColumn"] {{ flex: 1 1 0 !important; min-width: 0 !important; }}
.st-key-mg-fin div[data-testid="stCaptionContainer"] p {{ margin-bottom: 0 !important; font-size: 11px !important; letter-spacing: 0.03em; }}
.st-key-mg-fin .stMarkdown p {{ margin-top: 0 !important; font-weight: 600 !important; }}

/* 首页底部装饰猫：桌面克制居中、不抢中心；移动端隐藏（设计稿移动首页无底部大猫） */
.st-key-mg-hero-cat {{ max-width: 600px; margin-left: auto; margin-right: auto; }}

/* 桌面端 (≥681px) 精修：对齐 桌面端6/kv_3 —— pill 56px、放大镜 24px/#747878、副标题 18px/#444748 */
@media (min-width: 681px) {{
  .st-key-mg-cmdbar [data-testid="stHorizontalBlock"] {{ min-height: 56px !important; padding: 0 8px 0 16px !important; }}
  .st-key-mg-cmdbar [data-testid="stHorizontalBlock"]::before {{
    flex: 0 0 24px; width: 24px; height: 24px;
    background: no-repeat center / 24px 24px url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='%23747878' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cline x1='21' y1='21' x2='16.65' y2='16.65'/%3E%3C/svg%3E");
  }}
  .st-key-mg-hero div[data-testid="stCaptionContainer"] p {{ font-size: 18px !important; color: #444748 !important; }}
}}

@media (max-width: 680px) {{
  /* 移动端：左右 32px 边距（对齐 移动端7/kv_1 <main> px-[32px]）；命令栏/header/chips 单行由 base 规则覆盖全宽 */
  .block-container {{ padding-left: 2rem; padding-right: 2rem; padding-top: 1.0rem; }}
  .st-key-mg-score-hero div[data-testid="stMetricValue"] {{ font-size: 2.4rem; }}
  /* Hero 对齐 移动端7/kv_1：32px semibold、左对齐、占满宽、紧贴顶部 */
  .st-key-mg-hero {{ text-align: left; max-width: none; margin: 16px 0 0 0; }}
  .st-key-mg-hero h1 {{ font-size: 2rem !important; font-weight: 600 !important; }}
  /* 命令栏：移动端输入 13px、小猫键 38px（对齐 kv_1）；占满左对齐 */
  .st-key-mg-cmdbar {{ max-width: none; margin: 28px 0 0 0; }}
  .st-key-mg-cmdbar input {{ font-size: 13px !important; }}
  .st-key-mg-catbtn button {{ width: 38px !important; height: 38px !important; min-height: 38px !important; }}
  .st-key-mg-cmdbar [data-testid="stColumn"]:last-child {{ flex: 0 0 38px !important; width: 38px !important; min-width: 38px !important; }}
  .st-key-mg-chips {{ max-width: none; margin: 16px 0 0 0; }}
  .st-key-mg-chips [data-testid="stHorizontalBlock"] {{ justify-content: flex-start !important; }}
  /* 免责声明：固定间距 + safe-area（不贴底、不拉太长） */
  .st-key-mg-footer {{ margin-top: 32px; }}
  /* 移动端隐藏底部装饰猫（示例移动首页无底部大猫） */
  .st-key-mg-hero-cat {{ display: none !important; }}
}}
</style>
"""


def _inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


# 错误态命令栏（无法识别 ticker）：暖边 + 浅暖底；并强制深色输入文字/光标 + 可见 placeholder。
# 修复「深色模式下浅暖底 + 继承的浅色文字 → 输入几乎看不见」。Safari 需 -webkit-text-fill-color。
# 仅在 cmdbar_error=True 时注入：不影响浅色模式、普通深色输入框、数据不足态（其 cmdbar_error=False）。
_CMDBAR_ERROR_CSS = (
    "<style>"
    # 单胶囊模型：可见的暖边 + 浅暖底画在 pill（stHorizontalBlock）本身，输入框已透明融入
    '.st-key-mg-cmdbar [data-testid="stHorizontalBlock"]{'
    "border:1px solid #F0C38A !important;background:#FFF7EC !important;}"
    ".st-key-mg-cmdbar input{color:#111111 !important;"
    "-webkit-text-fill-color:#111111 !important;caret-color:#111111 !important;}"
    ".st-key-mg-cmdbar input::placeholder{color:#7A6A5A !important;"
    "-webkit-text-fill-color:#7A6A5A !important;opacity:1 !important;}"
    "</style>"
)


def _cat_img(px=22):
    # 透明 PNG：宽固定、高自适应、object-fit:contain，保比例、无白底方块、无背景
    return (f'<img src="{_CAT_DATA_URI}" alt="{_t("app_title")}" '
            f'style="width:{px}px;height:auto;object-fit:contain;'
            f'vertical-align:middle;background:transparent;"/>')


# ── 展示小工具 ────────────────────────────────────────────────
def _fmt(value, suffix="", pct=False):
    """空值统一显示为「待补录」；否则展示原值（可带 % 或单位）。"""
    s = str(value).strip()
    if s in ("", "未填写", "None", "nan", "（无）"):
        return _t("v_tbf")
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
    """统一卡片容器（白底/深色卡 + 轻边框 + 16px 圆角）。"""
    return st.container(border=True, key=key)


# ── 结果渲染（移动单栏堆叠 / 桌面两栏：左主栏 + 右辅栏）─────────────────
def render_result(res):
    result    = res["result"]
    canonical = res["canonical"]
    market    = str(result.get("market", "")).upper()
    market_label = {"US": _t("market_us"), "CN": _t("market_cn")}.get(market, _t("market_unknown"))
    name = result.get("long_name") or result.get("name") or canonical
    pending    = str(result.get("final_decision", "")) == "数据不足（待补录）"
    incomplete = str(result.get("data_status", "")) == "待补录"

    aidyn = result.get("ai_dynamic")
    rb    = _num(result, "total_score")
    mt    = _machine_total(result)

    # 标的头部（非 ### 章节；ticker/公司名为业务数据，不翻译）
    st.markdown(f"## {result.get('ticker', canonical)}　<span style='color:var(--muted);font-weight:500;font-size:15px'>"
                f"{_t('company_suffix').format(name=name, market=market_label)}</span>",
                unsafe_allow_html=True)

    main, side = st.columns([1.55, 1], gap="large")

    # ===== 左主栏：评分主角 + 优势/风险 + 研究优先级 =====
    with main:
        st.markdown("### " + _t("sec_score"))
        if pending:
            st.warning(_t("pending_warning"))
        else:
            with _card(key="mg-score-hero"):
                st.metric(_t("m_rule"), f"{rb:.0f} / 100")
                st.caption(_t("score_caption"))
            with _card(key="mg-aux"):
                st.caption(_t("aux_title"))
                a1, a2 = st.columns(2)
                a1.metric(_t("m_machine"), f"{mt:.0f} / 75")
                if not aidyn:
                    a2.metric(_t("m_aidyn"), _t("v_not_generated"))
                else:
                    _as = aidyn.get("ai_score")
                    a2.metric(_t("m_aidyn"), _t("v_insufficient") if _as is None else f"{_as} / 100")
                a3, a4 = st.columns(2)
                if not aidyn:
                    a3.metric(_t("m_conf"), "—")
                else:
                    _as = aidyn.get("ai_score")
                    a3.metric(_t("m_conf"),
                              _t("v_conf_insufficient") if _as is None
                              else f"{_fmt(aidyn.get('confidence'))}{_t('conf_cap_suffix')}")
                fsp = result.get("final_score_preview")
                try:
                    fsp_s = f"{float(fsp):.0f} / 100"
                except (TypeError, ValueError):
                    fsp_s = f"{rb:.0f} / 100"
                a4.metric(_t("m_preview"), fsp_s)
                st.caption(_t("safety_ai"))
                st.caption(_t("safety_preview"))
                if aidyn:
                    st.markdown(_t("ai_rating_line").format(r=_fmt(aidyn.get("ai_rating"))))
                    st.caption(_t("ai_reason_line").format(r=_fmt(aidyn.get("ai_reasoning"))))
                else:
                    st.info(_t("ai_not_generated_info"))
                with st.expander(_t("machine_dims_title")):
                    if incomplete:
                        st.caption(_t("machine_incomplete"))
                    d1, d2, d3, d4 = st.columns(4)
                    d1.metric(_t("dim_quality"), f"{_num(result, 'quality_score'):.0f}")
                    d2.metric(_t("dim_growth"), f"{_num(result, 'growth_score'):.0f}")
                    d3.metric(_t("dim_balance"), f"{_num(result, 'balance_sheet_score'):.0f}")
                    d4.metric(_t("dim_valuation"), f"{_num(result, 'valuation_score'):.0f}")

        # 关键优势 / 主要风险（三态兜底；条目 point/evidence 为业务数据，不翻译）
        def _points(title_key, key):
            st.markdown("### " + _t(title_key))
            with _card():
                if not aidyn:
                    st.caption(_t("points_not_generated"))
                    return
                items = aidyn.get(key) or []
                if not items:
                    st.caption(_t("points_empty"))
                    return
                for it in items:
                    em = str(it.get("evidence_metric", "")).strip()
                    tail = _t("evidence_prefix").format(m=em) if em else ""
                    st.markdown(f"- {_fmt(it.get('point'))}{tail}")

        _points("sec_strengths", "key_strengths")
        _points("sec_risks", "key_risks")

        prio, why = res["research_priority"]
        st.markdown("### " + _t("sec_priority"))
        with _card():
            st.markdown(f"#### {prio}")
            st.caption(_t("priority_safety"))
            st.write(why)

    # ===== 右辅栏：数据状态 + 核心财务 + 缺失字段 + 折叠详情 =====
    with side:
        with _card():
            _dq = _t("dq_partial") if (pending or incomplete) else _t("dq_full")
            st.markdown(f"**{_t('data_status')}**")
            st.caption(f"{_dq}　·　{_fmt(result.get('data_date'))}")
            st.caption(_t("source_line").format(s=_fmt(result.get("data_source"))))

        st.markdown("### " + _t("sec_financials"))
        with _card(key="mg-fin"):
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
            # 对齐设计稿：无表格线的 2 列「标签/数值」网格（标签弱化、数值加粗）；移动端仍保持 2 列
            for _i in range(0, len(rows), 2):
                _fc = st.columns(2)
                for _j, (_lbl, _val) in enumerate(rows[_i:_i + 2]):
                    with _fc[_j]:
                        st.caption(_lbl)
                        st.markdown(f"**{_val}**")
            st.caption(_t("fin_caption"))

        st.markdown("### " + _t("sec_missing"))
        mf = str(result.get("missing_fields", "") or "").strip()
        with _card(key=("mg-missing" if (mf and mf != "（无）") else None)):
            if mf and mf != "（无）":
                st.markdown(_t("missing_label").format(m=mf))
                st.caption(_t("missing_caption"))
            else:
                st.success(_t("missing_all_ok"))
            if pending:
                st.caption(_t("sev_pending"))
            elif incomplete:
                st.caption(_t("sev_incomplete"))
            if aidyn:
                mdw = aidyn.get("missing_data_warnings") or []
                if mdw:
                    st.caption(_t("ai_missing_prefix") + "、".join(str(x) for x in mdw))

        with st.expander(_t("old_ai_title")):
            st.caption(_t("old_ai_caption"))
            if str(result.get("ai_model", "")).strip():
                b1, b2, b3 = st.columns(3)
                b1.metric(_t("old_ai_moat"), _fmt(result.get("ai_moat_score")))
                b2.metric(_t("old_ai_mgmt"), _fmt(result.get("ai_management_score")))
                b3.metric(_t("old_ai_conf"), _fmt(result.get("ai_confidence")))
                st.markdown(_t("old_ai_judge").format(r=_fmt(result.get("ai_reason"))))
                st.markdown(_t("old_ai_evidence").format(r=_fmt(result.get("ai_evidence_needed"))))
            else:
                st.write(_t("old_ai_none"))

        st.markdown("### " + _t("sec_fullcard"))
        card = (res.get("card_text") or "").strip()
        with st.expander(_t("fullcard_expander"), expanded=False):
            if card:
                st.code(card, language="text")
                st.caption(_t("fullcard_copy"))
            else:
                st.caption(_t("fullcard_empty"))
        st.download_button(_t("download_card"), data=card or "（暂无内容）",
                           file_name=f"{canonical}_card.md", mime="text/markdown",
                           use_container_width=True)

        if res.get("warnings"):
            with st.expander(_t("warnings_expander").format(n=len(res["warnings"]))):
                for w in res["warnings"]:
                    st.write("• " + str(w))


# ── 命令栏（搜索框 + 小猫提交键）+ 示例 chips（共用）─────────────────
def _command_bar():
    with st.container(key="mg-cmdbar"):
        with st.form("query_form", clear_on_submit=False, border=False):
            ci, cb = st.columns([8, 1], vertical_alignment="center")
            tk = ci.text_input(_t("input_placeholder"), placeholder=_t("input_placeholder"),
                               label_visibility="collapsed")
            with cb.container(key="mg-catbtn"):
                sub = st.form_submit_button(_t("btn_analyze"), use_container_width=True)
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
            if cols[i].button(t, key=f"{key_prefix}_{i}_{t}", use_container_width=False):
                st.session_state["_requery"] = t
                st.rerun()


# ── 页面主体 ──────────────────────────────────────────────────
_inject_css()

# 顶部 Header：左产品名 + 右 说明/设置（含语言切换）；单行紧凑（见 .st-key-mg-header）
with st.container(key="mg-header"):
    _h_left, _h_right = st.columns([2, 1], vertical_alignment="center")
    _h_left.markdown(f"**{_t('app_title')}**")
    with _h_right:
        _n1, _n2 = st.columns(2)
        with _n1.popover(_t("nav_help"), use_container_width=False):
            st.caption(_t("help_body"))
        with _n2.popover(_t("nav_settings"), use_container_width=False):
            st.caption((_t("mode_ro") if READONLY else _t("mode_w")) + _t("mode_switch_hint"))
            _cur = _lang()
            _choice = st.radio(_t("ui_language"), ["中文", "English"],
                               index=0 if _cur == "zh" else 1, key="lang_radio")
            _new = {"中文": "zh", "English": "en"}.get(_choice, _cur)
            if _new != _cur:
                st.session_state["lang"] = _new
                st.rerun()

requery = st.session_state.pop("_requery", None)

# 首页 Hero：始终渲染在 mg-hero 容器；本会话提交过查询后由页尾全局 CSS 隐藏（同屏不残留）
with st.container(key="mg-hero"):
    st.markdown("# " + _t("hero_hello"))
    st.caption(_t("hero_sub"))

ticker, submitted = _command_bar()

run_ticker = None
if submitted:
    tv = (ticker or "").strip()
    if tv:
        run_ticker = tv
    else:
        st.error(_t("input_empty"))
if requery:
    run_ticker = requery   # 点击示例/最近查询：仍遵守当前 READONLY，绝不绕过

# 标记「本会话已提交过查询」——一旦提交，首页 Hero 不再出现
if run_ticker:
    st.session_state["has_submitted_query"] = True
_submitted_before = bool(st.session_state.get("has_submitted_query"))

# 首页空状态（仅从未提交过查询时）：示例 chips + 弱化大猫
if not _submitted_before:
    st.caption(_t("examples_hint"))
    _chips(_local_examples(), "ex")
    # 桌面端首页弱装饰猫：小尺寸、低对比、居中、不抢视觉中心；
    # 移动端由 CSS 隐藏（设计稿移动端首页无底部大猫）。搜索键里的小猫始终保留。
    with st.container(key="mg-hero-cat"):
        st.markdown(
            f"<div style='opacity:.4;margin-top:4vh;text-align:center'>{_cat_img(52)}</div>",
            unsafe_allow_html=True)

_showed_result = False   # 仅「成功结果页」隐藏 Hero；首页/加载/错误/数据不足态都保留 Hero（对齐 kv_1/2/3/7）
if run_ticker:
    with st.spinner(_t("loading").format(t=run_ticker)):
        # READONLY 始终透传：只读模式下点击示例/最近查询也不会写盘
        res = research_service.run_research(run_ticker, readonly=READONLY)
    if not res.get("ok"):
        if res.get("canonical") is None:
            st.session_state["cmdbar_error"] = True
            st.markdown(f"{_cat_img(20)} **{_t('err_title')}**", unsafe_allow_html=True)
            st.caption(_t("err_hint"))
            _chips(["AAPL", "MSFT", "600519.SH"], "err")
        elif res.get("error") == "本地暂无数据，当前为只读模式":
            st.session_state["cmdbar_error"] = False
            with _card():
                st.markdown(f"{_cat_img(28)} **{_t('nodata_title')}**", unsafe_allow_html=True)
                st.caption(_t("nodata_l1"))
                st.caption(_t("nodata_l2"))
                st.code(f"WEB_WRITABLE=1 streamlit run web_app.py\npython main.py "
                        f"{res.get('canonical') or run_ticker}", language="bash")
            _chips(["AAPL", "MSFT", "600519.SH"], "nodata")
        else:
            st.session_state["cmdbar_error"] = False
            st.error(res.get("error") or _t("err_generic"))
    else:
        st.session_state["cmdbar_error"] = False
        _showed_result = True   # 成功结果页：隐藏 Hero，命令栏占满左对齐（见页尾）
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
    _mode = _t("mode_word_ro") if READONLY else _t("mode_word_w")
    st.caption(_t("recent").format(mode=_mode))
    with st.container(key="mg-hist"):
        _PER_ROW = 4
        for _start in range(0, len(hist), _PER_ROW):
            _chunk = hist[_start:_start + _PER_ROW]
            cols = st.columns(_PER_ROW)
            for _j, h in enumerate(_chunk):
                if cols[_j].button(h, key=f"hist_{_start + _j}_{h}", use_container_width=True):
                    st.session_state["_requery"] = h
                    st.rerun()

# 同屏视觉修正：仅「成功结果页」隐藏首页 Hero + 命令栏占满左对齐；
# 错误态 / 数据不足态保留首页 Hero（对齐设计稿 kv_3 / kv_7）。
if _showed_result:
    st.markdown("<style>.st-key-mg-hero{display:none !important;}"
                ".st-key-mg-cmdbar{max-width:none !important;margin:6px 0 0 0 !important;}"
                "</style>", unsafe_allow_html=True)
if st.session_state.get("cmdbar_error"):
    st.markdown(_CMDBAR_ERROR_CSS, unsafe_allow_html=True)

with st.container(key="mg-footer"):
    st.caption(_t("footer"))
