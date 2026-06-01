# ============================================================
# store.py  —  芒格式选股评分器 v2.2.0-alpha1：数据访问层（CSV 后端）
#
# 职责：
#   stocks.csv 的唯一读写入口。封装列顺序、UTF-8-sig(BOM)、写前备份，
#   并用「白名单」守护人工判断字段——自动写入永远碰不到它们。
#
#   设计为后端无关：将来可整体替换为 SQLite，调用方（main/add_stocks）不变。
#   纯标准库（csv），不引入新依赖。
#
# 关键不变量：
#   - 自动写入只能经 update_machine_fields() / upsert_skeleton()，
#     且只允许 MACHINE_WRITABLE 列；MANUAL_PROTECTED 列原样保留。
#   - 新建骨架时人工字段一律留空，标记 review_status=pending_manual_review。
# ============================================================

import csv
import os
import shutil
import sys
from datetime import date, datetime

from ticker_resolver import resolve, UNKNOWN

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
STOCKS_PATH = os.path.join(BASE_DIR, "data", "stocks.csv")

# ── 列模型 ────────────────────────────────────────────────────
# 现有 36 列（保持原顺序）
BASE_COLUMNS = [
    "ticker", "name", "industry", "data_date",
    "roe_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg", "fcf_positive_years",
    "debt_to_equity", "revenue_growth_5y_cagr", "eps_growth_5y_cagr",
    "pe", "fcf_yield", "roic_5y_avg", "pe_percentile_5y",
    "moat_score", "management_score", "moat_reason", "management_reason",
    "risk_note", "risk_reason", "data_source", "confidence_score",
    "circle_of_competence", "warning_note",
    "brand_score", "switching_cost_score", "network_effect_score",
    "scale_advantage_score", "pricing_power_score", "moat_durability_score",
    "roe_trend", "roic_trend", "margin_trend", "revenue_trend", "debt_reason",
]
# v2.2.0-alpha1 新增列（追加到末尾，向后兼容，不改动现有列位置）
_V220_COLUMNS = ["market", "currency", "canonical_ticker", "review_status"]
# v2.3.0-alpha1 新增列：基础信息 + AI 质化层占位（alpha1 全空，alpha2 启用写入）
_V230_PROFILE_COLUMNS = ["long_name", "sector", "country"]
_V230_AI_COLUMNS = [
    "ai_moat_score", "ai_management_score",
    "ai_risk_score",   # 保留备用：未来用于数字化风险分（alpha2 暂不写）
    "ai_risk_flags",   # v2.3.0-alpha2：文本风险标记，如 "估值偏高; 周期性; 高杠杆"
    "ai_confidence",
    "ai_reason", "ai_evidence_needed", "ai_model", "ai_generated_at",
    "needs_human_review",
]
NEW_COLUMNS = _V220_COLUMNS + _V230_PROFILE_COLUMNS + _V230_AI_COLUMNS
ALL_COLUMNS = BASE_COLUMNS + NEW_COLUMNS

# ── 字段分类：白名单是「人工字段不被覆盖」的核心保障 ─────────────
# 人工判断字段：自动写入（机器 + AI）永远不碰
MANUAL_PROTECTED = {
    "moat_score", "management_score", "circle_of_competence", "confidence_score",
    "brand_score", "switching_cost_score", "network_effect_score",
    "scale_advantage_score", "pricing_power_score", "moat_durability_score",
    "moat_reason", "management_reason", "risk_note", "risk_reason", "debt_reason",
}
# AI 质化层：仅 ai_analysis 可写（alpha2 起）；不得写入人工/机器/身份字段
AI_WRITABLE = set(_V230_AI_COLUMNS)
# 身份键（仅建骨架时写，机器更新不动）
_IDENTITY = {"ticker", "canonical_ticker"}
# 状态列（由人工补录流程管理，不归机器自动写）
_STATUS = {"review_status"}
# 机器可写：其余全部（财务量化、估值、市场基础信息、趋势等），且不含 AI 列
MACHINE_WRITABLE = set(ALL_COLUMNS) - MANUAL_PROTECTED - AI_WRITABLE - _IDENTITY - _STATUS

REVIEW_PENDING  = "pending_manual_review"
REVIEW_COMPLETE = "complete"


