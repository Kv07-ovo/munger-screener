# ============================================================
# providers/ashare_provider.py  —  A股数据源（AKShare）  v2.4.0
#
# 职责：唯一调用 AKShare 的地方。把 A股基础信息/估值/年度财务
#       映射成与项目兼容的结构，写入 annual_financials.csv 与
#       stocks.csv（经 store 白名单）。
#
# 原则：
#   1. AKShare 是 optional dependency：未安装时 is_available()=False，
#      上层回退"数据不足，待补录"，绝不崩。
#   2. 绝不用假数据填缺失项：拿不到就留空 → 进入 missing_fields。
#   3. 只写机器字段（经 store.update_machine_fields 白名单），
#      绝不碰 moat_score/management_score/circle_of_competence/notes 等人工字段。
#   4. 银行/保险/券商等金融类：不强行计算普通企业的 D/E（留空），
#      由 validator 行业豁免处理 FCF Yield/毛利率。
#   5. 不接券商交易、不下单、不输出买卖建议。
#
# 单位约定（与美股一致）：revenue/net_income → 十亿(1e9)；ROE/利润率 → %；
#                        market_cap → 亿元(1e8)；pe/pb → 比值。
# ============================================================

import sys
from dataclasses import dataclass, field
from datetime import date

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCE = "AKShare"
# A股数据口径版本：每次扩展/调整 A股字段抓取口径时 +1。
# stocks.csv 的 data_rev 低于此值（或空/非数字）视为旧数据，需重新抓取。
ASHARE_DATA_REV = 1
# 金融类行业关键词：不套用普通企业 D/E / FCF / 毛利率
_FINANCIAL_KW = ("银行", "保险", "证券", "券商", "资本市场", "信托")


# ── AKShare 可用性（optional dependency）─────────────────────
def _akshare():
    try:
        import akshare as ak
        return ak
    except Exception:
        return None


def is_available():
    return _akshare() is not None


@dataclass
class AShareResult:
    canonical: str
    profile: dict = field(default_factory=dict)     # name/long_name/industry/sector/market/currency
    valuation: dict = field(default_factory=dict)   # pe/pb/market_cap
    annual_rows: list = field(default_factory=list)  # annual_financials.csv 行
    missing_fields: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    source: str = SOURCE
    ok: bool = False


# ── 工具 ──────────────────────────────────────────────────────
def _symbol6(canonical):
    """canonical(600519.SH) → AKShare 6 位代码(600519)。"""
    return str(canonical).split(".")[0].strip()


def _is_financial(industry):
    return any(k in str(industry) for k in _FINANCIAL_KW)


def _num(x):
    """转 float；失败/空返回 None。"""
    try:
        if x is None:
            return None
        s = str(x).replace(",", "").replace("%", "").strip()
        if s in ("", "--", "nan", "None", "-"):
            return None
        v = float(s)
        return None if v != v else v
    except (ValueError, TypeError):
        return None


def _annual_date_cols(columns):
    """从 stock_financial_abstract 宽表里挑出年报列（YYYYMMDD/ YYYY-MM-DD 且 1231 结尾）。"""
    out = []
    for c in columns:
        s = str(c).replace("-", "")
        if len(s) >= 8 and s[:8].isdigit() and s[4:8] == "1231":
            out.append(c)
    return out


def _find_metric_col(df):
    """找到含中文指标名的列（stock_financial_abstract 通常是 '指标'）。"""
    for cand in ("指标", "项目", "选项"):
        if cand in df.columns:
            return cand
    return df.columns[0] if len(df.columns) else None


def _row_value(df, metric_col, date_col, *keywords):
    """在指标列里找第一个含任一 keyword 的行，返回该年报列的数值。"""
    for _, r in df.iterrows():
        name = str(r.get(metric_col, ""))
        if any(k in name for k in keywords):
            v = _num(r.get(date_col))
            if v is not None:
                return v
    return None


