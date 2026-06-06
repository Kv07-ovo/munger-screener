# ============================================================
# api/ai_client.py  —  AI 评分 client 抽象（ai_evidence_v1 链路）
#
# 职责：把「打分」这件事抽象成可替换/可 mock 的 client：
#   - MockScoringClient：证据驱动的*确定性* mock（dev/CI/测试），显式标 METHOD_MOCK，
#     不是真实 AI、不读人工分、不读 legacy 主分；用于无 key 时仍让产品/测试可跑。
#   - AnthropicScoringClient：真实 LLM（惰性 import SDK；ANTHROPIC_API_KEY/AI_SCORING_API_KEY
#     由 env 提供，绝不写死、绝不打印）。无 key/SDK → available()=False。
#   - get_client()：按 env 选择 provider；可注入自定义 client 供测试。
#
# 约定 client 接口：
#   .method       -> METHOD_MOCK | METHOD_LLM
#   .model_name   -> str
#   .available()  -> bool
#   .score(packet, prompt) -> dict | str   # 原始输出，交由 validator 校验，绝不直接展示
# ============================================================
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from api import ai_scoring_schema as schema

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_EFFORT = "medium"
DEFAULT_MAX_TOKENS = 4096

# mock 的置信度上限低于真实 AI（0.9）：诚实标注其为确定性占位、非真实推理
MOCK_CONFIDENCE_CAP = 0.6

_METRIC_LABELS = {
    "roe_5y_avg": "ROE(5年均值)", "roic_5y_avg": "ROIC(5年均值)",
    "gross_margin_5y_avg": "毛利率(5年均值)", "net_margin_5y_avg": "净利率(5年均值)",
    "revenue_growth_5y_cagr": "营收5年CAGR", "eps_growth_5y_cagr": "EPS5年CAGR",
    "pe": "市盈率PE", "fcf_yield": "自由现金流收益率",
    "debt_to_equity": "负债权益比D/E", "fcf_positive_years": "FCF为正年数",
}


# ── 取值 / 单指标质量映射（0..100，越高越好；缺失返回 None）─────────────
def _v(packet: Dict[str, Any], field: str):
    info = (packet.get("metrics") or {}).get(field) or {}
    return info.get("value")


def _metric_quality(field: str, v):
    if v is None:
        return None
    if field == "roe_5y_avg":
        # 负 ROE（常因负权益/亏损）给 0，避免负权益公司被地板分 20 误抬
        return 0 if v < 0 else 100 if v >= 20 else 80 if v >= 15 else 60 if v >= 10 else 40 if v >= 5 else 20
    if field == "roic_5y_avg":
        return 0 if v < 0 else 100 if v >= 15 else 80 if v >= 10 else 60 if v >= 7 else 40 if v >= 4 else 20
    if field == "gross_margin_5y_avg":
        return 100 if v >= 60 else 80 if v >= 40 else 60 if v >= 25 else 40 if v >= 15 else 20
    if field == "net_margin_5y_avg":
        return 100 if v >= 20 else 80 if v >= 12 else 60 if v >= 6 else 40 if v >= 0 else 0
    if field in ("revenue_growth_5y_cagr", "eps_growth_5y_cagr"):
        return 100 if v >= 20 else 80 if v >= 12 else 60 if v >= 6 else 40 if v >= 0 else 20
    if field == "pe":
        if v <= 0 or v > 500:
            return None
        return 100 if v <= 15 else 80 if v <= 22 else 60 if v <= 30 else 40 if v <= 40 else 20
    if field == "fcf_yield":
        return 100 if v >= 5 else 80 if v >= 3 else 60 if v >= 1 else 40 if v >= 0 else 20
    if field == "debt_to_equity":
        if v <= 0:
            return 10   # 负权益等异常：高风险
        return 100 if v <= 0.3 else 90 if v <= 0.5 else 70 if v <= 1 else 50 if v <= 2 else 10
    if field == "fcf_positive_years":
        return 100 if v >= 5 else 80 if v >= 4 else 60 if v >= 3 else 40 if v >= 2 else 20
    return None


