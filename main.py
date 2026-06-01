# ============================================================
# main.py  —  芒格式选股评分器：主程序  v2.3.0-alpha2
#
# v2.0 新增：fetcher.py 自动抓取数据；压缩重复警告；
#            行业样本不足提示；ROE 虚高说明；候选原因字段。
#
# 用法：python main.py          → 完整运行
#       python main.py AAPL     → 单股深度分析
# ============================================================

import os, sys
from collections import defaultdict

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# v2.1.0：缺依赖时给清晰方案，而不是丢出原始 ModuleNotFoundError
try:
    import pandas as pd
except ImportError:
    print("✗ 缺少必需依赖：pandas")
    print("  解决：pip install -r requirements.txt")
    print("  （或 pip3 install pandas；Windows 可用 py -m pip install -r requirements.txt）")
    sys.exit(1)

from preflight          import run_preflight, print_report   # v2.1.0
import store                                                  # v2.2.0-alpha1
from ticker_resolver     import resolve, UNKNOWN              # v2.2.0-alpha1
import research_card                                          # v2.3.0-alpha1
import ai_analysis                                            # v2.3.0-alpha2
from scorer             import score_stock, generate_narrative
from validator          import (validate_data, get_final_decision, filter_fin_warning,
                                 missing_key_quant_fields, quant_labels)
from financial_analyzer import compute_all_metrics          # v1.8

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH    = os.path.join(BASE_DIR, "data", "stocks.csv")
ANNUAL_PATH   = os.path.join(BASE_DIR, "data", "annual_financials.csv")   # v1.8
OUTPUT_DIR    = os.path.join(BASE_DIR, "output")
OUTPUT_FULL   = os.path.join(OUTPUT_DIR, "munger_score_result.csv")
OUTPUT_CANDS  = os.path.join(OUTPUT_DIR, "research_candidates.csv")

# ── 旧版 CSV 缺列时的默认值 ──────────────────────────────────
_DEFAULTS = {
    "data_source": "未知", "confidence_score": "8",
    "circle_of_competence": "edge", "warning_note": "",
    "brand_score": "", "switching_cost_score": "", "network_effect_score": "",
    "scale_advantage_score": "", "pricing_power_score": "", "moat_durability_score": "",
    "roe_trend": "stable", "roic_trend": "stable",
    "margin_trend": "stable", "revenue_trend": "stable",
    "debt_reason": "",
    # v1.8
    "data_mode": "manual_fallback",
    "financial_data_years": "",
    "auto_calculated_metrics": "否",
    # v2.0
    "_fin_data_warning": "",
    "_fin_years_count":  "0",
    "_fin_source":       "",
    "_fin_updated_at":   "",
    "industry_size":     "1",
    # v2.2.0-alpha1：市场/币种/规范化代码/补录状态（向后兼容，旧 CSV 缺则补）
    "market":            "",
    "currency":          "",
    "canonical_ticker":  "",
    "review_status":     "",
    # v2.3.0-alpha1：基础信息 + AI 质化层占位（alpha1 全空，alpha2 启用）
    "long_name":         "",
    "sector":            "",
    "country":           "",
    "ai_moat_score":       "",
    "ai_management_score": "",
    "ai_risk_score":       "",
    "ai_risk_flags":       "",
    "ai_confidence":       "",
    "ai_reason":           "",
    "ai_evidence_needed":  "",
    "ai_model":            "",
    "ai_generated_at":     "",
    "needs_human_review":  "",
    # v2.4.0：A股/通用估值
    "pb":                  "",
    "market_cap":          "",
}

# ── v1.8：可被年度财务数据自动覆盖的字段（只有这些）──────────
# 不包含任何需要人工判断的字段（护城河/管理层/能力圈/风险/PE等）
_FINANCIAL_OVERRIDE_FIELDS = [
    "roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg",
    "debt_to_equity", "fcf_positive_years",
    "revenue_growth_5y_cagr", "eps_growth_5y_cagr",
    "roe_trend", "roic_trend", "margin_trend", "revenue_trend",
]


# ============================================================
# 辅助函数
# ============================================================

def _cjk_len(s):
    return sum(2 if ('一' <= c <= '鿿') or c in '★⚠✓' else 1 for c in str(s))

def _rpad(s, w):  return str(s) + ' ' * max(0, w - _cjk_len(str(s)))
def _lpad(s, w):  return ' ' * max(0, w - _cjk_len(str(s))) + str(s)

def _bar(score, max_score, width=20):
    filled = round(score / max_score * width) if max_score > 0 else 0
    return '█' * max(0, min(filled, width)) + '░' * (width - max(0, min(filled, width)))

def _trunc(s, max_w):
    s, w = str(s), 0
    for i, c in enumerate(s):
        w += 2 if '一' <= c <= '鿿' else 1
        if w > max_w - 1:
            return s[:i] + '…'
    return s

# ── v2.2.0-alpha2：待补录展示模式辅助 ────────────────────────
def _is_pending(result):
    """数据不足（待补录）→ 进入待补录展示模式，不输出公司质量结论。"""
    return (str(result.get("final_decision", "")) == "数据不足（待补录）"
            or str(result.get("data_status", "")) == "待补录")

def _market_label(market):
    return {"US": "美股", "CN": "A股"}.get(str(market).strip().upper(), "未知")