def parse_abstract(df, canonical, is_financial, today=None):
    """
    纯解析（可离线单测）：把 stock_financial_abstract 宽表解析成 annual_financials 行。
    v2.4.1 补算（仅用真实 AKShare 字段，不伪造）：
      - debt_to_equity = 资产负债率/(1-资产负债率)（仅非金融；金融类留空）
      - free_cash_flow = 每股企业自由现金流量 × 推算股本(经营现金流净额/每股经营现金流)
                         （AKShare 已给的企业自由现金流≈OCF-资本支出；仅非金融）
      - ROIC：无可靠口径 → 一律留空（不乱算）
    任一字段不可靠则留空，并写入 data_warning。
    """
    today = today or date.today().isoformat()
    if df is None or len(df) == 0:
        return []
    mc = _find_metric_col(df)
    date_cols = _annual_date_cols(df.columns)
    rows = []
    for dc in sorted(date_cols)[-5:]:        # 最近 5 个年报
        ys = str(dc).replace("-", "")[:4]
        if not ys.isdigit():
            continue
        rev    = _row_value(df, mc, dc, "营业总收入", "营业收入")
        ni     = _row_value(df, mc, dc, "归母净利润", "净利润")
        roe    = _row_value(df, mc, dc, "净资产收益率(ROE)", "净资产收益率")
        cost   = _row_value(df, mc, dc, "营业成本")
        ocf    = _row_value(df, mc, dc, "经营现金流量净额")
        ocf_ps = _row_value(df, mc, dc, "每股经营现金流")
        fcf_ps = _row_value(df, mc, dc, "每股企业自由现金流量")
        dar    = _row_value(df, mc, dc, "资产负债率")

        gm = round((rev - cost) / rev * 100, 2) if (rev and cost is not None and rev != 0) else ""
        nm = round(ni / rev * 100, 2) if (rev and ni is not None and rev != 0) else ""

        warns = ["ROIC缺失(AKShare无稳定口径)"]

        # ── debt_to_equity（非金融，从资产负债率换算）──────────
        de = ""
        if is_financial:
            warns.append("金融类:D/E不适用")
        elif dar is not None and 0 <= dar < 100:
            de = round((dar / 100) / (1 - dar / 100), 3)
        else:
            warns.append("资产负债率缺失/异常,D/E留空")

        # ── free_cash_flow（非金融，每股企业自由现金流量×推算股本）─
        fcf = ""
        if is_financial:
            warns.append("金融类:FCF不适用")
        else:
            shares = (ocf / ocf_ps) if (ocf is not None and ocf_ps not in (None, 0)) else None
            if fcf_ps is not None and shares and shares > 0:
                fcf = round(fcf_ps * shares / 1e9, 3)   # 十亿
            else:
                warns.append("FCF不可靠,留空")

        rows.append({
            "ticker": canonical,
            "year": ys,
            "revenue":        "" if rev is None else round(rev / 1e9, 3),
            "net_income":     "" if ni  is None else round(ni / 1e9, 3),
            "free_cash_flow": fcf,
            "roe":            "" if roe is None else round(roe, 2),
            "roic":           "",                      # 无可靠口径 → 留空
            "gross_margin":   gm,
            "net_margin":     nm,
            "debt_to_equity": de,
            "source": SOURCE,
            "updated_at": today,
            "data_warning": "; ".join(warns),
        })
    return rows


def parse_profile(info_df):
    """纯解析 stock_individual_info_em 的 [item,value] 表 → profile/valuation 片段。"""
    out = {"name": "", "industry": "", "market_cap": ""}
    if info_df is None or len(info_df) == 0:
        return out
    cols = list(info_df.columns)
    kc, vc = (cols[0], cols[1]) if len(cols) >= 2 else (None, None)
    if kc is None:
        return out
    for _, r in info_df.iterrows():
        k = str(r.get(kc, "")); v = r.get(vc, "")
        if "简称" in k:
            out["name"] = str(v).strip()
        elif "行业" in k:
            out["industry"] = str(v).strip()
        elif "总市值" in k:
            mv = _num(v)
            if mv is not None:
                out["market_cap"] = round(mv / 1e8, 2)   # 亿元
    return out


