# ============================================================
# preflight.py  —  芒格式选股评分器 v2.1.0：运行前基础自检
#
# 职责：
#   在 main.py 正式运行前，检查环境与数据文件是否就绪，
#   把「缺文件 / 缺依赖 / 缺必要列」变成清晰、可操作的提示。
#
# 设计原则：
#   1. 纯只读：绝不修改任何数据文件（连 output/ 也只检测不创建）。
#   2. 不导入 pandas：自检本身要负责检测 pandas 是否缺失，
#      所以只用标准库（csv / importlib），保证缺 pandas 时仍能运行。
#   3. 区分「致命问题」(FATAL) 与「提示」(WARN/INFO)：
#      只有致命问题才阻断评分。
#   4. 缺数据 ≠ 公司差：缺失的人工判断字段只提示，
#      引导用户走 manual_review_helper.py，不在这里下任何负面结论。
#
# 用法：
#   python preflight.py        # 独立运行，打印自检报告
#   （main.py 启动时也会自动调用 run_preflight()）
# ============================================================

import csv
import importlib.util
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 路径 ──────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(BASE_DIR, "data")
OUTPUT_DIR   = os.path.join(BASE_DIR, "output")
STOCKS_PATH  = os.path.join(DATA_DIR, "stocks.csv")
ANNUAL_PATH  = os.path.join(DATA_DIR, "annual_financials.csv")

# ── 必要 / 推荐列 ─────────────────────────────────────────────
# stocks.csv：只有 ticker 是程序运行的硬性前提；
# name / industry 缺失不阻断（main.py 用默认值兜底），只提示。
STOCKS_REQUIRED_COLS    = ["ticker"]
STOCKS_RECOMMENDED_COLS = ["name", "industry"]
# annual_financials.csv：文件可缺失（回退手动数据）；若存在，需有这两列。
ANNUAL_REQUIRED_COLS    = ["ticker", "year"]

# 依赖：pandas 是 main.py 的硬依赖；yfinance / openpyxl 只在
# fetcher.py / 模板导出时才用到，缺失不影响评分本身。
REQUIRED_MODULES = ["pandas"]
OPTIONAL_MODULES = {
    "yfinance": "fetcher.py 自动抓取财务数据时需要",
    "openpyxl": "manual_review_helper.py 生成/导入 Excel 模板时需要",
}

FATAL, WARN, INFO = "FATAL", "WARN", "INFO"
_ICON = {FATAL: "✗", WARN: "⚠", INFO: "·"}


def _module_available(name):
    return importlib.util.find_spec(name) is not None


def _read_header(path):
    """用标准库读 CSV 表头（utf-8-sig 兼容 BOM）。失败返回 None。"""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            return next(reader, [])
    except Exception:
        return None


