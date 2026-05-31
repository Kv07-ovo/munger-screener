# ============================================================
# fetcher.py  —  芒格式选股评分器 v2.0：自动数据抓取模块
#
# 职责：
#   从 yfinance (Yahoo Finance) 获取美股年度财务数据，
#   写入 data/annual_financials.csv。
#
# 绝对不触碰：data/stocks.csv（人工判断字段永不覆盖）
#
# 用法：
#   python fetcher.py AAPL               # 单只股票
#   python fetcher.py AAPL MSFT NVDA     # 多只股票
#   python fetcher.py --watchlist        # 读取 stocks.csv 全部 ticker
#   python fetcher.py --help
#
# 依赖：pip install yfinance pandas
# ============================================================

import csv
import os
import sys
import shutil
from datetime import date, datetime

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# yfinance 可选导入，导入失败时给出友好提示
try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False

# ── 路径 ──────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
ANNUAL_PATH  = os.path.join(BASE_DIR, "data", "annual_financials.csv")
STOCKS_PATH  = os.path.join(BASE_DIR, "data", "stocks.csv")

# annual_financials.csv 的列顺序（与旧版兼容，新增 source/updated_at/data_warning）
CSV_COLS = [
    "ticker", "year", "revenue", "net_income", "free_cash_flow",
    "roe", "roic", "gross_margin", "net_margin", "debt_to_equity",
    "source", "updated_at", "data_warning",
]

HELP_TEXT = """
fetcher.py — 芒格式选股评分器 v2.0 自动数据抓取

用法：
  python fetcher.py AAPL              抓取单只股票（写入 annual_financials.csv）
  python fetcher.py AAPL MSFT NVDA    抓取多只股票
  python fetcher.py --watchlist       从 stocks.csv 读取全部 ticker 逐个抓取
  python fetcher.py --help            显示帮助

  --valuation 模式（只更新 stocks.csv 的 pe / fcf_yield）：
  python fetcher.py --valuation GOOGL MA COST
  python fetcher.py --valuation --watchlist
  python fetcher.py --valuation --force GOOGL   # 强制覆盖已有值

数据来源：yfinance（Yahoo Finance）
写入位置：data/annual_financials.csv（财务数据）
          data/stocks.csv（仅 --valuation 模式，且只动 pe / fcf_yield 两个字段）

运行完成后，使用 python main.py 进行评分。
"""


# ============================================================
# 工具函数
# ============================================================

def _fv(df, col, *names):
    """
    在 DataFrame 的指定列中，依次尝试 names 里的行索引，返回 float 或 None。
    col 是一个 pd.Timestamp 列。
    """
    if df is None or df.empty:
        return None
    if col not in df.columns:
        return None
    for name in names:
        if name in df.index:
            try:
                v = float(df.loc[name, col])
                if v != v:  # NaN
                    continue
                return v
            except (ValueError, TypeError):
                continue
    return None


def _pct(numerator, denominator):
    """安全计算百分比：numerator/denominator × 100，分母为零返回 None。"""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100


def _valid_num(v):
    """Return True iff v is a non-None, non-NaN finite number."""
    if v is None:
        return False
    try:
        f = float(v)
        return f == f  # NaN != NaN
    except (TypeError, ValueError):
        return False


def _build_warnings(row_dict):
    """
    根据行数据生成 data_warning 字符串。
    列出所有缺失字段和异常值，多条用分号隔开。
    """
    w = []

    # 缺失检查
    for field in ["revenue", "net_income", "free_cash_flow",
                  "roe", "roic", "gross_margin", "net_margin", "debt_to_equity"]:
        v = row_dict.get(field)
        if v is None or v == "":
            w.append(f"缺少{field}")

    # 数值范围检查
    def _chk(field, condition_fn, msg):
        v = row_dict.get(field)
        if v not in (None, ""):
            try:
                if condition_fn(float(v)):
                    w.append(msg)
            except (ValueError, TypeError):
                pass

    _chk("roe",            lambda v: v > 100,  "ROE>100%")
    _chk("roic",           lambda v: v > 100,  "ROIC>100%")
    _chk("gross_margin",   lambda v: v > 100,  "gross_margin>100%")
    _chk("net_margin",     lambda v: v > 100,  "net_margin>100%")
    _chk("debt_to_equity", lambda v: v < 0,    "debt_to_equity<0")
    _chk("debt_to_equity", lambda v: v > 5,    "debt_to_equity>5")

    return "; ".join(w)


# ============================================================
# 核心抓取函数
# ============================================================