def _uf(val):
    """空字段统一显示为'未填写'，避免 '/10' 或空白。"""
    s = str(val).strip()
    return s if s else "未填写"


def _fmt_vs(val):
    return f"+{val:.1f}" if val > 0 else f"{val:.1f}"


def _compress_warnings(warning_str):
    """
    压缩重复的 warning 片段。
    例："使用近似ROIC; 使用近似ROIC; 使用近似ROIC; 使用近似ROIC"
    → "4年均使用近似ROIC"
    避免 warning_note 里看到一堆重复条目。
    """
    if not warning_str:
        return warning_str
    from collections import Counter
    parts  = [p.strip() for p in warning_str.split(";") if p.strip()]
    counts = Counter(parts)
    result = []
    for item, n in counts.items():
        result.append(f"{n}年均{item}" if n > 1 else item)
    return "; ".join(result)


def generate_candidate_reason(row):
    """
    为研究候选股生成入选理由，写入 research_candidates.csv 的
    candidate_reason 列，方便日后回顾"当时为什么选它"。
    """
    def _f(field, default=0.0):
        try: return float(row.get(field, default) or default)
        except: return default

    score = _f("total_score")
    fd    = str(row.get("final_decision", ""))

    prefix = (f"总分{score:.0f}达深入研究门槛(≥85)" if fd == "深入研究"
              else f"总分{score:.0f}进入观察池(70-84)")

    highlights = []
    if _f("quality_score")       >= 25: highlights.append("生意质量优秀")
    if _f("moat_score")          >= 16: highlights.append("护城河深厚")
    if _f("growth_score")        >= 12: highlights.append("成长稳健")
    if _f("balance_sheet_score") >= 13: highlights.append("财务安全")
    if _f("valuation_score")     >= 12: highlights.append("估值合理")

    return prefix + ("；" + "、".join(highlights) if highlights else "")


def _industry_rank_label(result):
    """
    返回行业排名展示文字。
    样本数 < 3 时，提示排名参考价值有限。
    """
    rank = result.get("industry_rank", 1)
    size = int(result.get("industry_size", 1))
    avg  = result.get("industry_avg_score", 0.0)
    vs   = result.get("score_vs_industry",  0.0)
    if size < 3:
        return (f"#{rank}（行业样本仅 {size} 只，排名参考价值有限）")
    return f"#{rank}（同行均分 {avg:.1f}，{_fmt_vs(vs)}）"


# ============================================================
# v1.8：年度财务数据合并
# ============================================================

def merge_annual_data(stocks, fin_metrics):
    """
    将 financial_analyzer 计算出的财务指标注入 stocks 行中。

    只覆盖量化财务字段（_FINANCIAL_OVERRIDE_FIELDS），
    不碰任何人工判断字段：
        护城河拆分分数、护城河理由、管理层评分/理由、
        能力圈、风险标记、信心分、负债说明、PE、FCF Yield 等。

    设计原则：
        年度历史数据 → 更客观、更准确的量化指标
        人工判断     → 仍然来自 stocks.csv，不可替代
    """
    for row in stocks:
        ticker = row.get("ticker", "").strip().upper()
        if ticker in fin_metrics:
            m = fin_metrics[ticker]
            for field in _FINANCIAL_OVERRIDE_FIELDS:
                if field in m:
                    row[field] = str(m[field])   # 保持 str 类型，与 CSV 一致
            row["data_mode"]               = "annual_financials"
            row["financial_data_years"]    = m.get("data_years", "")
            row["auto_calculated_metrics"] = "是"
            # v2.0：把 annual_financials 的元信息注入 row，供 validator 和展示使用
            row["_fin_data_warning"] = _compress_warnings(m.get("fin_data_warnings", ""))
            row["_fin_years_count"]  = str(m.get("years_count", 0))
            row["_fin_source"]       = m.get("source", "")
            row["_fin_updated_at"]   = m.get("updated_at", "")
        else:
            row.setdefault("data_mode",               "manual_fallback")
            row.setdefault("financial_data_years",    "")
            row.setdefault("auto_calculated_metrics", "否")
            row.setdefault("_fin_data_warning", "")
            row.setdefault("_fin_years_count",  "0")
            row.setdefault("_fin_source",       "")
            row.setdefault("_fin_updated_at",   "")
    return stocks


# ============================================================
# 行业对比（v1.5）
# ============================================================

def calc_industry_comparison(results):
    # v2.2.0-alpha2：数据不足（待补录）的股票不参与有效排名，
    # 否则它们的 0 分会污染同行均值、产生误导性排名。
    groups = defaultdict(list)
    for r in results:
        if _is_pending(r):
            # 占位值，确保下游列存在；展示层会显示"数据不足，暂不参与有效排名"
            r["industry_rank"]      = 0
            r["industry_size"]      = 0
            r["industry_avg_score"] = 0.0
            r["score_vs_industry"]  = 0.0
            continue
        groups[r["industry"]].append(r)
    for ind_stocks in groups.values():
        n   = len(ind_stocks)
        avg = sum(r["total_score"] for r in ind_stocks) / n
        for rank, r in enumerate(
                sorted(ind_stocks, key=lambda x: x["total_score"], reverse=True), start=1):
            r["industry_rank"]      = rank
            r["industry_size"]      = n       # v2.0：记录行业样本数
            r["industry_avg_score"] = round(avg, 1)
            r["score_vs_industry"]  = round(r["total_score"] - avg, 1)
    return results