def _renorm(ach: float, pos: float, nominal: float) -> float:
    return round(ach / pos * nominal, 2) if pos > 0 else 0.0


def _trend_pts(t, hi=2.0, mid=1.0):
    if t == "improving":
        return hi
    if t == "stable":
        return mid
    if t == "declining":
        return 0.0
    return None


# ── 7 维确定性打分（None-aware；moat/management 用量化代理，绝不用人工分）──
def _dim_business_quality(p):
    ach = pos = 0.0
    for field, mx in (("roe_5y_avg", 9), ("gross_margin_5y_avg", 7), ("net_margin_5y_avg", 5),
                      ("fcf_positive_years", 5), ("roic_5y_avg", 4)):
        q = _metric_quality(field, _v(p, field))
        if q is not None:
            pos += mx; ach += mx * q / 100.0
    return _renorm(ach, pos, 30.0)


def _dim_growth(p):
    ach = pos = 0.0
    rev = _v(p, "revenue_growth_5y_cagr")
    eps = _v(p, "eps_growth_5y_cagr")
    for field, v in (("revenue_growth_5y_cagr", rev), ("eps_growth_5y_cagr", eps)):
        q = _metric_quality(field, v)
        if q is not None:
            pos += 7; ach += 7 * q / 100.0
    if rev is not None and eps is not None:
        pos += 1; ach += 1 if (rev > 0 and eps > 0) else 0
    return _renorm(ach, pos, 15.0)


def _dim_balance_sheet(p):
    ach = pos = 0.0
    de = _v(p, "debt_to_equity")
    if de is not None:
        pos += 10; ach += 10 * (_metric_quality("debt_to_equity", de) or 0) / 100.0
    fy = _v(p, "fcf_positive_years")
    if fy is not None:
        pos += 5; ach += 5 if fy >= 5 else 0
    return _renorm(ach, pos, 15.0)


def _dim_valuation(p):
    ach = pos = 0.0
    pe = _v(p, "pe")
    fcf = _v(p, "fcf_yield")
    pe_q = _metric_quality("pe", pe)
    if pe_q is not None:
        pos += 6; ach += 6 * pe_q / 100.0
    if fcf is not None:
        pos += 6; ach += 6 * (_metric_quality("fcf_yield", fcf) or 0) / 100.0
    if pe_q is not None and fcf is not None:
        pos += 3; ach += 3 if (pe is not None and pe <= 25 and fcf >= 3) else 0
    base = _renorm(ach, pos, 15.0)
    pct = _v(p, "pe_percentile_5y")
    if pct is not None:
        if pct <= 25:
            base += 1
        elif pct > 80:
            base -= 3
    return round(schema.clamp(base, 0.0, 15.0), 2)


def _dim_moat(p):
    """量化护城河代理：盈利能力*水平+持续性*（ROIC/毛利/净利 + 其趋势），非人工 moat_score。"""
    ach = pos = 0.0
    roic_q = _metric_quality("roic_5y_avg", _v(p, "roic_5y_avg"))
    if roic_q is not None:
        pos += 6; ach += 6 * roic_q / 100.0
    gm_q = _metric_quality("gross_margin_5y_avg", _v(p, "gross_margin_5y_avg"))
    if gm_q is not None:
        pos += 5; ach += 5 * gm_q / 100.0
    for field in ("roic_trend", "margin_trend"):
        tp = _trend_pts(_v(p, field))
        if tp is not None:
            pos += 2; ach += tp
    return _renorm(ach, pos, 15.0)