def fetch_ticker_yf(ticker):
    """
    用 yfinance 抓取单只股票最近若干年的年度财务数据。

    返回：list of dict（每年一行），供写入 CSV 用。
          如果抓取失败，返回 None。

    yfinance 通常提供最近 4 年数据（偶尔 3 年）。
    财务数据列名以 pd.Timestamp 为列头，按日期降序排列。
    """
    t       = yf.Ticker(ticker)
    today   = date.today().isoformat()
    rows    = []

    # 获取三张财务报表（yfinance 可能返回 None 或空 DataFrame）
    try:
        income   = t.financials       # 年度利润表
        cashflow = t.cashflow         # 年度现金流量表
        balance  = t.balance_sheet    # 年度资产负债表
    except Exception as e:
        raise RuntimeError(f"获取报表失败：{e}") from e

    if income is None or income.empty:
        return None   # 没有利润表，无法继续

    for col in income.columns:
        year     = col.year
        row_dict = {"ticker": ticker, "year": year,
                    "source": "yfinance", "updated_at": today}
        extra_warns = []   # fetcher 额外记录的警告（如"使用估算FCF"）

        # ── 营收 ─────────────────────────────────────────────
        rev = _fv(income, col, "Total Revenue", "Revenue")
        row_dict["revenue"] = "" if rev is None else round(rev / 1e9, 3)

        # ── 净利润 ───────────────────────────────────────────
        ni = _fv(income, col,
                 "Net Income", "Net Income Common Stockholders",
                 "Net Income Including Noncontrolling Interests")
        row_dict["net_income"] = "" if ni is None else round(ni / 1e9, 3)

        # ── 毛利率 ───────────────────────────────────────────
        gp = _fv(income, col, "Gross Profit")
        gm = _pct(gp, rev)
        row_dict["gross_margin"] = "" if gm is None else round(gm, 2)

        # ── 净利率 ───────────────────────────────────────────
        nm = _pct(ni, rev)
        row_dict["net_margin"] = "" if nm is None else round(nm, 2)

        # ── 自由现金流 ───────────────────────────────────────
        fcf = _fv(cashflow, col, "Free Cash Flow")
        if fcf is None and cashflow is not None and not cashflow.empty:
            # 回退：经营现金流 − 资本支出（CapEx 在 yfinance 中通常为负数）
            ocf   = _fv(cashflow, col, "Operating Cash Flow",
                        "Cash Flow From Continuing Operating Activities")
            capex = _fv(cashflow, col, "Capital Expenditure",
                        "Capital Expenditures",
                        "Purchase Of Property Plant And Equipment")
            if ocf is not None and capex is not None:
                fcf = ocf + capex   # capex 是负数，所以相加等于减去
                extra_warns.append("使用OCF-CapEx估算FCF")
        row_dict["free_cash_flow"] = "" if fcf is None else round(fcf / 1e9, 3)

        # ── 股东权益（下面多个指标共用）─────────────────────
        equity = _fv(balance, col,
                     "Stockholders Equity", "Total Stockholder Equity",
                     "Common Stock Equity",
                     "Total Equity Gross Minority Interest")

        # ── ROE ──────────────────────────────────────────────
        roe = _pct(ni, equity)
        row_dict["roe"] = "" if roe is None else round(roe, 2)

        # ── 近似 ROIC：净利润 / (股东权益 + 总负债) ─────────
        # 真实 ROIC 需要 NOPAT 和 Invested Capital，数据来源复杂。
        # 这里用净利润 / (权益 + 负债) 作为简单近似，
        # 并在 data_warning 中标记"使用近似ROIC"。
        total_debt = _fv(balance, col,
                         "Total Debt",
                         "Long Term Debt And Capital Lease Obligation",
                         "Long Term Debt")
        if ni is not None and equity is not None:
            invested = (equity or 0) + (total_debt or 0)
            roic = _pct(ni, invested) if invested != 0 else None
            row_dict["roic"] = "" if roic is None else round(roic, 2)
            extra_warns.append("使用近似ROIC")
        else:
            row_dict["roic"] = ""

        # ── D/E ──────────────────────────────────────────────
        if total_debt is not None and equity is not None and equity != 0:
            row_dict["debt_to_equity"] = round(total_debt / equity, 3)
        else:
            row_dict["debt_to_equity"] = ""

        # ── data_warning ─────────────────────────────────────
        base_warn  = _build_warnings(row_dict)
        all_warns  = [w for w in [base_warn] + extra_warns if w]
        row_dict["data_warning"] = "; ".join(all_warns)

        # 只保留至少有一个关键字段有数据的行。
        # 这样可以避免 yfinance 返回空年份时覆盖已有的手动好数据。
        key_fields = ["revenue", "net_income", "roe", "gross_margin"]
        has_data = any(row_dict.get(f) not in ("", None) for f in key_fields)
        if has_data:
            rows.append(row_dict)
        else:
            print(f"  跳过 {year}：yfinance 未返回有效数据")


    return rows if rows else None