# ============================================================
# 数据读写
# ============================================================

def load_stocks(filepath):
    if not os.path.exists(filepath):
        print(f"错误：找不到 {filepath}"); return []
    # v2.1.0：用 utf-8-sig 读取，兼容 add_stocks/fetcher 写入的 BOM，
    # 否则首列会被读成 "﻿ticker" 导致 ticker 整列丢失。
    df = pd.read_csv(filepath, encoding="utf-8-sig", dtype=str)
    for col, default in _DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    df = df.fillna("")
    stocks = df.to_dict(orient="records")
    # v2.2.0-alpha1：为缺失的 canonical/market/currency 用 resolver 回填
    # （机器可派生的基础信息，非人工判断；只读内存，不改写文件）
    for row in stocks:
        if not str(row.get("canonical_ticker", "")).strip():
            r = resolve(str(row.get("ticker", "")))
            if r.market != UNKNOWN:
                row["canonical_ticker"] = r.canonical
                if not str(row.get("market", "")).strip():
                    row["market"] = r.market
                if not str(row.get("currency", "")).strip():
                    row["currency"] = r.currency
    print(f"已读取 {len(stocks)} 只股票（{filepath}）")
    return stocks


def save_results(results, filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    cols = [
        "ticker", "name", "industry", "data_date",
        "total_score", "rating", "final_decision",
        # v2.1.0-alpha2：数据完整度（区分"数据缺失"与"公司差"）
        "data_status", "missing_fields",
        "quality_score", "moat_score", "growth_score",
        "balance_sheet_score", "valuation_score", "management_score", "risk_penalty",
        "brand_score", "switching_cost_score", "network_effect_score",
        "scale_advantage_score", "pricing_power_score", "moat_durability_score",
        "calculated_moat_score", "trend_score",
        "industry_rank", "industry_size", "industry_avg_score", "score_vs_industry",
        # v1.8 + v2.0 新增列
        "data_mode", "financial_data_years", "auto_calculated_metrics",
        "fin_source", "fin_updated_at", "fin_data_warning",
        # 元数据
        "data_source", "confidence_score", "circle_of_competence",
        "warning_note", "debt_reason",
        "moat_reason", "management_reason", "risk_reason",
    ]
    df = pd.DataFrame(results)[cols]
    df = df.sort_values("total_score", ascending=False).reset_index(drop=True)
    df.to_csv(filepath, index=False, encoding="utf-8-sig")
    print(f"完整结果已保存：{filepath}")
    return df


def _candidate_reason(row):
    """
    v2.1.0-alpha2：研究候选的 reason 列。
    = 为什么值得研究（入选理由） + 数据完整度说明（还缺什么）。
    明确告知"缺失≠公司差"，引导补齐后复评。
    """
    base = generate_candidate_reason(row)
    mf = str(row.get("missing_fields", "") or "").strip()
    if mf and mf != "（无）":
        return f"{base}｜数据待补录：{mf}（缺失≠公司差，补齐后可复评）"
    return f"{base}｜数据完整"


def save_research_candidates(df, filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    cands = df[df["final_decision"].isin(["深入研究", "加入观察池"])].copy()
    if cands.empty:
        print("  无股票通过全部检查（研究候选名单为空）"); return
    # v2.1.0-alpha2：reason（为何值得研究 + 缺哪些数据）；missing_fields 已在 df 中
    cands["reason"] = cands.apply(_candidate_reason, axis=1)
    cands.to_csv(filepath, index=False, encoding="utf-8-sig")
    print(f"研究候选股已保存：{filepath}  共 {len(cands)} 只")


# ============================================================
# 终端输出
# ============================================================

def print_warnings(ticker, warnings):
    for w in warnings:
        print(f"  ⚠ [{ticker}] {_compress_warnings(w)}")


def print_top10(df):
    top10 = df.head(10).copy()
    cols  = [
        ("排名",     4,  None,                'r'),
        ("代码",     7,  "ticker",             'l'),
        ("名称",    10,  "name",               'l'),
        ("行业",    10,  "industry",           'l'),
        ("总分",     6,  "total_score",        'r'),
        ("行业排名", 6,  "industry_rank",      'r'),
        ("超额",     7,  "score_vs_industry",  'r'),
        ("最终决策", 12, "final_decision",     'l'),
        ("数据模式", 10, "data_mode",          'l'),
    ]
    sep    = "+" + "+".join("-" * (w + 2) for _, w, _, _ in cols) + "+"
    header = "|" + "|".join(" " + _rpad(h, w) + " " for h, w, _, _ in cols) + "|"
    W      = sum(w + 3 for _, w, _, _ in cols) + 1

    print("\n" + "=" * W)
    print("  芒格式选股评分器 v2.3.0-alpha2  —  评分前 10 名（含行业对比 & 数据模式）")
    print("=" * W);  print(sep);  print(header);  print(sep)

    for rank, (_, r) in enumerate(top10.iterrows(), start=1):
        line = "|"
        for h, w, field, align in cols:
            if field is None:
                cell = f"#{rank}"
            elif field == "total_score":
                cell = f"{r[field]:.1f}"
            elif field == "industry_rank":
                cell = f"#{int(r[field])}"
            elif field == "score_vs_industry":
                cell = _fmt_vs(r[field])
            elif field == "data_mode":
                # 缩短显示
                cell = "自动" if str(r[field]) == "annual_financials" else "手动"
            else:
                cell = str(r[field])
            line += " " + (_lpad(cell, w) if align == 'r' else _rpad(cell, w)) + " |"
        print(line)

    print(sep)
    print("  数据模式：自动 = annual_financials.csv 计算；手动 = stocks.csv 手填")
    print("=" * W)


def print_score_detail(result):
    # v2.2.0-alpha2：待补录股票不展示质量分项（缺失≠公司差）
    if _is_pending(result):
        mf = result.get("missing_fields", "") or ""
        print(f"\n  [待补录][{result['ticker']}] {_uf(result.get('name'))}  "
              f"（{_market_label(result.get('market'))}）  决策:{result['final_decision']}")
        print(f"    数据不足，暂不输出质量结论；缺失：{mf if mf and mf != '（无）' else '关键财务字段'}")
        return

    mode_tag = "[自动]" if result.get("data_mode") == "annual_financials" else "[手动]"
    wn = _trunc(result["warning_note"], 40) if result["warning_note"] else "无警告"
    td = result["_trend_detail"]
    trend_str = "/".join(f"{k.split('_')[0]}:{v[:3]}" for k, v in td.items())
    print(f"\n  {mode_tag}[{result['ticker']}] {result['name']}  "
          f"总分:{result['total_score']:.1f}  决策:{result['final_decision']}  "
          f"行业#{result['industry_rank']}  超额:{_fmt_vs(result['score_vs_industry'])}"
          f"  趋势:{result['trend_score']:.1f}/10")
    print(f"    生意{result['quality_score']:.0f}/护城河{result['moat_score']:.1f}"
          f"/成长{result['growth_score']:.0f}/负债{result['balance_sheet_score']:.0f}"
          f"/估值{result['valuation_score']:.0f}/管理{result['management_score']:.1f}"
          f"/扣{result['risk_penalty']:.0f}")
    if result["warning_note"]:
        print(f"    ⚠ {wn}")


def print_pending_detail(result):
    """
    v2.2.0-alpha2：待补录展示模式。
    数据不足时只展示事实信息 + 下一步建议 + 免责声明，
    绝不输出"生意质量差/护城河薄弱/估值偏高/管理层差"等质量结论。
    """
    W = 70
    market = str(result.get("market", "")).strip().upper()
    mf     = result.get("missing_fields", "") or ""

    print("\n" + "=" * W)
    print(f"  [{result['ticker']}] {_uf(result.get('name'))}  ·  待补录研究骨架")
    print("─" * W)
    print(f"  市场：{_market_label(market)}    币种：{_uf(result.get('currency'))}")
    print(f"  数据状态：数据不足（待补录）")
    print(f"  最终决策：{result.get('final_decision', '数据不足（待补录）')}")
    print(f"  行业排名：数据不足，暂不参与有效排名")
    print("─" * W)
    print(f"  缺失关键财务字段：{mf if mf and mf != '（无）' else '（多项关键财务字段为空）'}")
    print(f"  人工判断字段（护城河/管理层/能力圈/理由等）：待人工补录")
    print("─" * W)
    print("  【下一步建议】")
    if market == "CN":
        print("    · A股自动抓取暂未完整支持，当前仅创建本地研究骨架，")
        print("      请通过人工补录或后续数据源导入补齐财务数据。")
    elif market == "US":
        print(f"    · 抓取财务数据： python fetcher.py {result['ticker']}")
        print(f"                     python fetcher.py --valuation {result['ticker']}")
    else:
        print("    · 请确认代码与市场后补齐数据。")
    print("    · 补录人工判断字段： python manual_review_helper.py --template")
    print("    · 补齐后重新运行： python main.py " + result['ticker'])
    print("─" * W)
    print("  ★ 数据不足时本工具不输出任何公司质量结论 ★")
    print("    缺失 ≠ 公司差；补齐数据后重新评分即可得到真实结果。")
    print("    本工具仅为研究辅助，不构成任何买入/卖出/持有建议。")
    print("=" * W)


def print_detailed_analysis(result):
    # v2.2.0-alpha2：数据不足 → 待补录展示模式，避免误导性质量判断
    if _is_pending(result):
        print_pending_detail(result)
        return

    W = 70

    print("\n" + "=" * W)
    print(f"  [{result['ticker']}] {result['name']}  ·  {result['industry']}")
    print(f"  数据日期：{result['data_date']}  来源：{result['data_source']}")
    print("─" * W)
    print(f"  总分：{result['total_score']:.1f}/100   评级：{result['rating']}")
    print(f"  最终决策：{result['final_decision']}")
    print(f"  行业内排名：{_industry_rank_label(result)}")
    print("─" * W)

    # ── v1.8：财务数据来源 ───────────────────────────────────
    mode = result.get("data_mode", "manual_fallback")
    yrs  = result.get("financial_data_years", "")
    auto = result.get("auto_calculated_metrics", "否")
    if mode == "annual_financials":
        print(f"  【财务数据来源】  ✓ 自动计算（annual_financials.csv，{yrs}）")
        src  = result.get("fin_source", "")
        upd  = result.get("fin_updated_at", "")
        fdw  = result.get("fin_data_warning", "")
        if src:
            print(f"    数据来源：{src}  |  最近更新：{upd or '未知'}")
        if fdw:
            print(f"    数据警告：{_compress_warnings(fdw)}")
    else:
        print(f"  【财务数据来源】  ⚠ 手动填写（stocks.csv），建议运行 fetcher.py 自动更新")

    # 显示关键量化指标
    roe_avg  = result.get("_display_roe_avg",  0)
    roic_avg = result.get("_display_roic_avg", 0)
    rev_cagr = result.get("_display_rev_cagr", 0)
    fcf_yrs  = result.get("_display_fcf_yrs",  0)
    nm_avg   = result.get("_display_nm_avg",   0)
    if roe_avg or roic_avg or rev_cagr:
        print(f"    ROE均值 {roe_avg}%  |  ROIC均值 {roic_avg}%  |  "
              f"净利率均值 {nm_avg}%")
        print(f"    营收CAGR {rev_cagr}%  |  FCF正数年份 {fcf_yrs} 年")
    # v2.0：ROE > 100% 时提示 ROIC/FCF 更重要
    if float(roe_avg or 0) > 100:
        print(f"    ⚠ ROE > 100% 通常由大量股票回购导致股东权益极低，")
        print(f"      ROE 评分虚高。请重点参考 ROIC（{roic_avg}%）和自由现金流。")
    print("─" * W)

    # ── 数据质量 ─────────────────────────────────────────────
    conf_str = f"{result['confidence_score']}/10" if str(result['confidence_score']).strip() else "未填写"
    print(f"  【数据质量】  可信度：{conf_str}"
          f"   能力圈：{_uf(result['circle_of_competence'])}")
    if result["warning_note"]:
        for w in result["warning_note"].split(" | "):
            print(f"    ⚠ {_compress_warnings(w)}")
    else:
        print("    ✓ 无数据异常")
    print("─" * W)

    # ── 六维进度条 ───────────────────────────────────────────
    dims = [
        ("生意质量", result["quality_score"],       30),
        ("护城河  ", result["moat_score"],           20),
        ("成长稳定", result["growth_score"],         15),
        ("负债安全", result["balance_sheet_score"],  15),
        ("估值合理", result["valuation_score"],      15),
        ("管理层  ", result["management_score"],      5),
    ]
    print("  【评分分项】")
    for label, sc, mx in dims:
        print(f"  {label} [{_bar(sc, mx)}] {sc:.1f}/{mx}")
    if result["risk_penalty"] < 0:
        print(f"  风险扣分 {'░' * 20}  {result['risk_penalty']:.0f} 分")
    print("─" * W)

    # ── 护城河拆分 ───────────────────────────────────────────
    calc_m = result.get("calculated_moat_score", "")
    sub_labels = {
        "brand_score": "品牌强度", "switching_cost_score": "转换成本",
        "network_effect_score": "网络效应", "scale_advantage_score": "规模优势",
        "pricing_power_score": "定价权", "moat_durability_score": "持续性",
    }
    has_subs = any(result.get(k, "") != "" for k in sub_labels)
    if has_subs:
        print(f"  【护城河拆分】  均值：{calc_m}/10  →  得分：{result['moat_score']:.1f}/20")
        for k, label in sub_labels.items():
            v = result.get(k, "—")
            try:
                fv = float(v)
                print(f"    {_rpad(label, 8)} [{_bar(fv, 10, 10)}] {v}/10")
            except (ValueError, TypeError):
                print(f"    {_rpad(label, 8)} [—] {v}")
        print(f"    护城河理由：{result['moat_reason'] or '（未填写）'}")
        print("─" * W)

    # ── 趋势 ─────────────────────────────────────────────────
    td = result.get("_trend_detail", {})
    ts = result.get("trend_score", 0)
    trend_map = {"improving": "↑改善", "stable": "→稳定", "declining": "↓下滑"}
    trend_display = {
        "roe_trend": "ROE趋势", "roic_trend": "ROIC趋势",
        "margin_trend": "利润率趋势", "revenue_trend": "营收趋势",
    }
    src_tag = "（来自年度数据自动计算）" if mode == "annual_financials" else "（来自手动填写）"
    print(f"  【财务趋势】  趋势分：{ts:.1f}/10  {src_tag}")
    for f, label in trend_display.items():
        val = td.get(f, "stable")
        print(f"    {_rpad(label, 8)} {trend_map.get(val, val)}")
    print("─" * W)

    # ── 亮点 & 风险 ─────────────────────────────────────────
    narrative = generate_narrative(result)
    print("  【亮点】")
    for s in narrative["strengths"]:
        print(f"    ✓ {s}")
    print()
    print("  【主要风险】")
    for r in narrative["risks"]:
        print(f"    ⚠ {r}")
    print()
    print("  【管理层】")
    print(f"    {result['management_reason'] or '（未填写）'}")
    print()
    print("  【风险说明】")
    print(f"    {result['risk_reason'] or '（未填写）'}")

    if result.get("debt_reason"):
        print()
        print("  【负债说明】")
        print(f"    {result['debt_reason']}")
        print("    注：D/E 不是唯一债务风险指标，建议结合 interest_coverage")
        print("    和 net_debt_to_EBITDA 进行更全面评估。")

    print()
    print("─" * W)
    for line in narrative["disclaimer"].split("\n"):
        print(f"  {line}")
    print("=" * W)


def lookup_ticker(ticker, all_results):
    ticker  = ticker.strip().upper()
    matched = [r for r in all_results if r["ticker"].upper() == ticker]
    if not matched:
        print(f"\n  未找到 '{ticker}'，可用代码：{', '.join(r['ticker'] for r in all_results)}")
        return
    print_detailed_analysis(matched[0])


# ============================================================
# v2.2.0-alpha1：按需查询——确保代码在本地存在（必要时建骨架/抓取）
# ============================================================

def _has_annual(canonical):
    """annual_financials.csv 是否已有该 ticker 的年度财务（用于判断 A股是否需刷新）。"""
    if not os.path.exists(ANNUAL_PATH):
        return False
    try:
        import csv as _csv
        with open(ANNUAL_PATH, encoding="utf-8-sig", newline="") as f:
            t = str(canonical).strip().upper()
            return any(str(row.get("ticker", "")).strip().upper() == t
                       for row in _csv.DictReader(f))
    except Exception:
        return False


def ensure_local(raw_ticker):
    """
    识别并规范化代码，确保其在本地 stocks.csv 中存在。
    返回 canonical（成功）或 None（无法识别）。

    - 已存在：直接返回。
    - 美股不存在：建骨架 → 抓年度财务（annual_financials.csv）
                  → 估值 pe/fcf_yield 经 store 白名单写回。
    - A股不存在：仅建骨架（market=CN/currency=CNY，标记待人工补录），不抓取。

    所有 stocks.csv 写入都经过 store（白名单保护人工字段）。
    抓取失败不致命：骨架保留，缺数据走"数据不足（待补录）"。
    """
    r = resolve(raw_ticker)
    if r.market == UNKNOWN or not r.canonical:
        print(f"\n  无法识别代码 '{raw_ticker}'。")
        print("  支持格式：美股 AAPL / BRK-B；A股 600519 / 600519.SH / 000001.SZ")
        return None

    existed = store.exists(r.canonical)
    if existed:
        print(f"  本地已有 {r.canonical}（{r.market}），直接分析。")
    else:
        print(f"\n  本地无 {r.canonical}，创建骨架"
              f"（market={r.market} currency={r.currency}，人工字段留空待补录）...")
        store.upsert_skeleton(r)

    # 美股：仅在新建时自动抓取（避免每次查询都打 yfinance）
    if r.market == "US" and r.autofetch_supported and not existed:
        try:
            import fetcher
        except Exception as e:
            print(f"  ⚠ 无法加载 fetcher（{e}），保留骨架，跳过自动抓取。")
            return r.canonical
        if not getattr(fetcher, "_HAS_YF", False):
            print("  ⚠ 未安装 yfinance，保留骨架，跳过自动抓取（pip install yfinance）。")
            return r.canonical
        # 1) 基础信息 profile → 经 store 白名单写回（v2.3.0-alpha1）
        try:
            prof   = fetcher.fetch_profile_yf(r.provider_symbol)
            fields = {k: v for k, v in prof.items() if v}   # 仅写非空，industry 覆盖 Unknown
            if fields:
                store.update_machine_fields(r.canonical, fields)
                print(f"  基础信息已写入（经 store 白名单）：{fields.get('long_name','')}"
                      f" / {fields.get('sector','')} / {fields.get('industry','')}")
        except Exception as e:
            print(f"  ⚠ 基础信息抓取失败（{e}），保留骨架。")
        # 2) 年度财务 → annual_financials.csv（机器专用文件）
        print(f"  正在抓取 {r.provider_symbol} 年度财务数据...")
        try:
            fetcher.fetch_and_update([r.provider_symbol])
        except Exception as e:
            print(f"  ⚠ 年度财务抓取失败（{e}），保留骨架，可稍后重试。")
        # 3) 估值 pe/fcf_yield → 经 store 白名单写回 stocks.csv
        try:
            val    = fetcher.fetch_valuation_yf(r.provider_symbol)
            fields = {}
            if val.get("pe")        is not None: fields["pe"]        = val["pe"]
            if val.get("fcf_yield") is not None: fields["fcf_yield"] = val["fcf_yield"]
            if fields:
                store.update_machine_fields(r.canonical, fields)
                print(f"  估值已写入（经 store 白名单）：{fields}")
        except Exception as e:
            print(f"  ⚠ 估值抓取失败（{e}），可稍后运行 fetcher.py --valuation。")
    elif r.market == "CN":
        # A股：新建必抓；已存在但尚无年度财务则刷新。akshare 调用全封装在 provider 层。
        if existed and _has_annual(r.canonical):
            pass   # 已有年度财务，不重复抓取
        else:
            try:
                from providers import ashare_provider
            except Exception as e:
                ashare_provider = None
                print(f"  ⚠ 无法加载 ashare_provider（{e}），A股回退待补录。")
            if ashare_provider and ashare_provider.is_available():
                print("  使用 AKShare 抓取 A股数据（基础信息/估值/年度财务）...")
                try:
                    ashare_provider.ingest(r.canonical, r.exchange)
                except Exception as e:
                    print(f"  ⚠ AKShare 抓取异常（{e}），保留骨架，待补录（不误判为差公司）。")
            else:
                print("  未安装 akshare，A股回退为『数据不足，待补录』。")
                print("  安装 A股数据支持： pip install -r requirements-optional.txt")
                print("  （安装后重跑即可；当前不会误判为差公司。）")

    return r.canonical


def generate_ai_for(canonical, stocks, results):
    """
    v2.3.0-alpha2：为查询的单只股票生成「AI 暂定质化判断」并经 store 白名单持久化。
    - 用合并后的财务指标喂 HeuristicProvider（纯规则）。
    - 数据不足时不编分（ai_analysis 内部处理）。
    - 只写 ai_* 字段（store.update_ai_fields 白名单保护人工字段）。
    - 同步注入 result，供卡片即时展示。
    """
    canonical = str(canonical).strip().upper()
    row = next((r for r in stocks
                if str(r.get("canonical_ticker") or r.get("ticker", "")).strip().upper() == canonical),
               None)
    res = next((r for r in results if r["ticker"].strip().upper() == canonical), None)
    if row is None or res is None:
        return

    is_pending = (str(res.get("final_decision", "")) == "数据不足（待补录）"
                  or str(res.get("data_status", "")) == "待补录")
    metrics = {k: row.get(k) for k in (
        "roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg",
        "revenue_growth_5y_cagr", "debt_to_equity", "fcf_positive_years", "pe",
        "roe_trend", "roic_trend", "margin_trend", "revenue_trend", "sector", "industry")}
    metrics["is_pending"] = is_pending

    aiq    = ai_analysis.analyze(metrics)
    fields = aiq.to_store_dict()
    store.update_ai_fields(canonical, fields)   # 只写 ai_*，拒绝人工字段
    for k, v in fields.items():                 # 注入 result 供卡片展示
        res[k] = v
    print(f"  AI 暂定判断已生成（{aiq.ai_model}，置信度 {aiq.ai_confidence}，"
          f"仅供参考、非人工确认、需人工复核）")


# ============================================================
# 主程序
# ============================================================

def main():
    print("=" * 70)
    print("   芒格式选股评分器  Munger Stock Screener  v2.3.0-alpha2")
    print("=" * 70)
    print("★ 纯学习工具，不连接券商，不自动下单 ★\n")

    # ── 步骤 0：运行前自检（v2.1.0）────────────────────────────
    # 检查依赖/目录/文件/必要列；致命问题直接给出方案并退出，
    # 提示类问题（如缺 annual_financials.csv）打印后继续运行。
    ok, problems = run_preflight()
    if problems:
        print_report(problems)
        print()
    if not ok:
        print("  自检未通过，已停止运行。请按上面的方案处理后重试。\n")
        return

    # ── 步骤 0.5：查询模式（v2.2.0-alpha1）─────────────────────
    # 有参数时先确保该代码在本地存在（必要时建骨架+抓取），再走批量管道。
    # 无参数时为原批量评分模式，行为完全不变。
    query_canonical = None
    if len(sys.argv) > 1:
        query_canonical = ensure_local(sys.argv[1])
        if query_canonical is None:
            return
        print()

    # ── 步骤 1：读取 stocks.csv（人工判断字段）─────────────────
    stocks = load_stocks(INPUT_PATH)
    if not stocks:
        return

    # ── 步骤 2：读取 annual_financials.csv 并计算指标（v1.8）───
    print()
    fin_metrics = compute_all_metrics(ANNUAL_PATH)

    # ── 步骤 3：合并年度财务数据到 stocks 行（v1.8）────────────
    # 只覆盖量化财务字段，不碰护城河/管理层/能力圈等人工判断字段
    stocks = merge_annual_data(stocks, fin_metrics)

    auto_count   = sum(1 for r in stocks if r.get("data_mode") == "annual_financials")
    manual_count = len(stocks) - auto_count
    print(f"  数据模式：{auto_count} 只使用自动计算，{manual_count} 只回退手动数据")

    # ── 步骤 4：评分 + 数据校验 + 最终决策 ──────────────────────
    print("\n正在评分 & 数据校验...")
    results, all_warnings = [], []

    for row in stocks:
        result                   = score_stock(row)
        warnings, warning_note   = validate_data(row)
        result["warning_note"]   = warning_note
        result["final_decision"] = get_final_decision(result, row)

        # v2.1.0-alpha2：标注关键量化字段缺失情况（缺失≠公司差）
        _missing = missing_key_quant_fields(row)
        result["missing_fields"] = "、".join(quant_labels(_missing)) if _missing else "（无）"
        result["data_status"]    = "待补录" if _missing else "完整"
        # v2.2.0-alpha2：市场/币种/补录状态（供待补录展示模式使用）
        result["market"]           = row.get("market", "")
        result["currency"]         = row.get("currency", "")
        result["review_status"]    = row.get("review_status", "")
        result["canonical_ticker"] = row.get("canonical_ticker", "")
        # v2.3.0-alpha1：基础信息 + 风险短标签（供研究卡片展示）
        result["long_name"]     = row.get("long_name", "")
        result["sector"]        = row.get("sector", "")
        result["country"]       = row.get("country", "")
        result["risk_note"]     = row.get("risk_note", "")
        result["pe"]            = row.get("pe", "")           # v2.4.0（卡片估值行）
        result["pb"]            = row.get("pb", "")           # v2.4.0
        result["market_cap"]    = row.get("market_cap", "")   # v2.4.0
        # v2.3.0-alpha2：透传已存的 AI 暂定字段（供卡片展示）
        for _aif in ("ai_moat_score", "ai_management_score", "ai_risk_flags",
                     "ai_confidence", "ai_reason", "ai_evidence_needed",
                     "ai_model", "ai_generated_at", "needs_human_review"):
            result[_aif] = row.get(_aif, "")

        # v1.8：注入数据模式和展示用的关键财务指标
        result["data_mode"]               = row.get("data_mode", "manual_fallback")
        result["financial_data_years"]    = row.get("financial_data_years", "")
        result["auto_calculated_metrics"] = row.get("auto_calculated_metrics", "否")
        # 供 print_detailed_analysis 展示，带 _display_ 前缀避免与评分字段混淆
        result["_display_roe_avg"]  = round(float(row.get("roe_5y_avg") or 0), 1)
        result["_display_roic_avg"] = round(float(row.get("roic_5y_avg") or 0), 1)
        result["_display_rev_cagr"] = round(float(row.get("revenue_growth_5y_cagr") or 0), 1)
        result["_display_fcf_yrs"]  = int(float(row.get("fcf_positive_years") or 0))
        result["_display_nm_avg"]   = round(float(row.get("net_margin_5y_avg") or 0), 1)
        # v2.0 新增展示字段
        result["fin_source"]        = row.get("_fin_source", "")
        result["fin_updated_at"]    = row.get("_fin_updated_at", "")
        # v2.1：过滤掉对特殊行业（银行/综合控股/保险）无意义的财务警告片段
        result["fin_data_warning"]  = filter_fin_warning(
            row, row.get("_fin_data_warning", "")
        )

        results.append(result)
        if warnings:
            all_warnings.append((row.get("ticker", "?"), warnings))

    # ── 步骤 5：行业对比 ─────────────────────────────────────────
    calc_industry_comparison(results)
    print(f"完成！共评分 {len(results)} 只股票。")

    # ── 步骤 6：集中打印 Warning ─────────────────────────────────
    if all_warnings:
        print("\n" + "─" * 70)
        print(f"  数据校验警告（共 {sum(len(w) for _, w in all_warnings)} 条）")
        print("─" * 70)
        for ticker, warns in all_warnings:
            print_warnings(ticker, warns)

    # ── 步骤 7：简要明细 ─────────────────────────────────────────
    print("\n" + "─" * 70)
    print("  逐股评分简要（[自动] = 年度数据计算  [手动] = stocks.csv 手填）")
    print("─" * 70)
    for r in results:
        print_score_detail(r)

    # ── 步骤 8：保存 CSV ─────────────────────────────────────────
    print("\n" + "─" * 70)
    df = save_results(results, OUTPUT_FULL)
    save_research_candidates(df, OUTPUT_CANDS)

    # ── 步骤 9：前 10 排名表 ─────────────────────────────────────
    print_top10(df)

    # ── 步骤 10：分布统计 ────────────────────────────────────────
    print("\n  final_decision 分布：")
    for decision, count in df["final_decision"].value_counts().items():
        print(f"    {_rpad(decision, 14)} {'█' * count}  ({count} 只)")

    print("\n  行业对比摘要（各行业第1名）：")
    seen = set()
    for _, row_data in df.iterrows():
        if row_data["industry_rank"] == 1 and row_data["industry"] not in seen:
            seen.add(row_data["industry"])
            print(f"    {_rpad(row_data['industry'], 12)} "
                  f"#{1} {row_data['ticker']}({row_data['total_score']:.1f}分)"
                  f"  均值:{row_data['industry_avg_score']:.1f}"
                  f"  [{row_data.get('data_mode', '')[:4]}]")

    print()

    # ── 步骤 11：单股查询 ─────────────────────────────────────────
    if len(sys.argv) > 1:
        # v2.3.0-alpha1：CLI 单股输出改为「研究卡片」
        tk = (query_canonical or sys.argv[1]).strip().upper()
        matched = [r for r in results if r["ticker"].upper() == tk]
        if matched:
            generate_ai_for(tk, stocks, results)   # v2.3.0-alpha2：生成 AI 暂定判断
            research_card.render(matched[0])
            # v2.3.0-alpha3：研究卡片落盘 research_notes/<canonical>_card.md
            try:
                path = research_card.save_card(matched[0])
                print(f"\n  研究卡片已保存：{path}")
            except Exception as e:
                print(f"\n  ⚠ 研究卡片保存失败（{e}），不影响终端输出。")
        else:
            print(f"\n  未找到 '{tk}'，可用代码：{', '.join(r['ticker'] for r in results)}")
    else:
        print("─" * 70)
        print("  输入股票代码查看深度分析，直接回车退出")
        print("─" * 70)
        while True:
            raw = input("  代码：").strip()
            if not raw:
                break
            lookup_ticker(raw, results)
            print()

    print("\n  完成！查看 output/ 目录获取完整结果。\n")


if __name__ == "__main__":
    main()