def _dim_management(p):
    """量化管理/治理代理：资本运用与稳健性（FCF 持续/负债/营收趋势），非人工 management_score。"""
    ach = pos = 0.0
    fy = _v(p, "fcf_positive_years")
    if fy is not None:
        pos += 2.5; ach += 2.5 if fy >= 5 else 1.5 if fy >= 3 else 0.5 if fy >= 1 else 0.0
    de = _v(p, "debt_to_equity")
    if de is not None:
        pos += 1.5
        ach += (1.5 if (0 < de <= 0.5) else 1.0 if (0 < de <= 1) else 0.5 if (0 < de <= 2) else 0.0)
    tp = _trend_pts(_v(p, "revenue_trend"), hi=1.0, mid=0.5)
    if tp is not None:
        pos += 1.0; ach += tp
    return _renorm(ach, pos, 5.0)


def _dim_data_quality(p):
    n_missing = len(p.get("missing_fields") or [])
    stale = bool(p.get("stale_fields"))
    conf = p.get("data_confidence")
    adj = -min(5.0, n_missing * 1.0)
    if stale:
        adj -= 1.0
    if n_missing == 0 and not stale and conf == 1.0:
        adj += 2.0
    return round(schema.clamp(adj, -5.0, 5.0), 2)


def _fmt(field, v):
    unit = "percent"
    if field in ("pe", "debt_to_equity"):
        return f"{v:g}"
    if field == "fcf_positive_years":
        return f"{int(v)}年"
    return f"{v:g}%"


