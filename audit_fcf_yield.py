#!/usr/bin/env python3
# ============================================================
# audit_fcf_yield.py — 只读审计：列出 data/stocks.csv 中疑似「小数量纲」的 fcf_yield 行
#
# 背景：fcf_yield 源头已统一为「百分比」口径（fetcher / ashare_provider 计算结果 *100）。
#       但存量 data/stocks.csv 里历史自动抓取行可能仍是「小数」口径（如 0.0479 实为 4.79%）。
#       本脚本只读扫描、列出候选行，**不修改任何文件**，供人工决定是否做一次性归一。
#
# 判定规则：fcf_yield 可解析且 0 < abs(值) < 1 → 疑似小数量纲（百分比通常 ≥ 1）。
#           （注意：真实低于 1% 的百分比也会落入此区间，故为「候选」而非「确定」，需人工核对。）
#
# 重要：本审计为启发式工具，候选数 > 0 并不代表「归一失败」。归一（×100）后真实
#       低于 1% 的百分比（如 0.61）仍会被列为候选，属正常现象。归一是否成功应以
#       normalize_fcf_yield.py 基于 expected_after 的状态自检为准，而非本脚本候选数为 0。
#
# 用法：python3 audit_fcf_yield.py
# ============================================================

import csv
import os

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
STOCKS_PATH = os.path.join(BASE_DIR, "data", "stocks.csv")


def find_candidates(rows):
    """纯函数：从已解析的行（dict 列表）中找出疑似「小数量纲」的 fcf_yield 候选行。

    判定规则：fcf_yield 可解析且 0 < abs(值) < 1 → 候选。
    返回 [(ticker, raw, data_source, pe), ...]。
    **不打印、不读写文件**，供 audit/normalize 共同复用，避免规则漂移。
    """
    candidates = []
    for r in rows:
        raw = str(r.get("fcf_yield", "")).strip()
        if raw in ("", "None", "nan"):
            continue
        try:
            val = float(raw)
        except (ValueError, TypeError):
            continue
        if 0 < abs(val) < 1:           # 疑似小数量纲
            candidates.append((r.get("ticker", ""), raw,
                               r.get("data_source", ""), r.get("pe", "")))
    return candidates


def main():
    print("=" * 72)
    print("  fcf_yield 量纲审计（只读，不修改任何文件）")
    print("=" * 72)
    if not os.path.exists(STOCKS_PATH):
        print(f"  找不到 {STOCKS_PATH}")
        return

    with open(STOCKS_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    candidates = find_candidates(rows)

    print(f"  扫描 {len(rows)} 行，疑似小数量纲候选 {len(candidates)} 行：\n")
    if candidates:
        print(f"    {'ticker':<12}{'fcf_yield':>12}{'  →×100':>12}   {'pe':>8}  data_source")
        print("    " + "-" * 64)
        for tk, raw, src, pe in candidates:
            pct = f"{float(raw) * 100:.4f}"
            print(f"    {tk:<12}{raw:>12}{pct:>12}   {pe:>8}  {src}")
    else:
        print("    （无候选；所有 fcf_yield 看起来已是百分比口径）")

    print("\n  说明（启发式审计，非确定性判据）：")
    print("    • 规则 0 < abs(fcf_yield) < 1 仅表示「疑似」小数量纲候选，并非确定。")
    print("    • 归一（×100）后，真实低于 1% 的百分比（如 0.61% 存为 0.61）仍会落入该区间，")
    print("      会被继续列为候选，这属正常现象，而非归一失败。")
    print("    • 因此本审计不能作为「归一是否失败」的唯一判据；")
    print("      归一成功与否应以 normalize_fcf_yield.py 的状态自检（expected_after）为准。")
    print("    • 本脚本只读，未写入/未修改任何文件。")


if __name__ == "__main__":
    main()