# ── 抓取（调用 AKShare，逐项 try/except 兜底）──────────────────
def fetch(canonical):
    ak = _akshare()
    res = AShareResult(canonical=canonical)
    if ak is None:
        res.warnings.append("akshare 未安装")
        return res

    sym = _symbol6(canonical)

    # 1) 基础信息
    industry = ""
    try:
        _info_fn = getattr(ak, "stock_individual_info_em", None)
        info = _info_fn(symbol=sym) if _info_fn else None
        prof = parse_profile(info)
        industry = prof.get("industry", "")
        res.profile.update({
            "name": prof.get("name", ""),
            "long_name": prof.get("name", ""),
            "industry": industry,
            "sector": industry,
            "market": "CN",
            "currency": "CNY",
            "data_source": SOURCE,
        })
        if prof.get("market_cap") not in ("", None):
            res.valuation["market_cap"] = prof["market_cap"]
    except Exception as e:
        res.warnings.append(f"基础信息抓取失败:{e}")

    # 2) 估值 PE(TTM)/PB/市值（百度源，date,value 时序取最新；市值单位为亿元）
    vfn = getattr(ak, "stock_zh_valuation_baidu", None)
    if vfn:
        def _latest(indicator):
            try:
                d = vfn(symbol=sym, indicator=indicator, period="近一年")
                if d is not None and len(d):
                    return _num(d.iloc[-1].get("value"))
            except Exception as e:
                res.warnings.append(f"估值[{indicator}]失败:{str(e)[:40]}")
            return None
        pe = _latest("市盈率(TTM)")
        pb = _latest("市净率")
        mv = _latest("总市值")
        if pe is not None: res.valuation["pe"]         = round(pe, 2)
        if pb is not None: res.valuation["pb"]         = round(pb, 2)
        if mv is not None: res.valuation["market_cap"] = round(mv, 2)   # 亿元
    else:
        res.warnings.append("估值函数 stock_zh_valuation_baidu 不可用")

    # 行业判定（带 ticker 兜底：沙箱/网络受限时 AKShare 行业可能为空）
    try:
        from validator import effective_industry
        eff_ind = effective_industry({"ticker": canonical, "industry": industry})
    except Exception:
        eff_ind = industry
    is_fin = _is_financial(eff_ind)

    # 3) 年度财务（补算 D/E、FCF）
    try:
        _abs_fn = getattr(ak, "stock_financial_abstract", None)
        abs_df  = _abs_fn(symbol=sym) if _abs_fn else None
        res.annual_rows = parse_abstract(abs_df, canonical, is_fin)
    except Exception as e:
        res.warnings.append(f"年度财务抓取失败:{e}")

    # 4) fcf_yield = 最新年 FCF / market_cap（单位换算；仅非金融且两者可靠）
    if not is_fin and res.annual_rows and res.valuation.get("market_cap"):
        latest_fcf = res.annual_rows[-1].get("free_cash_flow", "")
        mcap = res.valuation.get("market_cap")
        if latest_fcf not in ("", None) and mcap:
            try:
                # FCF 十亿(1e9) / 市值 亿(1e8) → *10
                res.valuation["fcf_yield"] = round(float(latest_fcf) * 10 / float(mcap) * 100, 4)
            except (ValueError, ZeroDivisionError):
                pass

    # 5) 缺失项（据实，不造假）
    has_de  = any(r.get("debt_to_equity")  not in ("", None) for r in res.annual_rows)
    has_fcf = any(r.get("free_cash_flow")   not in ("", None) for r in res.annual_rows)
    res.missing_fields = ["roic_5y_avg(无可靠口径)"]
    if not has_de:
        res.missing_fields.append("debt_to_equity" + ("(金融类不适用)" if is_fin else ""))
    if not has_fcf:
        res.missing_fields.append("free_cash_flow/fcf_yield" + ("(金融类不适用)" if is_fin else ""))
    if not res.annual_rows:
        res.missing_fields.append("annual_financials(年度财务未取到)")

    res.ok = bool(res.profile or res.annual_rows or res.valuation)
    return res


