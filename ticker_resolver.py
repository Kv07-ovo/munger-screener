# ============================================================
# ticker_resolver.py  —  芒格式选股评分器 v2.2.0-alpha1
#
# 职责：把任意输入的股票代码识别为「市场 + 规范化代码」，
#       并给出各数据源（yfinance）的查询符号。
#
#   只解析，不读写文件、不联网，无第三方依赖（纯标准库）。
#
# 支持形式：
#   美股：AAPL、MSFT、BRK-B、BRK.B（类别股 . → -）
#   A股：600519、600519.SH、600519.SS、000001.SZ、300750、688981、830799.BJ
#
# 用法（自检）：
#   python ticker_resolver.py
# ============================================================

import sys
from dataclasses import dataclass

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 市场常量 ──────────────────────────────────────────────────
US      = "US"
CN      = "CN"
UNKNOWN = "UNKNOWN"

# A股后缀 → 交易所（含 yfinance 用的 .SS 别名）
_CN_SUFFIX_TO_EXCHANGE = {
    "SH": "SSE",   # 上交所
    "SS": "SSE",   # 上交所（yfinance 写法）
    "SZ": "SZSE",  # 深交所
    "BJ": "BSE",   # 北交所
}
# 交易所 → (内部规范后缀, yfinance 查询后缀)
_EXCHANGE_SUFFIX = {
    "SSE":  ("SH", ".SS"),
    "SZSE": ("SZ", ".SZ"),
    "BSE":  ("BJ", ""),     # 北交所 yfinance 基本无覆盖
}
_CURRENCY = {US: "USD", CN: "CNY", UNKNOWN: ""}


@dataclass
class ResolvedTicker:
    input: str               # 原始输入
    canonical: str           # 内部主键（去重用），如 AAPL / BRK-B / 600519.SH
    market: str              # US / CN / UNKNOWN
    exchange: str            # NASDAQ/NYSE 未知时留空；A股为 SSE/SZSE/BSE
    currency: str            # USD / CNY / ""
    provider_symbol: str     # yfinance 查询符号（如 600519.SS、BRK-B）
    autofetch_supported: bool  # alpha1：US=True，CN=False

    def __str__(self):
        return (f"{self.input!r:>14} -> market={self.market:<7} "
                f"canonical={self.canonical:<12} exch={self.exchange or '-':<5} "
                f"ccy={self.currency or '-':<4} yf={self.provider_symbol or '-':<11} "
                f"autofetch={self.autofetch_supported}")


def _unknown(raw: str) -> ResolvedTicker:
    return ResolvedTicker(raw, "", UNKNOWN, "", "", "", False)


def _cn_from_exchange(base: str, exchange: str, raw: str) -> ResolvedTicker:
    """A股：已知交易所，构造规范化结果。base 必须是数字代码。"""
    if not base.isdigit() or not (4 <= len(base) <= 6):
        return _unknown(raw)
    canon_suf, yf_suf = _EXCHANGE_SUFFIX[exchange]
    canonical = f"{base}.{canon_suf}"
    provider  = f"{base}{yf_suf}" if yf_suf else ""
    return ResolvedTicker(
        input=raw, canonical=canonical, market=CN, exchange=exchange,
        currency=_CURRENCY[CN], provider_symbol=provider,
        autofetch_supported=False,      # alpha1：A股不自动抓取
    )


def _cn_from_number(num: str, raw: str) -> ResolvedTicker:
    """A股纯数字代码：按首位推断交易所。"""
    first = num[0]
    if first == "6":
        exchange = "SSE"                # 含 688 科创板
    elif first in ("0", "3"):
        exchange = "SZSE"               # 含 300 创业板
    elif first in ("4", "8"):
        exchange = "BSE"                # 北交所
    else:
        return _unknown(raw)
    return _cn_from_exchange(num, exchange, raw)


def _us(symbol: str, raw: str) -> ResolvedTicker:
    """美股：类别股分隔统一为 '-'，交易所未知留空。"""
    return ResolvedTicker(
        input=raw, canonical=symbol, market=US, exchange="",
        currency=_CURRENCY[US], provider_symbol=symbol,
        autofetch_supported=True,
    )


def resolve(raw: str) -> ResolvedTicker:
    """
    识别并规范化股票代码。无法判定时返回 market=UNKNOWN（不瞎猜）。
    """
    s = (raw or "").strip().upper().replace(" ", "")
    if not s:
        return _unknown(raw)

    base, dot, suf = s.partition(".")

    # 1) 带后缀：A股后缀优先（.SH/.SS/.SZ/.BJ）
    if dot and suf in _CN_SUFFIX_TO_EXCHANGE:
        return _cn_from_exchange(base, _CN_SUFFIX_TO_EXCHANGE[suf], s)

    # 2) 纯数字 → A股，按首位推断交易所
    if s.isdigit():
        return _cn_from_number(s, s)

    # 3) 带点但非 A股后缀，且形如 BRK.B → 美股类别股（. → -）
    if dot and base.isalpha() and suf.isalpha() and len(suf) <= 2:
        return _us(f"{base}-{suf}", s)

    # 4) 纯字母（可含 '-' 类别分隔）→ 美股
    if s.replace("-", "").isalpha() and s.replace("-", ""):
        return _us(s, s)

    # 5) 其余无法判定
    return _unknown(s)


# ── 自检入口 ──────────────────────────────────────────────────
_SELFTEST = ["AAPL", "msft", "BRK-B", "BRK.B", "600519", "600519.SH",
             "600519.SS", "000001.SZ", "300750", "688981", "830799.BJ",
             "  aapl ", "12AB!", ""]

if __name__ == "__main__":
    print("=" * 78)
    print("  ticker_resolver 自检")
    print("=" * 78)
    for t in _SELFTEST:
        print("  " + str(resolve(t)))
