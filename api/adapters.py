"""Framework-agnostic adapter: wraps research_service.run_research into JSON-safe payloads.

This module holds the ACTUAL API logic and is fully unit-testable without any web
framework or HTTP client (see tests/test_api.py). api/main.py is only transport.

Hard rules (mirror the migration constraints):
  - read-only: always calls run_research(ticker, readonly=True) -> never writes CSV, never fetches.
  - never raises to the caller: every failure becomes a structured {"ok": False, "state": ...}.
  - never returns a non-JSON-serializable object (numpy / Decimal / NaN are coerced).
  - business logic (scoring / AI weight) is untouched; numeric values are passed through verbatim.
"""
from __future__ import annotations

import math
import re
import sys
import traceback
from typing import Any

import research_service
from api import ai_scoring_schema as ai_schema


def _jsonable(obj: Any) -> Any:
    """Recursively coerce obj into JSON-serializable primitives (numpy/pandas scalars, NaN, etc.)."""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "item"):                 # numpy / pandas scalar -> native python
        try:
            return _jsonable(obj.item())
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    return str(obj)


def health_payload() -> dict:
    return {"ok": True}


def _points(aidyn: dict, key: str) -> list:
    return _point_list(aidyn.get(key))


def _point_list(items) -> list:
    """把 [{point, evidence_metric}, ...] 或 [str, ...] 规整为 list[str]（非空）。"""
    out = []
    for it in (items or []):
        if isinstance(it, dict):
            p = str(it.get("point", "")).strip()
            if p:
                out.append(p)
        elif it:
            out.append(str(it))
    return out


def build_research_payload(ticker: str) -> dict:
    """Return a JSON-safe dict for one ticker. Never raises.

    States: complete | pending | invalid_ticker | insufficient_data | error
    """
    ticker = (ticker or "").strip()
    if not ticker:
        return {"ok": False, "state": "invalid_ticker",
                "message": "请输入股票代码", "ticker": ticker}

    try:
        out = research_service.run_research(ticker, readonly=True)
    except Exception:
        # Keep the traceback server-side only; never leak internals to the client.
        traceback.print_exc(file=sys.stderr)
        return {"ok": False, "state": "error",
                "message": "服务器处理出错，请稍后重试", "ticker": ticker}

    if not out.get("ok"):
        canonical = out.get("canonical")
        if canonical is None:
            return {"ok": False, "state": "invalid_ticker",
                    "message": "小猫没找到这个股票代码", "ticker": ticker, "canonical": None}
        return {"ok": False, "state": "insufficient_data",
                "message": "暂时没有足够数据", "ticker": ticker, "canonical": canonical}

    result = out.get("result") or {}
    canonical = out.get("canonical") or ticker
    name = result.get("long_name") or result.get("name") or canonical
    pending = str(result.get("final_decision", "")) == "数据不足（待补录）"
    incomplete = str(result.get("data_status", "")) == "待补录"
    state = "pending" if (pending or incomplete) else "complete"

    prio = out.get("research_priority")
    if isinstance(prio, (list, tuple)):
        priority_label = prio[0] if prio else ""
        priority_note = prio[1] if len(prio) > 1 else ""
    else:
        priority_label = prio if isinstance(prio, str) else ""
        priority_note = ""

    aidyn = result.get("ai_dynamic") or {}          # legacy Phase-1 启发式（仅参考）
    ai = result.get("ai_evidence") or {}            # ai_evidence_v1 主分链路（权威）
    ai_method = ai.get("scoring_method") or ai_schema.METHOD_UNAVAILABLE
    ai_total = ai.get("total_score")                # 主分；不可用时为 None（绝不回落 legacy）

    mf = str(result.get("missing_fields", "") or "").strip()
    missing = [] if (not mf or mf == "（无）") else [s.strip() for s in re.split(r"[、,，;；]", mf) if s.strip()]

    payload = {
        "ok": True,
        "state": state,
        "ticker": ticker,
        "canonical": canonical,
        "company_name": name,
        "market": result.get("market"),
        # ── 主分来源：AI 证据评分（ai_evidence_v1）。legacy scorer 不再作为最终主分。──
        "total_score": ai_total,
        # 兼容旧字段：保持 final_score_preview == total_score 不变量（现二者同为 AI 主分）。
        "final_score_preview": ai_total,
        "rating": ai.get("rating"),
        "confidence": ai.get("confidence"),
        "summary": ai.get("summary"),
        "strengths": _point_list(ai.get("strengths")),
        "risks": _point_list(ai.get("risks")),
        "research_priority": priority_label,
        "research_priority_note": priority_note,
        "ai_rating": aidyn.get("ai_rating"),        # legacy Phase-1 评级（参考，非主分）
        "missing_fields": missing,
        "financials": {                              # legacy 规则分（参考/调试，非主分来源）
            "pe": result.get("pe"),
            "pb": result.get("pb"),
            "market_cap": result.get("market_cap"),
            "quality_score": result.get("quality_score"),
            "growth_score": result.get("growth_score"),
            "balance_sheet_score": result.get("balance_sheet_score"),
            "valuation_score": result.get("valuation_score"),
        },
        # ── 新增可信字段（ai_evidence_v1）──
        "ai_generated": bool(ai.get("ai_generated", False)),
        "scoring_method": ai_method,                # ai_llm | ai_mock | unavailable
        "scoring_rubric_version": ai.get("scoring_rubric_version"),
        "validator_status": ai.get("validator_status"),
        "generated_at": ai.get("generated_at"),
        "evidence_packet_id": ai.get("evidence_packet_id"),
        "ai_breakdown": ai.get("breakdown"),        # 7 维 {dim: score}
        "score_drivers": ai.get("score_drivers"),
        "missing_data_impact": ai.get("missing_data_impact"),
        "source_dates": ai.get("source_dates"),
        "stale_fields": ai.get("stale_fields"),
        "warnings": ai.get("warnings"),
        "disclaimer": ai.get("disclaimer"),
        # ── 数据透明度 / legacy 逐维参考 ──
        "data_confidence": ai.get("data_confidence", result.get("data_confidence")),
        "score_breakdown": result.get("score_breakdown"),   # legacy（score_engine）逐维明细：参考
        "raw": result,
    }
    return _jsonable(payload)