# ============================================================
# --valuation 模式：只更新 stocks.csv 的 pe / fcf_yield
# ============================================================

def fetch_valuation_yf(ticker):
    """
    用 yfinance info 获取单只股票的 PE 和 FCF Yield。

    返回 dict：
        pe            float or None
        pe_source     "trailingPE" / "forwardPE(估算)" / None
        fcf_yield     float or None  （小数，如 0.05 = 5%）
        fcf_yield_src "freeCashflow/marketCap" / None
        warnings      list[str]
    """
    t = yf.Ticker(ticker)
    warnings_out = []

    try:
        info = t.info
    except Exception as e:
        raise RuntimeError(f"yfinance.info 获取失败：{e}") from e

    # ── PE ────────────────────────────────────────────────────
    pe = pe_source = None
    trailing = info.get("trailingPE")
    forward  = info.get("forwardPE")

    if _valid_num(trailing):
        pe        = round(float(trailing), 2)
        pe_source = "trailingPE"
    elif _valid_num(forward):
        pe        = round(float(forward), 2)
        pe_source = "forwardPE(估算)"
        warnings_out.append(f"PE 使用 forwardPE 估算（trailingPE 缺失）")
    else:
        warnings_out.append("PE 无法获取（trailingPE 和 forwardPE 均缺失）")

    # ── FCF Yield = freeCashflow / marketCap ──────────────────
    fcf_yield = fcf_yield_src = None
    fcf = info.get("freeCashflow")
    mkt = info.get("marketCap")

    if _valid_num(fcf) and _valid_num(mkt) and float(mkt) != 0:
        fcf_yield     = round(float(fcf) / float(mkt), 4)
        fcf_yield_src = "freeCashflow/marketCap"
    else:
        if not _valid_num(fcf):
            warnings_out.append("fcf_yield 无法计算（freeCashflow 缺失）")
        elif not _valid_num(mkt) or float(mkt) == 0:
            warnings_out.append("fcf_yield 无法计算（marketCap 缺失或为零）")

    return {
        "pe":            pe,
        "pe_source":     pe_source,
        "fcf_yield":     fcf_yield,
        "fcf_yield_src": fcf_yield_src,
        "warnings":      warnings_out,
    }


