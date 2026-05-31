# ============================================================
# add_stocks.py  —  芒格式选股评分器：添加新股票到 stocks.csv
#
# 职责：
#   把新 ticker 写入 data/stocks.csv，让 main.py 能对它评分。
#   财务数字留空，由 fetcher.py + annual_financials.csv 自动填充。
#   护城河、管理层、能力圈等判断字段需要用户手动补充。
#
# 用法：
#   python add_stocks.py GOOGL
#   python add_stocks.py GOOGL MA COST ADBE CRM JPM BRK-B
#
# 流程建议：
#   1. python add_stocks.py GOOGL MA COST   # 先添加股票框架
#   2. python fetcher.py GOOGL MA COST      # 自动抓取财务数据
#   3. 手动编辑 data/stocks.csv，补充行业/护城河/管理层/能力圈
#   4. python main.py                       # 运行评分
# ============================================================

import os
import sys
from datetime import date

# v2.1.0：Windows 控制台默认非 UTF-8（如 GBK），打印中文会报错，先重配。
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
STOCKS_PATH = os.path.join(BASE_DIR, "data", "stocks.csv")

# ── 新股票的默认值 ──────────────────────────────────────────
# 财务数字全部留空（由 fetcher.py 通过 annual_financials.csv 填充）
# 人工判断字段填最保守的默认值，提醒用户手动完善
_DEFAULTS = {
    "name":                   "",        # 默认等于 ticker，用户应改成中文名
    "industry":               "Unknown", # 用户必填
    "data_date":              "",        # 写入时填当天日期
    # ── 财务字段全部留空，等 fetcher.py 填充 ──────────────
    "roe_5y_avg":             "",
    "gross_margin_5y_avg":    "",
    "net_margin_5y_avg":      "",
    "fcf_positive_years":     "",
    "debt_to_equity":         "",
    "revenue_growth_5y_cagr": "",
    "eps_growth_5y_cagr":     "",
    "pe":                     "",        # 市场数据，用户手填
    "fcf_yield":              "",        # 市场数据，用户手填
    "roic_5y_avg":            "",
    "pe_percentile_5y":       "",        # 市场数据，用户手填
    # ── 护城河（用户必须手动打分）─────────────────────────
    "moat_score":             "0",
    "management_score":       "0",
    "moat_reason":            "",
    "management_reason":      "",
    "risk_note":              "",
    "risk_reason":            "",
    # ── 元数据 ──────────────────────────────────────────────
    "data_source":            "manual_pending",
    "confidence_score":       "6",
    "circle_of_competence":   "edge",
    "warning_note":           "",
    # ── 护城河拆分（默认 0，用户手动打分）──────────────────
    "brand_score":            "0",
    "switching_cost_score":   "0",
    "network_effect_score":   "0",
    "scale_advantage_score":  "0",
    "pricing_power_score":    "0",
    "moat_durability_score":  "0",
    # ── 趋势（fetcher.py 会自动覆盖）──────────────────────
    "roe_trend":              "stable",
    "roic_trend":             "stable",
    "margin_trend":           "stable",
    "revenue_trend":          "stable",
    "debt_reason":            "",
}

HELP_TEXT = """
add_stocks.py  —  添加新股票到 stocks.csv

用法：
  python add_stocks.py GOOGL              添加单只
  python add_stocks.py GOOGL MA COST      添加多只

添加后建议步骤：
  1. python fetcher.py GOOGL MA COST      自动抓取财务数据
  2. 手动编辑 data/stocks.csv，补充：
       industry        行业
       moat_score      护城河总分（0-10）
       brand_score     品牌强度（0-10）
       switching_cost_score  转换成本（0-10）
       network_effect_score  网络效应（0-10）
       scale_advantage_score 规模优势（0-10）
       pricing_power_score   定价权（0-10）
       moat_durability_score 护城河持续性（0-10）
       moat_reason     护城河理由
       management_score  管理层评分（0-10）
       management_reason 管理层理由
       circle_of_competence  inside / edge / outside
       confidence_score  数据可信度（0-10）
       risk_note       风险标记关键词
       risk_reason     风险说明
       pe / fcf_yield / pe_percentile_5y  当前市场估值数据
  3. python main.py                       运行评分
"""


def load_csv():
    """读取 stocks.csv，返回 DataFrame。文件不存在时返回空 DataFrame。"""
    if not os.path.exists(STOCKS_PATH):
        print(f"  未找到 {STOCKS_PATH}，将创建新文件。")
        return pd.DataFrame()
    return pd.read_csv(STOCKS_PATH, dtype=str).fillna("")