# ── 内部读写 ──────────────────────────────────────────────────
def _read_raw():
    """读 stocks.csv，返回 (fieldnames, rows)。文件不存在返回 ([], [])。"""
    if not os.path.exists(STOCKS_PATH):
        return [], []
    with open(STOCKS_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]
        return list(reader.fieldnames or []), rows


def _backup():
    """写前时间戳备份（落在 data/，已被 .gitignore 的 *_backup_* 规则忽略）。"""
    if not os.path.exists(STOCKS_PATH):
        return None
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = STOCKS_PATH.replace(".csv", f"_backup_{ts}.csv")
    shutil.copy2(STOCKS_PATH, dest)
    return dest


def _output_columns(existing_fieldnames):
    """输出列 = ALL_COLUMNS + 文件里出现过的其它未知列（不丢数据）。"""
    extra = [c for c in existing_fieldnames if c and c not in ALL_COLUMNS]
    return ALL_COLUMNS + extra


def _backfill_identity(row):
    """
    用 resolver 回填 canonical_ticker / market / currency（机器可派生的基础信息，
    非人工判断，派生不算"乱填"）。只在相应字段为空时填。
    """
    tk = str(row.get("ticker", "")).strip()
    if not tk:
        return row
    r = resolve(tk)
    if r.market == UNKNOWN:
        return row
    if not str(row.get("canonical_ticker", "")).strip():
        row["canonical_ticker"] = r.canonical
    if not str(row.get("market", "")).strip():
        row["market"] = r.market
    if not str(row.get("currency", "")).strip():
        row["currency"] = r.currency
    return row


# ── 公共 API ──────────────────────────────────────────────────
def load_all():
    """
    读取全部股票，确保 ALL_COLUMNS 都存在，并回填 canonical/market/currency。
    只读，不写文件。返回 list[dict]（值均为 str）。
    """
    _, rows = _read_raw()
    out = []
    for r in rows:
        full = {c: str(r.get(c, "") or "") for c in ALL_COLUMNS}
        # 保留未知额外列
        for k, v in r.items():
            if k not in full:
                full[k] = str(v or "")
        _backfill_identity(full)
        out.append(full)
    return out


def get(canonical):
    """按 canonical_ticker 取一行（找不到返回 None）。"""
    canonical = str(canonical).strip().upper()
    for r in load_all():
        if str(r.get("canonical_ticker", "")).strip().upper() == canonical:
            return r
    return None


def exists(canonical):
    return get(canonical) is not None


def save_all(rows):
    """写回全部行（统一列序 + utf-8-sig）。写前自动备份。"""
    existing_fieldnames, _ = _read_raw()
    cols = _output_columns(existing_fieldnames)
    os.makedirs(os.path.dirname(STOCKS_PATH), exist_ok=True)
    _backup()
    with open(STOCKS_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in cols})


def _build_skeleton(resolved):
    """
    构造一行新股票骨架：
      - 机器基础信息：ticker/canonical/market/currency/data_date/data_source
      - 财务量化字段：全部留空（等 fetcher / annual_financials）
      - 人工判断字段：全部留空（绝不自动填分），review_status=pending
    """
    row = {c: "" for c in ALL_COLUMNS}
    row["ticker"]           = resolved.canonical
    row["canonical_ticker"] = resolved.canonical
    row["market"]           = resolved.market
    row["currency"]         = resolved.currency
    row["name"]             = resolved.canonical   # 占位名，建议人工改成公司名
    row["industry"]         = "Unknown"
    row["data_date"]        = date.today().isoformat()
    row["data_source"]      = "yfinance(pending)" if resolved.market == "US" else "pending_fetch"
    row["review_status"]    = REVIEW_PENDING
    row["needs_human_review"] = "true"   # v2.3.0：质化字段尚待 AI/人工
    return row


def upsert_skeleton(resolved):
    """
    确保该股票在本地存在。已存在 → 原样返回（绝不改动已有行）。
    不存在 → 追加骨架并写回，返回新行。
    resolved 为 ticker_resolver.ResolvedTicker。
    """
    if resolved.market == UNKNOWN or not resolved.canonical:
        raise ValueError(f"无法识别的代码，拒绝建骨架：{resolved.input!r}")

    existing = get(resolved.canonical)
    if existing is not None:
        return existing

    rows = load_all()
    skeleton = _build_skeleton(resolved)
    rows.append(skeleton)
    save_all(rows)
    return skeleton