def update_stocks_valuation(tickers, force=False):
    """
    对 tickers 逐个调用 fetch_valuation_yf，
    只将 pe / fcf_yield 写回 data/stocks.csv。
    其余字段原封不动。
    force=True 时覆盖已有非空值；否则跳过已有值。
    """
    if not _HAS_YF:
        print("错误：未安装 yfinance。请先运行：pip install yfinance")
        return

    if not os.path.exists(STOCKS_PATH):
        print(f"错误：找不到 {STOCKS_PATH}")
        return

    # ── 读取 stocks.csv ───────────────────────────────────────
    with open(STOCKS_PATH, encoding="utf-8-sig") as f:
        reader     = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        stocks     = list(reader)

    if "pe" not in fieldnames or "fcf_yield" not in fieldnames:
        print("错误：stocks.csv 缺少 pe 或 fcf_yield 列，请检查文件格式。")
        return

    stocks_idx = {row["ticker"].strip().upper(): row for row in stocks}

    # ── 备份 ─────────────────────────────────────────────────
    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = STOCKS_PATH.replace(".csv", f"_backup_{ts}.csv")
    shutil.copy2(STOCKS_PATH, backup_path)
    print(f"已备份 stocks.csv → {backup_path}\n")

    any_written = False

    for raw_ticker in tickers:
        ticker = raw_ticker.strip().upper()
        print(f"[{ticker}] 正在获取估值数据...")

        if ticker not in stocks_idx:
            print(f"  ⚠ {ticker} 不在 stocks.csv 中，跳过\n")
            continue

        try:
            val = fetch_valuation_yf(ticker)
        except Exception as e:
            print(f"  ✗ 获取失败：{e}\n")
            continue

        row            = stocks_idx[ticker]
        updated_fields = []
        skipped_fields = []

        def _should_update(field_name, new_val):
            """判断是否写入：new_val 有效，且（force 或 原值为空/0）。"""
            if new_val is None:
                return False
            existing = str(row.get(field_name, "")).strip()
            has_value = existing and existing not in ("0", "0.0")
            if has_value and not force:
                return False
            return True

        # pe
        if val["pe"] is not None:
            if _should_update("pe", val["pe"]):
                row["pe"]       = str(val["pe"])
                updated_fields.append(f"pe={val['pe']} [{val['pe_source']}]")
                any_written = True
            else:
                skipped_fields.append(f"pe={row['pe']}（已有值，跳过）")

        # fcf_yield
        if val["fcf_yield"] is not None:
            if _should_update("fcf_yield", val["fcf_yield"]):
                row["fcf_yield"] = str(val["fcf_yield"])
                updated_fields.append(
                    f"fcf_yield={val['fcf_yield']} ({val['fcf_yield_src']})"
                )
                any_written = True
            else:
                skipped_fields.append(f"fcf_yield={row['fcf_yield']}（已有值，跳过）")

        # 结果输出
        if updated_fields:
            print(f"  ✓ 已更新：{', '.join(updated_fields)}")
        if skipped_fields:
            print(f"  – 跳过：{', '.join(skipped_fields)}")
        for w in val["warnings"]:
            print(f"  ⚠ {w}")
        if not updated_fields and not val["warnings"]:
            print(f"  – 无需更新（pe / fcf_yield 已有值）")
        print()

    if not any_written:
        print("无字段被更新，stocks.csv 未修改。")
        return

    # ── 写回 stocks.csv ───────────────────────────────────────
    with open(STOCKS_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(stocks)

    print(f"stocks.csv 已写回：{STOCKS_PATH}")
    print("可运行 python manual_review_helper.py 重新检查，或 python main.py 评分。")


# ============================================================
# CSV 读写 / 备份 / 合并
# ============================================================

def load_existing():
    """
    加载现有 annual_financials.csv。
    如果文件不存在或缺少新列，自动补全。
    返回 DataFrame。
    """
    if not os.path.exists(ANNUAL_PATH):
        return pd.DataFrame(columns=CSV_COLS)
    df = pd.read_csv(ANNUAL_PATH, dtype=str)
    for col in ["source", "updated_at", "data_warning"]:
        if col not in df.columns:
            df[col] = ""
    df = df.fillna("")
    return df


def backup_csv():
    """写入前备份现有文件，返回备份路径（文件不存在时返回 None）。"""
    if not os.path.exists(ANNUAL_PATH):
        return None
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = ANNUAL_PATH.replace(".csv", f"_backup_{ts}.csv")
    shutil.copy2(ANNUAL_PATH, dest)
    print(f"  已备份旧文件：{dest}")
    return dest


def upsert_rows(df_existing, new_rows):
    """
    将 new_rows 合并进 df_existing：
    - 相同 (ticker, year) → 更新（覆盖旧值）
    - 新的 (ticker, year) → 追加
    返回合并后的 DataFrame。

    关键：所有值统一转为字符串，避免 pandas 新版本对混合类型报错。
    """
    if not new_rows:
        return df_existing

    # 把所有 new_rows 的值转为字符串（与 CSV 读出的类型保持一致）
    df_new = pd.DataFrame([
        {k: ("" if v is None else str(v)) for k, v in r.items()}
        for r in new_rows
    ])

    # 确保所有列存在
    for col in CSV_COLS:
        if col not in df_new.columns:
            df_new[col] = ""
        if col not in df_existing.columns:
            df_existing[col] = ""

    # 统一类型
    for col in df_new.columns:
        df_new[col] = df_new[col].astype(str)
    for col in df_existing.columns:
        df_existing[col] = df_existing[col].astype(str)

    df_new["year"]      = df_new["year"].str.strip()
    df_existing["year"] = df_existing["year"].str.strip()
    df_new["ticker"]    = df_new["ticker"].str.upper().str.strip()
    df_existing["ticker"] = df_existing["ticker"].str.upper().str.strip()

    if len(df_existing) == 0:
        return df_new

    # 基于 ticker+year 进行行级合并（不依赖 .update()，避免类型问题）
    idx = ["ticker", "year"]
    df_existing = df_existing.set_index(idx)
    df_new      = df_new.set_index(idx)

    # 逐行覆盖（手动 update，避免 pandas dtype 约束）
    for row_idx in df_new.index:
        if row_idx in df_existing.index:
            df_existing.loc[row_idx] = df_new.loc[row_idx]

    # 追加全新的行
    new_only = df_new[~df_new.index.isin(df_existing.index)]
    result   = pd.concat([df_existing, new_only]).reset_index()
    return result


def write_csv(df):
    """排序并写入 annual_financials.csv，确保列顺序正确。"""
    os.makedirs(os.path.dirname(ANNUAL_PATH), exist_ok=True)
    extra = [c for c in df.columns if c not in CSV_COLS]
    df    = df[CSV_COLS + extra]                          # 规范列顺序
    df    = df.sort_values(["ticker", "year"]).reset_index(drop=True)
    df.to_csv(ANNUAL_PATH, index=False, encoding="utf-8")
    print(f"  已写入：{ANNUAL_PATH}（共 {len(df)} 行）")


# ============================================================
# 主入口
# ============================================================

def fetch_and_update(tickers):
    """
    逐个抓取 tickers，合并写入 annual_financials.csv。
    任何单只失败不影响其他股票；全部失败则不修改 CSV。
    """
    if not _HAS_YF:
        print("错误：未安装 yfinance。请先运行：pip install yfinance")
        print("安装后重新运行此命令。")
        return

    df_existing  = load_existing()
    all_new_rows = []
    any_success  = False

    for ticker in tickers:
        print(f"\n正在获取 {ticker} 数据...")
        try:
            rows = fetch_ticker_yf(ticker)
        except Exception as e:
            print(f"  获取失败：{e}")
            print(f"  未修改 annual_financials.csv")
            print(f"  你仍然可以使用旧数据运行 python main.py")
            continue

        if not rows:
            print(f"  获取失败：{ticker} 没有返回数据（可能代码错误或数据源暂时无响应）")
            print(f"  未修改 annual_financials.csv")
            print(f"  你仍然可以使用旧数据运行 python main.py")
            continue

        # 打印成功摘要
        years = sorted(r["year"] for r in rows)
        print(f"  成功获取 {ticker} 最近 {len(rows)} 年年度数据")
        print(f"  更新年份：{', '.join(str(y) for y in years)}")

        # 打印每年的 warning（如果有）
        for r in rows:
            w = r.get("data_warning", "")
            if w:
                print(f"  Warning {r['year']}：{w}")

        all_new_rows.extend(rows)
        any_success = True

    if not any_success:
        print("\n所有股票获取均失败，未修改 annual_financials.csv。")
        print("你仍然可以使用旧数据运行 python main.py")
        return

    # 备份 → 合并 → 写入
    backup_csv()
    df_updated = upsert_rows(df_existing, all_new_rows)
    write_csv(df_updated)
    print("\n完成！现在可以运行 python main.py 进行评分。")


def load_watchlist():
    """从 stocks.csv 读取所有 ticker（只读，绝不修改）。"""
    if not os.path.exists(STOCKS_PATH):
        print(f"错误：找不到 {STOCKS_PATH}")
        return []
    try:
        df      = pd.read_csv(STOCKS_PATH, encoding="utf-8-sig", dtype=str)
        tickers = df["ticker"].dropna().str.strip().str.upper().tolist()
        return [t for t in tickers if t]
    except Exception as e:
        print(f"读取 stocks.csv 失败：{e}")
        return []


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(HELP_TEXT)
        sys.exit(0)

    # ── --valuation 分支：只更新 stocks.csv 的 pe / fcf_yield ──
    if "--valuation" in args:
        force = "--force" in args

        if "--watchlist" in args:
            tickers = load_watchlist()
            if not tickers:
                print("stocks.csv 里没有找到 ticker，退出。")
                sys.exit(1)
            print(f"从 stocks.csv 读取到 {len(tickers)} 只股票：{', '.join(tickers)}\n")
        else:
            tickers = [t.strip().upper() for t in args
                       if not t.startswith("-") and t.strip()]

        if not tickers:
            print("没有指定股票代码。示例：python fetcher.py --valuation GOOGL MA")
            sys.exit(1)

        update_stocks_valuation(tickers, force=force)
        sys.exit(0)

    # ── 普通模式：抓取年度财务数据，写入 annual_financials.csv ──
    if "--watchlist" in args:
        tickers = load_watchlist()
        if not tickers:
            print("stocks.csv 里没有找到 ticker，退出。")
            sys.exit(1)
        print(f"从 stocks.csv 读取到 {len(tickers)} 只股票：{', '.join(tickers)}")
    else:
        tickers = [t.strip().upper() for t in args if not t.startswith("-") and t.strip()]

    if not tickers:
        print("没有指定股票代码，退出。用 --help 查看用法。")
        sys.exit(1)

    fetch_and_update(tickers)
