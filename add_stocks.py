# ============================================================
# add_stocks.py  —  芒格式选股评分器：添加新股票到 stocks.csv
#
# v2.2.0-alpha1：改为委托 store.upsert_skeleton（经 ticker_resolver），
#   使「手动添加」与「main.py 查询自动添加」走同一套骨架逻辑：
#     - 自动识别市场（US/CN）、规范化代码、写 market/currency
#     - 人工判断字段一律留空，review_status=pending_manual_review（绝不自动填分）
#   财务量化字段留空，由 fetcher.py + annual_financials.csv 自动填充。
#
# 用法：
#   python add_stocks.py GOOGL
#   python add_stocks.py GOOGL MA COST 600519.SH 000001.SZ
# ============================================================

import sys

# Windows 控制台默认非 UTF-8（如 GBK），打印中文会报错，先重配。
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import store
from ticker_resolver import resolve, UNKNOWN

HELP_TEXT = """
add_stocks.py  —  添加新股票到 stocks.csv（v2.2.0-alpha1）

用法：
  python add_stocks.py GOOGL                 添加单只（美股）
  python add_stocks.py GOOGL MA 600519.SH    添加多只（支持 A股代码）

行为：
  - 自动识别市场并规范化代码（如 600519 → 600519.SH）
  - 创建骨架行：market/currency 自动填好，人工判断字段留空待补录
  - 不抓取财务数据；抓取请用 fetcher.py（美股）

添加后建议步骤：
  1. python fetcher.py <美股代码>        自动抓取财务数据
  2. python manual_review_helper.py --template   生成人工补录模板
  3. 填写模板并 --apply-xlsx-template 导入
  4. python main.py                      运行评分
"""


def add_tickers(tickers):
    """识别 + 规范化，对不存在的 ticker 通过 store 建骨架。已存在则跳过。"""
    added, skipped, unknown = [], [], []

    for raw in tickers:
        r = resolve(raw)
        if r.market == UNKNOWN or not r.canonical:
            unknown.append(raw)
            print(f"  [{raw}] 无法识别市场，已跳过（支持：AAPL / BRK-B / 600519 / 600519.SH / 000001.SZ）")
            continue
        if store.exists(r.canonical):
            skipped.append(r.canonical)
            print(f"  [{r.canonical}] 已存在，跳过")
        else:
            store.upsert_skeleton(r)
            added.append(r.canonical)
            print(f"  [{r.canonical}] 已添加骨架（market={r.market} currency={r.currency}，"
                  f"人工字段留空待补录）")

    if not added:
        print("\n没有新增任何股票。")
        return

    print(f"\n已写入 {store.STOCKS_PATH}（新增 {len(added)} 只）")
    print("\n" + "=" * 60)
    print("  这些字段需要后续补充（骨架不会自动乱填）：")
    print("=" * 60)
    print("    财务量化：roe/roic/毛利率/净利率/pe/fcf_yield 等")
    print("              → 美股可运行 fetcher.py 自动抓取")
    print("    人工判断：industry / moat_score / 护城河六项 / management_score /")
    print("              circle_of_competence / confidence_score / 各 reason / risk_note")
    print("              → 运行 manual_review_helper.py --template 用 Excel 补录")
    print()
    us_added = [t for t in added if resolve(t).market == "US"]
    if us_added:
        print(f"  建议先抓美股财务： python fetcher.py {' '.join(us_added)}")
    print(f"  然后运行评分：     python main.py")
    print("=" * 60)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.strip()]

    if not args or "--help" in args or "-h" in args:
        print(HELP_TEXT)
        sys.exit(0)

    tickers = [a for a in args if not a.startswith("-")]
    if not tickers:
        print("没有指定股票代码，用 --help 查看用法。")
        sys.exit(1)

    print(f"正在处理 {len(tickers)} 只：{', '.join(tickers)}\n")
    add_tickers(tickers)
