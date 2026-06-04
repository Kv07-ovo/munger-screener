#!/usr/bin/env python3
# ============================================================
# normalize_fcf_yield.py — 一次性归一：把 data/stocks.csv 中已审计出的
#   fcf_yield「小数量纲」候选行 ×100 修正为「百分比」口径。
#
# 安全模型：
#   - 默认 DRY-RUN：只打印 before/after，**不备份、不写入**。
#   - 仅 --apply：先备份，再仅修改 fcf_yield 一列，其余字节原样。
#   - 幂等：若数据已处于归一后状态，则不再写入（也绝不回滚）。
#   - 候选判定唯一来源 = audit_fcf_yield.find_candidates（避免规则漂移）。
#
# 成功判据（不依赖 audit 候选数为 0）：
#   1) 预期 10 个 ticker 都已完成 before → after；
#   2) 每个 ticker 的 fcf_yield == expected_after，且不再等于 old value；
#   3) 其它行不被修改；
#   4) 剩余 audit 候选只允许是「真实低于 1% 的百分比」白名单
#      （GOOGL=0.61 / WMT=0.74 / 300750.SZ=0.92），出现其它 ticker 才报错。
#
# 约束：不联网、不重抓数据、不安装依赖、不创建 commit。
#
# 用法：
#   python3 normalize_fcf_yield.py            # DRY-RUN / 状态自检
#   python3 normalize_fcf_yield.py --apply    # 备份并写入（仅当数据尚未归一时）
# ============================================================

import csv
import os
import sys
import shutil
from datetime import datetime
from decimal import Decimal

from audit_fcf_yield import find_candidates   # import 时不扫描、不打印

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
STOCKS_PATH = os.path.join(BASE_DIR, "data", "stocks.csv")

# 本次一次性归一的「事实表」：(ticker, old_value, expected_after)。
# expected_after 必须等于 old_value ×100（启动时由 _consistency_check 校验）。
EXPECTED = [
    ("GOOGL",     "0.0061",  "0.61"),
    ("MA",        "0.037",   "3.7"),
    ("COST",      "0.0183",  "1.83"),
    ("ADBE",      "0.089",   "8.9"),
    ("CRM",       "0.1058",  "10.58"),
    ("BRK-B",     "0.0598",  "5.98"),
    ("600519.SH", "0.0479",  "4.79"),
    ("WMT",       "0.0074",  "0.74"),
    ("300750.SZ", "0.0092",  "0.92"),
    ("688981.SH", "-0.0367", "-3.67"),
]
EXPECTED_TICKERS = {tk for tk, _old, _aft in EXPECTED}

# 归一后仍 < 1 的真实百分比 → 允许继续作为 audit 候选残留（自动从 EXPECTED 推导）。
ALLOWED_RESIDUAL = {tk: aft for tk, _old, aft in EXPECTED if 0 < abs(float(aft)) < 1}


def to_pct(old_raw):
    """old(str) * 100：用 Decimal 避免浮点误差，去尾零、不用科学计数法。"""
    d = (Decimal(old_raw) * 100).normalize()
    return format(d, "f")


def _consistency_check():
    """确保 expected_after 严格等于 old ×100（把 ×100 规则钉死为唯一来源）。"""
    bad = [(tk, old, aft, to_pct(old)) for tk, old, aft in EXPECTED if to_pct(old) != aft]
    if bad:
        print("  ✗ 内部一致性错误：expected_after 与 old×100 不符：")
        for tk, old, aft, calc in bad:
            print(f"      {tk}: old={old} ×100={calc} 但表内 after={aft}")
        sys.exit(2)


