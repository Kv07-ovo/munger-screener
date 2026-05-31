# ============================================================
# financial_analyzer.py  —  芒格式选股评分器：年度财务分析  v2.0
#
# v2.0 改动（相对 v1.8）：
#   1. 新增 _sfn()：安全转 float，空值返回 None（不污染均值）
#   2. avg() 改为跳过空值
#   3. 透传 source / updated_at / fin_data_warnings / years_count
#   4. 忽略 CSV 中多出来的列（source/updated_at/data_warning 等）
#   5. 支持不足 5 年数据（有几年算几年，并在结果中标注）
# ============================================================

import os
import pandas as pd


def _sf(val, default=0.0):
    """安全转 float，失败或 NaN 返回 default（默认 0.0）。"""
    try:
        v = float(val)
        return default if v != v else v
    except (ValueError, TypeError):
        return default


def _sfn(val):
    """
    安全转 float，空值/无效值返回 None。
    与 _sf 的区别：空字符串、"nan" 等返回 None 而不是 0，
    避免把缺失值当作 0 参与均值计算。
    """
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in ("", "nan", "none", "n/a", "null"):
        return None
    try:
        v = float(s)
        return None if v != v else v   # NaN → None
    except (ValueError, TypeError):
        return None


def _trend(first_val, last_val, threshold=0.10):
    """
    比较最早年份和最新年份，判断趋势。
    improving: +10% 以上；declining: -10% 以下；stable: 其余。
    任一值为 None 或非正数时返回 stable。
    """
    if first_val is None or last_val is None or first_val <= 0:
        return "stable"
    ratio = last_val / first_val
    if ratio > 1 + threshold:
        return "improving"
    if ratio < 1 - threshold:
        return "declining"
    return "stable"


def _cagr(start, end, years):
    """复合年化增长率（%）。start/end 为 None 或非正数时返回 0.0。"""
    if not start or not end or start <= 0 or end <= 0 or years <= 0:
        return 0.0
    return round(((end / start) ** (1.0 / years) - 1) * 100, 2)


def compute_ticker_metrics(rows):
    """
    对单只股票的年度数据列表计算财务指标。

    v2.0 改进：
      - avg() 跳过空值，不把缺失当 0 处理
      - 透传 source / updated_at / fin_data_warnings / years_count
      - 支持不足 5 年数据（计算已有年份，并在结果中注明）
      - 忽略 source/updated_at/data_warning 等额外列

    参数：
        rows : list of dict，每个 dict 代表一年的财务数据

    返回：
        dict，含所有供 main.py 注入的字段；不足 2 年时返回 None
    """
    if len(rows) < 2:
        return None

    # 按年份升序，取最近 5 年
    rows = sorted(rows, key=lambda r: int(_sf(r.get("year", 0))))[-5:]
    n     = len(rows)
    first = rows[0]
    last  = rows[-1]

    def avg(field):
        """跳过空值求均值；全部为空时返回 0.0。"""
        valid = [v for v in (_sfn(r.get(field)) for r in rows) if v is not None]
        return round(sum(valid) / len(valid), 2) if valid else 0.0

    # 5 年平均财务指标
    roe_avg  = avg("roe")
    roic_avg = avg("roic")
    gm_avg   = avg("gross_margin")
    nm_avg   = avg("net_margin")

    # FCF 正数年数（空字符串视为 0，不计入正数）
    fcf_pos = sum(1 for r in rows if (_sfn(r.get("free_cash_flow")) or 0) > 0)

    # 最新年 D/E
    de_latest = _sfn(last.get("debt_to_equity")) or 0.0

    # CAGR（年数用实际年份差）
    years_span = int(_sf(last.get("year"))) - int(_sf(first.get("year")))
    years_span = max(years_span, 1)

    rev_start  = _sfn(first.get("revenue"))
    rev_end    = _sfn(last.get("revenue"))
    earn_start = _sfn(first.get("net_income"))
    earn_end   = _sfn(last.get("net_income"))

    rev_cagr  = _cagr(rev_start,  rev_end,  years_span)
    earn_cagr = _cagr(earn_start, earn_end, years_span)

    # 趋势（比较最早年 vs 最新年）
    roe_t    = _trend(_sfn(first.get("roe")),        _sfn(last.get("roe")))
    roic_t   = _trend(_sfn(first.get("roic")),       _sfn(last.get("roic")))
    margin_t = _trend(_sfn(first.get("net_margin")), _sfn(last.get("net_margin")))
    rev_t    = _trend(rev_start, rev_end)

    start_yr = int(_sf(first.get("year")))
    end_yr   = int(_sf(last.get("year")))

    # 透传 source / updated_at（取最新一年的值）
    source     = str(last.get("source",     "")).strip()
    updated_at = str(last.get("updated_at", "")).strip()

    # 汇总所有年份的 data_warning
    all_warnings = [str(r.get("data_warning", "")).strip() for r in rows]
    fin_data_warnings = "; ".join(w for w in all_warnings if w)

    # 年份不足提示
    if n < 5:
        shortage_note = f"年度数据仅 {n} 年（建议 5 年）"
        fin_data_warnings = (
            shortage_note + (f"; {fin_data_warnings}" if fin_data_warnings else "")
        )

    return {
        # ── 与 stocks.csv 字段对齐（供 main.py 直接注入）──
        "roe_5y_avg":             roe_avg,
        "roic_5y_avg":            roic_avg,
        "gross_margin_5y_avg":    gm_avg,
        "net_margin_5y_avg":      nm_avg,
        "debt_to_equity":         de_latest,
        "fcf_positive_years":     fcf_pos,
        "revenue_growth_5y_cagr": rev_cagr,
        "eps_growth_5y_cagr":     earn_cagr,
        "roe_trend":              roe_t,
        "roic_trend":             roic_t,
        "margin_trend":           margin_t,
        "revenue_trend":          rev_t,
        # ── 元信息（供展示，不参与评分）──────────────────
        "data_years":             f"{start_yr}-{end_yr}",
        "years_count":            n,
        "source":                 source,
        "updated_at":             updated_at,
        "fin_data_warnings":      fin_data_warnings,
    }


def compute_all_metrics(filepath):
    """
    读取 annual_financials.csv，对每只股票计算财务指标。

    v2.0：自动忽略 source/updated_at/data_warning 等额外列，
          有几年算几年，不足数据只提示不崩溃。

    返回：{ "AAPL": {...}, "MSFT": {...}, ... }
    文件不存在时返回空 dict（程序回退到 stocks.csv 手动数据）。
    """
    if not os.path.exists(filepath):
        print(f"  [financial_analyzer] 未找到 {filepath}，跳过自动计算")
        return {}

    try:
        df = pd.read_csv(filepath, encoding="utf-8", dtype=str)
    except Exception as e:
        print(f"  [financial_analyzer] 读取失败：{e}")
        return {}

    df = df.fillna("")

    # 按 ticker 分组
    groups = {}
    for _, row in df.iterrows():
        t = str(row.get("ticker", "")).strip().upper()
        if t:
            groups.setdefault(t, []).append(dict(row))

    # 逐只计算
    results = {}
    for ticker, rows in groups.items():
        metrics = compute_ticker_metrics(rows)
        if metrics:
            results[ticker] = metrics

    print(f"  [financial_analyzer] 已计算 {len(results)} 只股票"
          f"（{', '.join(sorted(results.keys()))}）")
    return results
