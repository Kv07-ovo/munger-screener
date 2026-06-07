# ============================================================
# research_service.py  —  核心研究流水线（CLI 与 Web 共用）  v2.5.0 web-mvp
#
# 职责：把「识别市场 → 抓取/刷新 → 读取+合并+评分+行业对比 → AI 初判 →
#       合成研究优先级 → 研究卡片文本」这套核心流程集中在此一处，
#       由 main.py（CLI 展示）与 web_app.py（Streamlit 展示）共同调用，
#       不复制两套逻辑。
#
# 单向依赖：
#   main.py / web_app.py
#     → research_service.py
#       → store / scorer / validator / financial_analyzer / providers / research_card
#   （本模块不 import main，也不 import web_app，避免循环依赖。）
#
# 严格不变量（与既有版本一致）：
#   - 不改评分公式（scorer.py）、不改 research_priority、不改 store 白名单。
#   - 人工字段永不被自动覆盖（仍由 store 白名单保证）。
#   - 缺失数据 = 待补录，不判为差公司。
#   - 不输出买入/卖出/持有建议。
#   - AKShare 为可选依赖：未装/失败时回退「数据不足，待补录」，不崩。
#
# 函数全部从 main.py 原样搬迁，逻辑未改；另新增 score_all / load_score_compare /
# run_research 作为共用入口。
# ============================================================

import os
import csv

import pandas as pd

import store
import ai_analysis
import ai_scorer
import research_card
from ticker_resolver     import resolve, UNKNOWN
from scorer              import score_stock
from validator           import (validate_data, get_final_decision, filter_fin_warning,
                                  missing_key_quant_fields, quant_labels, effective_industry)
from financial_analyzer  import compute_all_metrics

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH    = os.path.join(BASE_DIR, "data", "stocks.csv")
ANNUAL_PATH   = os.path.join(BASE_DIR, "data", "annual_financials.csv")
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
    # v2.4.2：A股数据口径版本戳（机器） + 纯人工备注（受保护）
    "data_rev":            "",
    "notes":               "",
}

# ── v1.8：可被年度财务数据自动覆盖的字段（只有这些）──────────
# 不包含任何需要人工判断的字段（护城河/管理层/能力圈/风险/PE等）
_FINANCIAL_OVERRIDE_FIELDS = [
    "roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg",
    "debt_to_equity", "fcf_positive_years",
    "revenue_growth_5y_cagr", "eps_growth_5y_cagr",
    "roe_trend", "roic_trend", "margin_trend", "revenue_trend",
]


# ── 共用小工具（被核心与展示层共同使用）──────────────────────
def _is_pending(result):
    """
    数据严重不足（待补录）→ 待补录展示模式，不输出质量结论。
    v2.4.1：只看 final_decision（≥2 关键字段缺失等）；仅 1 项缺失不算 pending，
    走"部分机器财务分 + 研究优先级封顶中（数据不完整）"。
    """
    return str(result.get("final_decision", "")) == "数据不足（待补录）"


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


# ============================================================
# v1.8：年度财务数据合并
# ============================================================