def load_rows():
    """读取 data/stocks.csv（utf-8-sig），返回 dict 行列表。"""
    with open(STOCKS_PATH, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def current_fcf(rows):
    """返回 {ticker: 当前 fcf_yield 字符串}，仅限 EXPECTED 的 10 个 ticker。"""
    out = {}
    for r in rows:
        tk = r.get("ticker", "")
        if tk in EXPECTED_TICKERS:
            out[tk] = str(r.get("fcf_yield", "")).strip()
    return out


def is_normalized(rows):
    """是否所有 10 个 ticker 都已等于 expected_after。"""
    cur = current_fcf(rows)
    return all(cur.get(tk) == aft for tk, _old, aft in EXPECTED)


def print_expected_table():
    """打印 10 个 ticker 的 before → after（来自事实表）。"""
    print(f"  预期归一 {len(EXPECTED)} 行（before → after，仅 fcf_yield 列）：\n")
    print(f"    {'ticker':<12}{'old (before)':>15}{'expected_after':>16}")
    print("    " + "-" * 45)
    for tk, old, aft in EXPECTED:
        print(f"    {tk:<12}{old:>15}{aft:>16}")


def print_preview(candidates):
    """（未归一时）打印当前候选行 before → after。"""
    print(f"  候选 {len(candidates)} 行（before → after，仅 fcf_yield 列）：\n")
    print(f"    {'ticker':<12}{'old_fcf_yield':>15}{'new_fcf_yield':>15}   data_source")
    print("    " + "-" * 64)
    for tk, raw, src, _pe in candidates:
        print(f"    {tk:<12}{raw:>15}{to_pct(raw):>15}   {src}")


def validate_candidates(candidates):
    """（未归一时）写前校验：候选必须恰好是预期的 10 个 ticker。返回错误列表。"""
    tickers = [c[0] for c in candidates]
    tset = set(tickers)
    errors = []
    if len(candidates) != 10:
        errors.append(f"候选数量为 {len(candidates)}，期望恰好 10")
    if len(tickers) != len(tset):
        dup = sorted({t for t in tickers if tickers.count(t) > 1})
        errors.append(f"候选 ticker 存在重复：{dup}")
    if tset != EXPECTED_TICKERS:
        missing = sorted(EXPECTED_TICKERS - tset)
        extra   = sorted(tset - EXPECTED_TICKERS)
        if missing:
            errors.append(f"缺少预期 ticker：{missing}")
        if extra:
            errors.append(f"出现未预期 ticker：{extra}")
    return errors


def verify_success(rows):
    """成功判据 1~3：10 个 ticker 均 == expected_after 且 != old。返回错误列表。"""
    cur = current_fcf(rows)
    errors = []
    for tk, old, aft in EXPECTED:
        if tk not in cur:
            errors.append(f"{tk}: 表中找不到该行")
            continue
        v = cur[tk]
        if v != aft:
            errors.append(f"{tk}: 期望 after={aft}，实际={v}")
        elif v == old:
            errors.append(f"{tk}: 仍等于归一前旧值 {old}")
    return errors


def check_residual(rows):
    """成功判据 4：剩余 audit 候选必须 ⊆ 白名单，且值一致。返回 (candidates, errors)。"""
    candidates = find_candidates(rows)
    errors = []
    for tk, raw, _src, _pe in candidates:
        if tk not in ALLOWED_RESIDUAL:
            errors.append(f"非预期残留候选：{tk}={raw}")
        elif raw != ALLOWED_RESIDUAL[tk]:
            errors.append(f"{tk} 残留值={raw}，期望白名单值={ALLOWED_RESIDUAL[tk]}")
    return candidates, errors


def backup():
    """备份原文件到 data/stocks_backup_before_fcf_normalize_<时间戳>.csv（被 .gitignore 忽略）。"""
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BASE_DIR, "data", f"stocks_backup_before_fcf_normalize_{ts}.csv")
    shutil.copy2(STOCKS_PATH, dst)
    return dst