def run_preflight():
    """
    执行全部自检，返回 (ok: bool, problems: list[tuple[level, msg, fix]])。

    ok 为 False 仅当存在 FATAL 级问题（缺 pandas / 缺 data 目录 /
    缺 stocks.csv / stocks.csv 缺 ticker 列）。其余只作提示。
    """
    problems = []

    def add(level, msg, fix=""):
        problems.append((level, msg, fix))

    # ── 1. 依赖检查 ──────────────────────────────────────────
    for mod in REQUIRED_MODULES:
        if not _module_available(mod):
            add(FATAL, f"缺少必需依赖：{mod}",
                "运行：pip install -r requirements.txt"
                "（或 pip3，Windows 可用 py -m pip install -r requirements.txt）")
    for mod, why in OPTIONAL_MODULES.items():
        if not _module_available(mod):
            add(INFO, f"未安装可选依赖：{mod}（{why}）",
                f"如需该功能：pip install {mod}")

    # ── 2. 目录检查 ──────────────────────────────────────────
    if not os.path.isdir(DATA_DIR):
        add(FATAL, f"找不到数据目录：{DATA_DIR}",
            "请确认在项目根目录运行，且 data/ 目录存在。")
    if not os.path.isdir(OUTPUT_DIR):
        # output/ 会被 main.py 自动创建，仅提示。
        add(INFO, f"output/ 目录不存在（{OUTPUT_DIR}）",
            "无需处理：main.py 保存结果时会自动创建。")

    # ── 3. stocks.csv 检查 ───────────────────────────────────
    if not os.path.isfile(STOCKS_PATH):
        add(FATAL, f"找不到 {STOCKS_PATH}",
            "用 add_stocks.py 创建，例如：python add_stocks.py AAPL MSFT")
    else:
        header = _read_header(STOCKS_PATH)
        if header is None:
            add(FATAL, f"无法读取 {STOCKS_PATH}（文件损坏或编码异常）",
                "请用 UTF-8 编码重新保存，或从备份恢复 data/stocks_backup_*.csv。")
        else:
            cols = set(header)
            for c in STOCKS_REQUIRED_COLS:
                if c not in cols:
                    add(FATAL, f"stocks.csv 缺少必要列：{c}",
                        "表头应包含 ticker 列；可对照 add_stocks.py 生成的列结构。")
            missing_rec = [c for c in STOCKS_RECOMMENDED_COLS if c not in cols]
            if missing_rec:
                add(WARN, f"stocks.csv 缺少推荐列：{', '.join(missing_rec)}",
                    "不影响运行（会用默认值兜底），建议补上以获得完整展示与行业对比。")
            # v2.2.0-alpha1：市场/币种/规范化列缺失只提示（首次写入时自动补全）
            missing_v220 = [c for c in ("market", "currency", "canonical_ticker", "review_status")
                            if c not in cols]
            if missing_v220:
                add(INFO, f"stocks.csv 暂无 v2.2.0 列：{', '.join(missing_v220)}",
                    "无需处理：运行/添加股票时由 store.py 自动补全（向后兼容）。")

    # ── 4. annual_financials.csv 检查（缺失不致命）───────────
    if not os.path.isfile(ANNUAL_PATH):
        add(INFO, "未找到 annual_financials.csv（年度财务数据）",
            "评分将回退到 stocks.csv 的手填财务字段。"
            "如需自动计算：python fetcher.py --watchlist")
    else:
        header = _read_header(ANNUAL_PATH)
        if header is None:
            add(WARN, f"无法读取 {ANNUAL_PATH}（编码异常或损坏）",
                "将回退手动数据；可删除后重新运行 fetcher.py 重建。")
        else:
            missing = [c for c in ANNUAL_REQUIRED_COLS if c not in set(header)]
            if missing:
                add(WARN, f"annual_financials.csv 缺少列：{', '.join(missing)}",
                    "文件可能格式异常；删除后用 fetcher.py --watchlist 重新生成。")

    ok = not any(level == FATAL for level, _, _ in problems)
    return ok, problems


def print_report(problems):
    """把自检结果打印成对齐的报告。无问题时打印一行通过。"""
    if not problems:
        print("  ✓ 自检通过：依赖、目录、数据文件、必要列均就绪。")
        return
    width = 64
    print("  运行前自检报告")
    print("  " + "─" * width)
    for level, msg, fix in problems:
        print(f"  {_ICON[level]} [{level}] {msg}")
        if fix:
            print(f"       → {fix}")
    print("  " + "─" * width)
    n_fatal = sum(1 for lv, _, _ in problems if lv == FATAL)
    if n_fatal:
        print(f"  发现 {n_fatal} 个致命问题，需先解决才能运行评分。")
    else:
        print("  以上为提示，可继续运行；按需处理即可。")


def main():
    print("=" * 70)
    print("   芒格式选股评分器  ·  运行前基础自检  (preflight v2.1.0)")
    print("=" * 70)
    ok, problems = run_preflight()
    print_report(problems)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