def merge_annual_data(stocks, fin_metrics):
    """
    将 financial_analyzer 计算出的财务指标注入 stocks 行中。
    只覆盖量化财务字段（_FINANCIAL_OVERRIDE_FIELDS），不碰任何人工判断字段。
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
    from collections import defaultdict
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
# 数据读取
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


# ============================================================
# 按需查询：确保代码在本地存在（必要时建骨架/抓取）
# ============================================================

def _has_annual(canonical):
    """annual_financials.csv 是否已有该 ticker 的年度财务。"""
    if not os.path.exists(ANNUAL_PATH):
        return False
    try:
        with open(ANNUAL_PATH, encoding="utf-8-sig", newline="") as f:
            t = str(canonical).strip().upper()
            return any(str(row.get("ticker", "")).strip().upper() == t
                       for row in csv.DictReader(f))
    except Exception:
        return False


def _ashare_needs_refresh(canonical):
    """
    v2.4.2：A股是否需要重新抓取年度数据（仅 CN 用，不影响美股）。
      - 无 annual 行 → 需刷新
      - stocks.csv 的 data_rev 为空/缺失/非数字 → 视为旧数据 → 需刷新
      - data_rev < 当前 ASHARE_DATA_REV → 需刷新
      - data_rev == 当前 → 最新，不刷新
    判定本身只读，不抓取、不写库。
    """
    if not _has_annual(canonical):
        return True
    try:
        from providers import ashare_provider   # 仅取常量，import 不需要 akshare
        current = int(ashare_provider.ASHARE_DATA_REV)
    except Exception:
        return False   # 取不到当前口径版本时，不强制刷新（保留现有数据）
    row = store.get(canonical)
    rev_raw = "" if row is None else str(row.get("data_rev", "")).strip()
    try:
        rev = int(float(rev_raw))      # 空/非数字 → 抛异常 → 视为旧数据
    except (ValueError, TypeError):
        return True
    return rev < current


def ensure_local(raw_ticker):
    """
    识别并规范化代码，确保其在本地 stocks.csv 中存在。
    返回 canonical（成功）或 None（无法识别）。

    - 已存在：直接返回。
    - 美股不存在：建骨架 → 抓年度财务（annual_financials.csv）
                  → 估值 pe/fcf_yield 经 store 白名单写回。
    - A股不存在/口径过旧：建骨架并经 provider 抓取/刷新（akshare 不可用则保留待补录）。

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
        # A股：新建必抓；已存在但数据陈旧/口径过时（data_rev 旧）则刷新。
        # akshare 调用全封装在 provider 层；判定只读、不影响美股。
        needs = (not existed) or _ashare_needs_refresh(r.canonical)
        if not needs:
            pass   # 已有最新口径年度数据，不重复抓取
        else:
            try:
                from providers import ashare_provider
            except Exception as e:
                ashare_provider = None
                print(f"  ⚠ 无法加载 ashare_provider（{e}），A股回退待补录。")
            if ashare_provider and ashare_provider.is_available():
                action = "刷新（检测到旧/缺数据）" if existed else "抓取"
                print(f"  使用 AKShare {action} A股数据（基础信息/估值/年度财务）...")
                try:
                    ashare_provider.ingest(r.canonical, r.exchange)
                except Exception as e:
                    print(f"  ⚠ AKShare 抓取异常（{e}），保留现有数据，标记待刷新（不清空、不误判）。")
            else:
                # 未装 akshare/不可用：保留旧数据，仅提示待刷新——绝不清空、绝不崩
                print("  待刷新：未安装 akshare，A股数据无法抓取/刷新；保留现有数据。")
                print("  安装 A股数据支持： pip install -r requirements-optional.txt")
                print("  （安装后重跑即可；当前不会误判为差公司，也不会清空已有数据。）")

    return r.canonical


# ============================================================
# 评分（从 main 的批量循环抽出；逻辑逐字未改）
# ============================================================

def score_all(stocks):
    """
    对全部 stocks 行评分 + 数据校验 + 最终决策 + 展示字段注入。
    返回 (results, all_warnings)。无任何磁盘写入（纯计算）。
    """
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

    return results, all_warnings


# ============================================================
# AI 初判（启发式，仅写 ai_*；从 main 原样搬迁）
# ============================================================

def generate_ai_for(canonical, stocks, results):
    """
    v2.3.0-alpha2：为查询的单只股票生成「AI 暂定质化判断」并经 store 白名单持久化。
    数据不足时不编分；只写 ai_* 字段；同步注入 result 供卡片展示。
    """
    canonical = str(canonical).strip().upper()
    row = next((r for r in stocks
                if str(r.get("canonical_ticker") or r.get("ticker", "")).strip().upper() == canonical),
               None)
    res = next((r for r in results if r["ticker"].strip().upper() == canonical), None)
    if row is None or res is None:
        return

    # v2.4.1：只有"严重不足"(final_decision)才不生成；仅 1 项缺失仍生成（低置信度）
    is_pending = _is_pending(res)
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
# AI 动态评分（Phase 1：旁路、纯内存、不写盘、不联网）
# 仅向 result 注入展示字段 res["ai_dynamic"] 与 res["final_score_preview"]，
# 绝不调用 store、绝不写 stocks.csv、绝不参与排序。
# ============================================================

# AI 动态评分输入用到的原始指标（取自 stocks 行，而非评分结果）
_DYN_METRIC_FIELDS = (
    "roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg",
    "fcf_positive_years", "debt_to_equity", "revenue_growth_5y_cagr", "eps_growth_5y_cagr",
    "pe", "fcf_yield", "pe_percentile_5y", "pb", "market_cap",
    "roe_trend", "roic_trend", "margin_trend", "revenue_trend",
)
# 规则分上下文（只读引用，AI 不可改写）
_DYN_DIM_KEYS = (
    "quality_score", "moat_score", "growth_score", "balance_sheet_score",
    "valuation_score", "management_score", "risk_penalty", "trend_score",
)
_FINANCIAL_INDUSTRY_KW = ("银行", "保险", "证券", "券商", "资本市场", "综合控股")