def apply_changes(new_values):
    """外科式逐行替换：仅替换候选 ticker 行的 fcf_yield 列，其余字节原样。

    保留 BOM 与 CRLF；不做整表 csv 重写，避免改动无关单元格。
    new_values: dict ticker -> new_fcf_yield(str)。返回 [(ticker, old, new), ...]。
    """
    with open(STOCKS_PATH, "rb") as f:
        data = f.read()

    has_bom = data.startswith(b"\xef\xbb\xbf")
    body    = data[3:] if has_bom else data
    text    = body.decode("utf-8")
    lines   = text.split("\r\n")          # 保留尾部空串 → 还原末尾换行

    header     = lines[0].split(",")
    ticker_idx = header.index("ticker")
    fcf_idx    = header.index("fcf_yield")

    changed = []
    for i in range(1, len(lines)):
        line = lines[i]
        if line == "":
            continue
        fields = line.split(",")
        if len(fields) <= fcf_idx:
            continue
        tk = fields[ticker_idx]
        if tk in new_values:
            old = fields[fcf_idx]
            fields[fcf_idx] = new_values[tk]
            lines[i] = ",".join(fields)
            changed.append((tk, old, new_values[tk]))

    out = "\r\n".join(lines).encode("utf-8")
    if has_bom:
        out = b"\xef\xbb\xbf" + out
    with open(STOCKS_PATH, "wb") as f:
        f.write(out)
    return changed


def self_check_report(rows):
    """统一的状态自检与报告。返回 exit code（0=通过）。"""
    print("\n  —— 状态自检（基于 expected_after，不依赖 audit 候选数为 0）——")

    s_errors = verify_success(rows)
    if s_errors:
        print("  ✗ 成功判据未通过：")
        for e in s_errors:
            print(f"      - {e}")
    else:
        print("  ✓ 10 个 ticker 均 == expected_after，且不再等于旧值。")

    candidates, r_errors = check_residual(rows)
    res = sorted({c[0] for c in candidates})
    print(f"  剩余 audit 候选：{res if res else '（无）'}")
    print(f"  允许残留白名单：{sorted(ALLOWED_RESIDUAL)}（均为真实 <1% 的百分比）")
    if r_errors:
        print("  ✗ 残留候选异常：")
        for e in r_errors:
            print(f"      - {e}")
    else:
        print("  ✓ 残留候选未超出白名单。")

    if s_errors or r_errors:
        print("\n  ✗ 自检未通过（exit 1）。")
        return 1
    print("\n  ✓ 自检通过（exit 0）。")
    return 0


def main():
    apply_mode = "--apply" in sys.argv[1:]
    mode = "APPLY 写入模式" if apply_mode else "DRY-RUN 预览模式（不备份、不写入）"
    print("=" * 72)
    print(f"  fcf_yield 一次性归一  —  {mode}")
    print("=" * 72)

    _consistency_check()
    rows = load_rows()
    print(f"  扫描 {len(rows)} 行。\n")

    if is_normalized(rows):
        # 幂等：已归一 → 不再写入、不回滚，仅做状态自检。
        print("  状态：10 个目标 ticker 已处于归一后状态（before → after 均已完成）。")
        print("  幂等：无需再次写入，未备份、未修改、未回滚任何文件。\n")
        print_expected_table()
        sys.exit(self_check_report(rows))

    # ---- 尚未归一 ----
    candidates = find_candidates(rows)
    print_preview(candidates)
    v_errors = validate_candidates(candidates)
    if v_errors:
        print("\n  ✗ 写前候选校验未通过，立即中止（不备份、不写入）：")
        for e in v_errors:
            print(f"      - {e}")
        sys.exit(1)
    print("\n  ✓ 写前候选校验通过：恰好 10 个，且 ticker 集合与预期完全一致。")

    if not apply_mode:
        print("\n  这是 DRY-RUN：未备份、未写入任何文件。")
        print("  确认无误后运行： python3 normalize_fcf_yield.py --apply")
        return

    # ---- 仅 --apply 写入 ----
    new_values = {tk: to_pct(raw) for tk, raw, _src, _pe in candidates}
    bpath = backup()
    print(f"\n  已备份原文件 → {bpath}")
    changed = apply_changes(new_values)
    print(f"  已修改 {len(changed)} 行（仅 fcf_yield 列）：")
    for tk, old, new in changed:
        print(f"      {tk:<12} {old:>12} → {new}")

    rows = load_rows()
    sys.exit(self_check_report(rows))


if __name__ == "__main__":
    main()