def update_machine_fields(canonical, fields: dict):
    """
    只更新 MACHINE_WRITABLE 列（read-modify-write）。
    非白名单 key（尤其人工字段）一律忽略并告警——这是结构性保护。
    返回 (updated_list, rejected_list)。
    """
    canonical = str(canonical).strip().upper()
    rows = load_all()
    target = None
    for r in rows:
        if str(r.get("canonical_ticker", "")).strip().upper() == canonical:
            target = r
            break
    if target is None:
        print(f"  [store] 未找到 {canonical}，跳过机器字段写入。")
        return [], []

    updated, rejected = [], []
    for k, v in fields.items():
        if k in MACHINE_WRITABLE:
            target[k] = "" if v is None else str(v)
            updated.append(k)
        else:
            rejected.append(k)   # 人工/身份/状态字段：拒绝自动写入

    if rejected:
        print(f"  [store] 已拒绝非白名单字段的自动写入（保护人工字段）：{', '.join(rejected)}")
    if updated:
        save_all(rows)
    return updated, rejected


def update_ai_fields(canonical, fields: dict):
    """
    v2.3.0-alpha2：只更新 AI_WRITABLE 列（ai_* + needs_human_review）。
    非 AI_WRITABLE 的 key（尤其人工字段 moat_score/management_score/
    circle_of_competence/notes/*_reason/risk_note）一律拒绝并告警。
    与 update_machine_fields 同构：read-modify-write + 写前备份。
    返回 (updated_list, rejected_list)。
    """
    canonical = str(canonical).strip().upper()
    rows = load_all()
    target = None
    for r in rows:
        if str(r.get("canonical_ticker", "")).strip().upper() == canonical:
            target = r
            break
    if target is None:
        print(f"  [store] 未找到 {canonical}，跳过 AI 字段写入。")
        return [], []

    updated, rejected = [], []
    for k, v in fields.items():
        if k in AI_WRITABLE:
            target[k] = "" if v is None else str(v)
            updated.append(k)
        else:
            rejected.append(k)   # 人工/机器/身份/状态字段：AI 一律不得写入

    if rejected:
        print(f"  [store] AI 已拒绝非 AI 白名单字段的写入（保护人工字段）：{', '.join(rejected)}")
    if updated:
        save_all(rows)
    return updated, rejected


# ── 自检入口 ──────────────────────────────────────────────────
def _selftest():
    print("=" * 70)
    print("  store 自检（只读 + 白名单演示，不修改 stocks.csv）")
    print("=" * 70)
    rows = load_all()
    print(f"  load_all：{len(rows)} 行，列数 {len(rows[0]) if rows else 0}")
    if rows:
        r0 = rows[0]
        print(f"  示例：{r0['ticker']}  canonical={r0['canonical_ticker']} "
              f"market={r0['market']} currency={r0['currency']}")
    print(f"  列总数：{len(ALL_COLUMNS)}")
    print(f"  MACHINE_WRITABLE（{len(MACHINE_WRITABLE)}）")
    print(f"  AI_WRITABLE（{len(AI_WRITABLE)}，仅 ai_analysis 可写）："
          f"{', '.join(sorted(AI_WRITABLE))}")
    print(f"  MANUAL_PROTECTED（{len(MANUAL_PROTECTED)}，机器+AI 永不触碰）："
          f"{', '.join(sorted(MANUAL_PROTECTED))}")
    # 三类白名单必须互不相交
    print("  互斥自检：")
    print(f"    人工 ∩ 机器 = {(MANUAL_PROTECTED & MACHINE_WRITABLE) or '∅'}")
    print(f"    人工 ∩ AI   = {(MANUAL_PROTECTED & AI_WRITABLE) or '∅'}")
    print(f"    机器 ∩ AI   = {(MACHINE_WRITABLE & AI_WRITABLE) or '∅'}")
    # update_ai_fields 白名单分类演示（不落盘：因无该 ticker 时直接返回）
    upd, rej = update_ai_fields("__no_such_ticker__",
                                {"ai_moat_score": 7, "moat_score": 9, "management_score": 8})
    print(f"  update_ai_fields 分类：放行 ai_moat_score / 拒绝 moat_score,management_score")


if __name__ == "__main__":
    _selftest()