class MockScoringClient:
    """证据驱动的确定性 mock：不读人工分、不读 legacy 主分；输出显式标 METHOD_MOCK。"""
    method = schema.METHOD_MOCK
    model_name = "evidence-mock-v1"

    def available(self) -> bool:
        return True

    def score(self, packet: Dict[str, Any], prompt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        breakdown = {
            "business_quality": _dim_business_quality(packet),
            "growth": _dim_growth(packet),
            "balance_sheet": _dim_balance_sheet(packet),
            "valuation": _dim_valuation(packet),
            "moat": _dim_moat(packet),
            "management_governance": _dim_management(packet),
            "data_quality_adjustment": _dim_data_quality(packet),
        }
        total = round(sum(breakdown.values()), 2)
        display = round(schema.clamp(total, 0.0, 100.0), 2)
        rating = schema.rating_for_score(display)

        # 证据绑定的 strengths/risks：仅引用证据包中确有数值且在 EVIDENCE_METRICS 的字段
        scored = []
        for field in _METRIC_LABELS:
            if field not in schema.EVIDENCE_METRICS:
                continue
            v = _v(packet, field)
            q = _metric_quality(field, v)
            if q is not None:
                scored.append((field, v, q))
        strengths = [{"point": f"{_METRIC_LABELS[f]} {_fmt(f, v)}，量化上偏强", "evidence_metric": f}
                     for f, v, q in sorted(scored, key=lambda t: -t[2]) if q >= 80][:4]
        risks = [{"point": f"{_METRIC_LABELS[f]} {_fmt(f, v)}，量化上偏弱", "evidence_metric": f}
                 for f, v, q in sorted(scored, key=lambda t: t[2]) if q <= 40][:4]
        evidence_used = sorted({f for f, _v_, _q in scored})

        # 置信度：覆盖度 × 一致性 × 陈旧惩罚，封顶 MOCK_CONFIDENCE_CAP
        coverage = packet.get("data_confidence") or 0.0
        consistency = 1.0
        fy = _v(packet, "fcf_yield")
        if fy is not None and fy > 30:
            consistency -= 0.2
        for f in ("roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg"):
            mv = _v(packet, f)
            if mv is not None and mv > 100:
                consistency -= 0.2
                break
        consistency = max(0.3, consistency)
        if packet.get("stale_fields"):
            consistency *= 0.85
        confidence = round(min(MOCK_CONFIDENCE_CAP, MOCK_CONFIDENCE_CAP * coverage * consistency), 2)

        n_missing = len(packet.get("missing_fields") or [])
        stale_n = len(packet.get("stale_fields") or [])
        missing_impact = (f"缺失 {n_missing} 项核心量化指标" if n_missing else "核心量化指标齐全")
        if stale_n:
            missing_impact += f"；{stale_n} 项数据可能过期"
        missing_impact += "，已据此重归一化并下调置信度。"

        drivers = []
        for name in ("business_quality", "growth", "balance_sheet", "valuation", "moat"):
            lo, hi = schema.DIMENSION_BOUNDS[name]
            ratio = (breakdown[name] / hi) if hi else 0
            tag = "强" if ratio >= 0.7 else "中" if ratio >= 0.4 else "弱"
            drivers.append(f"{name} {breakdown[name]:g}/{hi:g}（{tag}）")

        warnings = ["本评分由确定性 mock 生成，仅供 dev/示例，非真实 AI 推理"]
        warnings += [w for w in (packet.get("data_warnings") or []) if not schema.has_advice(w)]

        summary = (f"基于 {len(scored)} 项量化证据的确定性映射，{packet.get('company_name') or packet.get('ticker')} "
                   f"综合得 {display:g}/100，倾向「{rating}」。本结果为开发用确定性示例评分，非真实 AI 推理，"
                   f"需人工复核，不作任何决策依据。")

        return {
            "total_score": total,
            "rating": rating,
            "confidence": confidence,
            "breakdown": breakdown,
            "summary": summary,
            "strengths": strengths,
            "risks": risks,
            "missing_data_impact": missing_impact,
            "score_drivers": drivers,
            "evidence_used": evidence_used,
            "warnings": warnings,
        }


class AnthropicScoringClient:
    """真实 LLM provider（Anthropic）。惰性 import SDK；key 由 env 提供，绝不写死/打印。"""
    method = schema.METHOD_LLM

    def __init__(self, model: Optional[str] = None, effort: str = DEFAULT_EFFORT,
                 max_tokens: int = DEFAULT_MAX_TOKENS):
        self.model_name = model or os.getenv("AI_SCORING_MODEL") or DEFAULT_MODEL
        self.effort = effort
        self.max_tokens = max_tokens

    @staticmethod
    def _has_key() -> bool:
        return bool(os.getenv("AI_SCORING_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))

    def available(self) -> bool:
        if not self._has_key():
            return False
        try:
            import anthropic  # noqa: F401  惰性 import：无 SDK 不致 import 期崩溃
        except Exception:
            return False
        return True

    def _new_client(self):
        import anthropic
        key = os.getenv("AI_SCORING_API_KEY")  # 优先专用 key；否则交由 SDK 读 ANTHROPIC_API_KEY
        return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()

    def score(self, packet: Dict[str, Any], prompt: Dict[str, Any]) -> str:
        client = self._new_client()
        resp = client.messages.create(
            model=self.model_name,
            max_tokens=self.max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            system=prompt["system"],
            messages=[{"role": "user", "content": prompt["user"]}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return _extract_json(text)


def _extract_json(text: str) -> str:
    """从 LLM 文本中提取 JSON 对象（容忍偶发的围栏/前后缀）。失败则原样返回交由 validator 处置。"""
    s = (text or "").strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    m = re.search(r"\{.*\}", s, re.DOTALL)
    return m.group(0) if m else s


def get_client(provider: Optional[str] = None):
    """按 env 选择 client；返回 client 或 None（None → 服务层判 ai_unavailable）。

    AI_SCORING_PROVIDER:
      - 未设 / "" / mock / dev / heuristic → MockScoringClient（默认，保证无 key 也可跑且测试稳定）
      - anthropic / claude                → AnthropicScoringClient（无 key/SDK 时 available()=False）
      - none / off / disabled             → None（显式强制 ai_unavailable）
    """
    prov = (provider if provider is not None else os.getenv("AI_SCORING_PROVIDER", "")).strip().lower()
    if prov in ("", "mock", "dev", "heuristic"):
        return MockScoringClient()
    if prov in ("anthropic", "claude"):
        return AnthropicScoringClient()
    if prov in ("none", "off", "disabled"):
        return None
    # 未知 provider：保守回退到 mock（显式标记，不伪装真实 AI）
    return MockScoringClient()