def build_new_row(ticker):
    """根据 ticker 构建一行新股票数据，所有默认值来自 _DEFAULTS。"""
    row = dict(_DEFAULTS)
    row["ticker"]    = ticker.upper()
    row["name"]      = ticker.upper()   # 默认名等于代码，提醒用户修改
    row["data_date"] = date.today().isoformat()
    return row


def add_tickers(tickers):
    """
    把 tickers 列表中不存在的股票添加到 stocks.csv。

    逻辑：
        1. 读取现有 CSV
        2. 找出哪些 ticker 已存在（不区分大小写）
        3. 对新 ticker 生成默认行
        4. 追加并写回 CSV
        5. 打印提示，告知用户哪些字段需要手动补充
    """
    df_existing  = load_csv()
    today        = date.today().isoformat()

    # 现有 ticker 集合（大写，方便比对）
    existing_set = set(df_existing["ticker"].str.upper().tolist()) if len(df_existing) else set()

    added   = []
    skipped = []

    for raw in tickers:
        ticker = raw.strip().upper()
        if not ticker:
            continue
        if ticker in existing_set:
            skipped.append(ticker)
            print(f"  [{ticker}] 已存在，跳过")
        else:
            added.append(ticker)
            print(f"  [{ticker}] 已添加（默认值）")

    if not added:
        print("\n没有新增任何股票。")
        return

    # 构建新行 DataFrame
    new_rows = [build_new_row(t) for t in added]
    df_new   = pd.DataFrame(new_rows)

    # 确保列顺序与现有 CSV 一致（新列追加到末尾）
    if len(df_existing) > 0:
        # 先对齐列：现有列保留，df_new 中新增的列追加
        for col in df_existing.columns:
            if col not in df_new.columns:
                df_new[col] = ""
        df_new = df_new[df_existing.columns.tolist() +
                        [c for c in df_new.columns if c not in df_existing.columns]]
        df_out = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        # CSV 不存在，直接用新行（列顺序由 _DEFAULTS 决定）
        df_out = df_new

    # 写回，保留 UTF-8-sig 编码（Excel 友好）
    df_out.to_csv(STOCKS_PATH, index=False, encoding="utf-8-sig")
    print(f"\n已写入 {STOCKS_PATH}（新增 {len(added)} 只，共 {len(df_out)} 只）")

    # 提示用户需要手动补充的字段
    print("\n" + "=" * 60)
    print("  接下来请手动补充以下判断字段（打开 data/stocks.csv 编辑）")
    print("=" * 60)
    fields_to_fill = [
        ("name",                 "公司中文名或全称"),
        ("industry",             "行业（当前为 Unknown）"),
        ("moat_score",           "护城河总分 0-10"),
        ("brand/switching/..",   "护城河六项拆分分数 0-10"),
        ("moat_reason",          "护城河理由（文字描述）"),
        ("management_score",     "管理层评分 0-10"),
        ("management_reason",    "管理层理由"),
        ("circle_of_competence", "能力圈：inside / edge / outside"),
        ("confidence_score",     "数据可信度 0-10（默认 6）"),
        ("risk_note",            "风险关键词（fraud/high valuation/policy risk/debt）"),
        ("risk_reason",          "风险说明"),
        ("pe / fcf_yield",       "当前市场估值数据（需手动查询）"),
        ("pe_percentile_5y",     "PE 5年历史分位（需手动查询）"),
    ]
    for field, desc in fields_to_fill:
        print(f"    {field:<26} {desc}")

    print("\n  建议步骤：")
    print(f"    1. python fetcher.py {' '.join(added)}")
    print(f"       → 自动抓取财务数据，写入 annual_financials.csv")
    print(f"    2. 编辑 data/stocks.csv，补充上面的判断字段")
    print(f"    3. python main.py → 运行完整评分")
    print("=" * 60)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.strip()]

    if not args or "--help" in args or "-h" in args:
        print(HELP_TEXT)
        sys.exit(0)

    tickers = [a.upper() for a in args if not a.startswith("-")]
    if not tickers:
        print("没有指定股票代码，用 --help 查看用法。")
        sys.exit(1)

    print(f"正在处理 {len(tickers)} 只股票：{', '.join(tickers)}\n")
    add_tickers(tickers)