# ── 落库（写 annual_financials + stocks.csv，均走既有写入层）──
def ingest(canonical, exchange=None):
    """
    抓取并写入。返回摘要 dict。所有 stocks.csv 写入经 store 白名单。
    失败/部分失败不崩，缺失留空。
    """
    res = fetch(canonical)
    summary = {"ok": res.ok, "source": SOURCE, "written": [], "missing": res.missing_fields,
               "warnings": res.warnings, "annual_years": len(res.annual_rows)}
    if not res.ok:
        print(f"  [AKShare] {canonical} 未获取到有效数据，保留骨架（待补录）。"
              f" 警告：{'; '.join(res.warnings) or '无'}")
        return summary

    # 1) 年度财务 → annual_financials.csv（复用 fetcher 的合并/写入层）
    if res.annual_rows:
        try:
            import fetcher
            df_existing = fetcher.load_existing()
            fetcher.backup_csv()
            df_updated = fetcher.upsert_rows(df_existing, res.annual_rows)
            fetcher.write_csv(df_updated)
            summary["written"].append(f"annual_financials({len(res.annual_rows)}年)")
        except Exception as e:
            print(f"  ⚠ 年度财务写入失败（{e}）。")

    # 2) 基础信息 + 估值 → stocks.csv（经 store 白名单，绝不碰人工字段）
    try:
        import store
        # 已从 AKShare 取得数据 → 标注来源 + 打数据口径版本戳（供刷新判定）
        fields = {"data_source": SOURCE, "data_rev": ASHARE_DATA_REV}
        for k in ("name", "long_name", "industry", "sector", "market", "currency"):
            if res.profile.get(k):
                fields[k] = res.profile[k]
        for k in ("pe", "pb", "market_cap", "fcf_yield"):
            if res.valuation.get(k) not in (None, ""):
                fields[k] = res.valuation[k]
        if fields:
            store.update_machine_fields(canonical, fields)
            summary["written"].append("stocks.csv:" + ",".join(fields.keys()))
    except Exception as e:
        print(f"  ⚠ 基础信息/估值写入失败（{e}）。")

    print(f"  [AKShare] {canonical} 已写入：{', '.join(summary['written']) or '无'}")
    print(f"  [AKShare] 缺失（待补录，非公司差）：{', '.join(res.missing_fields)}")
    if res.warnings:
        print(f"  [AKShare] 提示：{'; '.join(res.warnings)}")
    return summary


# ── 自检入口 ──────────────────────────────────────────────────
def _selftest():
    print("=" * 70)
    print("  ashare_provider 自检")
    print("=" * 70)
    print(f"  AKShare 可用: {is_available()}")
    # 离线纯解析自检（不依赖网络）
    try:
        import pandas as pd
        demo = pd.DataFrame({
            "指标": ["营业总收入", "归母净利润", "营业成本", "净资产收益率(ROE)",
                     "资产负债率", "经营现金流量净额", "每股经营现金流", "每股企业自由现金流量"],
            "20231231": [1000_0000_0000, 500_0000_0000, 300_0000_0000, 31.5,
                         16.42, 615_0000_0000, 49.13, 61.26],
            "20221231": [900_0000_0000, 450_0000_0000, 280_0000_0000, 30.0,
                         18.0, 550_0000_0000, 44.0, 55.0],
        })
        rows = parse_abstract(demo, "600519.SH", is_financial=False)
        print(f"  离线解析样例（600519.SH）：{len(rows)} 年")
        for r in rows:
            print(f"    {r['year']}: rev={r['revenue']}十亿 ni={r['net_income']}十亿 "
                  f"roe={r['roe']}% gm={r['gross_margin']}% nm={r['net_margin']}% "
                  f"roic={r['roic']!r} fcf={r['free_cash_flow']!r}")
    except Exception as e:
        print(f"  离线解析自检失败：{e}")
    if is_available():
        print("\n  尝试在线抓取 600519（需网络，失败仅提示）...")
        try:
            res = fetch("600519.SH")
            print(f"    profile={res.profile}")
            print(f"    valuation={res.valuation}  annual_years={len(res.annual_rows)}")
            print(f"    missing={res.missing_fields}")
        except Exception as e:
            print(f"    在线抓取失败（环境/网络）：{e}")
    else:
        print("  未安装 akshare：A股将回退『数据不足，待补录』。")
        print("  安装：pip install -r requirements-optional.txt")


if __name__ == "__main__":
    _selftest()