def _build_dynamic_payload(row, res):
    """组装 AI 动态评分输入：原始指标 + 缺失标记(权威) + 行业/类型 + 规则分上下文。"""
    _missing = missing_key_quant_fields(row)
    ind = effective_industry(row)
    return {
        "company_type": {
            "industry": row.get("industry", ""),
            "sector":   row.get("sector", ""),
            "is_special_financial": any(kw in ind for kw in _FINANCIAL_INDUSTRY_KW),
        },
        "metrics": {k: row.get(k) for k in _DYN_METRIC_FIELDS},
        "missing_fields": {
            "missing_quant_fields": _missing,
            "missing_quant_labels": quant_labels(_missing),
            "data_status": res.get("data_status", ""),
            "is_pending":  _is_pending(res),
        },
        "rule_based_context": {
            "rule_based_score":     res.get("total_score"),
            "rule_based_score_max": 100,
            "dimension_scores": {k: res.get(k) for k in _DYN_DIM_KEYS},
        },
    }


def attach_dynamic_score(canonical, stocks, results):
    """
    为查询的单只股票生成 AI 动态评分并注入 result（仅展示）。
    - 数据不足 → res["ai_dynamic"] 为合法的「数据不足」结构（rating=数据不足, ai_score=None）。
    - provider 异常 / 校验失败 / 内部异常 → res["ai_dynamic"]=None（UI 显示「AI 动态评分未生成」）。
    任何情况下 final_score_preview 都有值、且不参与排序；不写任何文件。
    """
    canonical = str(canonical).strip().upper()
    res = next((r for r in results
                if str(r.get("ticker", "")).strip().upper() == canonical), None)
    if res is None:
        return
    try:
        row = next((r for r in stocks
                    if str(r.get("canonical_ticker") or r.get("ticker", "")).strip().upper() == canonical),
                   None)
        if row is None:
            res["ai_dynamic"] = None
            res["final_score_preview"] = res.get("total_score")
            return
        out = ai_scorer.score_dynamic(_build_dynamic_payload(row, res))
        if out is None:                                   # 失败 → 未生成
            res["ai_dynamic"] = None
            res["final_score_preview"] = res.get("total_score")
        else:
            res["ai_dynamic"] = out
            res["final_score_preview"] = ai_scorer.compute_final_preview(
                res.get("total_score"), out.get("ai_score"))
    except Exception:                                     # 兜底：绝不影响主流程
        res["ai_dynamic"] = None
        res["final_score_preview"] = res.get("total_score")


def attach_score_breakdown(canonical, stocks, results):
    """None-aware 动态评分增强（MVP，仅展示、只读、不写盘）：
      向 result 注入 res['data_confidence'] 与 res['score_breakdown']（逐维度可解释）。
      - 缺失字段不计入维度分母并重归一化；D/E 缺失/≤0 不给低负债分（见 api/score_engine.py）。
      - 不覆盖 res['total_score']（保持 API 兼容与 final_score_preview==total_score 不变量）。
      - 任何异常都不影响主评分/展示。"""
    canonical = str(canonical).strip().upper()
    res = next((r for r in results
                if str(r.get("ticker", "")).strip().upper() == canonical), None)
    if res is None:
        return
    try:
        row = next((r for r in stocks
                    if str(r.get("canonical_ticker") or r.get("ticker", "")).strip().upper() == canonical),
                   None)
        if row is None:
            return
        from api import score_engine    # 惰性 import，避免任何包初始化顺序问题
        eng = score_engine.compute(row)
        res["data_confidence"] = eng["data_confidence"]
        res["score_breakdown"] = eng["score_breakdown"]
    except Exception:
        res.pop("data_confidence", None)
        res.pop("score_breakdown", None)


def attach_ai_evidence_score(canonical, stocks, results, client=None):
    """ai_evidence_v1 主分链路（只读、不写盘）：向 result 注入 res['ai_evidence']。

    - res['ai_evidence'] 为经 validator 的可信结构化结果 + 元数据（scoring_method /
      validator_status / source_dates / missing_fields / stale_fields / generated_at 等）。
    - 无 key / provider 失败 / 校验失败 → ai_evidence 为明确的 ai_unavailable/failed 占位
      （total_score=None、scoring_method=unavailable），绝不回落 legacy total_score 作为主分。
    - 绝不写任何文件、绝不依赖网络可用性；任何异常都不影响既有展示字段。
    - client 可注入（测试/自定义 provider）；None 时按 env 选择（默认确定性 mock）。
    """
    canonical = str(canonical).strip().upper()
    res = next((r for r in results
                if str(r.get("ticker", "")).strip().upper() == canonical), None)
    if res is None:
        return
    try:
        row = next((r for r in stocks
                    if str(r.get("canonical_ticker") or r.get("ticker", "")).strip().upper() == canonical),
                   None)
        if row is None:
            return
        from api import ai_scoring_service   # 惰性 import，避免包初始化顺序问题
        res["ai_evidence"] = ai_scoring_service.score_with_ai(row, client=client)
    except Exception:
        # 主分链路异常：不影响既有展示，但打印到 stderr 便于排查（不泄露给前端）
        import sys
        import traceback
        traceback.print_exc(file=sys.stderr)
        res.pop("ai_evidence", None)


# ============================================================
# 共用编排：单股研究（CLI 与 Web 都调）
# ============================================================

def load_score_compare():
    """
    读取 stocks.csv + 合并年度财务 + 全量评分 + 行业对比。
    **只读计算，不写任何磁盘文件。**
    返回 (stocks, results, all_warnings)。
    """
    stocks = load_stocks(INPUT_PATH)
    if not stocks:
        return [], [], []
    fin_metrics = compute_all_metrics(ANNUAL_PATH)
    stocks = merge_annual_data(stocks, fin_metrics)
    results, all_warnings = score_all(stocks)
    calc_industry_comparison(results)
    return stocks, results, all_warnings


def run_research(ticker: str, readonly: bool = False) -> dict:
    """
    单股研究统一入口（CLI 单股 / Web 都调用）。

    返回 dict：
      ok, input, canonical, error, result, research_priority, card_text, readonly, warnings

    readonly=True（只读模式，Web 默认；可写需显式 WEB_WRITABLE=1）：
      - 不抓取新数据、不建骨架、不写 stocks.csv / annual_financials.csv / ai_*；
      - 仅读取本地已有数据并展示；
      - 本地无该 ticker → ok=False，error="本地暂无数据，当前为只读模式"。

    readonly=False（默认）：
      - 允许 ensure_local 抓取/建骨架/刷新（经 store 白名单）、生成 AI 初判（写 ai_*）。

    任何情况都不输出买入/卖出/持有建议；缺失数据=待补录、不判差。
    """
    out = {
        "ok": False, "input": ticker, "canonical": None, "error": None,
        "result": None, "research_priority": None, "card_text": None,
        "readonly": readonly, "warnings": [],
    }

    r = resolve(ticker)
    if r.market == UNKNOWN or not r.canonical:
        out["error"] = "无法识别股票代码（支持：AAPL / BRK-B / 600519 / 600519.SH / 000001.SZ）"
        return out
    canonical = r.canonical
    out["canonical"] = canonical

    if readonly:
        # 只读：绝不抓取/建骨架/写盘
        if not store.exists(canonical):
            out["error"] = "本地暂无数据，当前为只读模式"
            return out
    else:
        ensure_local(ticker)   # 允许写：建骨架/抓取/刷新（经 store 白名单）

    # 只读计算（load/merge/score/compare 均不写盘）
    stocks, results, all_warnings = load_score_compare()
    res = next((x for x in results
                if str(x.get("canonical_ticker") or x.get("ticker", "")).strip().upper() == canonical),
               None)
    if res is None:
        out["error"] = "本地暂无该股票的可用数据"
        return out

    if not readonly:
        generate_ai_for(canonical, stocks, results)   # 写 ai_*（仅非只读）

    # Phase 1：AI 动态评分（旁路、纯内存、不写盘、不联网；只读模式也生成供展示）
    attach_dynamic_score(canonical, stocks, results)
    # MVP：None-aware 动态评分增强（data_confidence + score_breakdown，仅展示、不改 total_score）
    attach_score_breakdown(canonical, stocks, results)
    # ai_evidence_v1：AI 证据评分（新主分链路；只读、不写盘）。失败→明确 ai_unavailable，不回落 legacy。
    attach_ai_evidence_score(canonical, stocks, results)

    out["ok"]                = True
    out["result"]            = res
    out["research_priority"] = research_card.research_priority(res)
    out["card_text"]         = research_card.card_text(res)
    out["warnings"]          = [w for (t, ws) in all_warnings
                                if str(t).strip().upper() == canonical for w in ws]
    return out
