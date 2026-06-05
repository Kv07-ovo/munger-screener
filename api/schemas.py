"""Response shapes for the research API (documentation / typing only — stdlib, no pydantic).

These TypedDicts describe what api.adapters.build_research_payload returns so the React
frontend (web_frontend/src/api.ts ResearchResult) and the backend stay in sync. They are
NOT validated at runtime (pydantic is not a dependency here); they exist for clarity + IDEs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class Financials(TypedDict, total=False):
    pe: Any
    pb: Any
    market_cap: Any
    quality_score: Any
    growth_score: Any
    balance_sheet_score: Any
    valuation_score: Any


class ResearchOk(TypedDict, total=False):
    ok: bool                       # True
    state: str                     # "complete" | "pending"
    ticker: str
    canonical: str
    company_name: str
    market: Optional[str]
    total_score: Any
    final_score_preview: Any       # == total_score (AI_WEIGHT == 0)
    research_priority: str
    research_priority_note: str
    ai_rating: Optional[str]
    strengths: List[str]
    risks: List[str]
    missing_fields: List[str]
    financials: Financials
    data_confidence: Any                 # MVP: 0..1 数据完整度（已用评分字段/应有评分字段），可选
    score_breakdown: List[Dict[str, Any]]  # MVP: 逐维度 {dimension,score,max_score,used_fields,missing_fields,notes}，可选
    raw: Dict[str, Any]


class ResearchError(TypedDict, total=False):
    ok: bool                       # False
    state: str                     # "invalid_ticker" | "insufficient_data" | "error"
    message: str
    ticker: str
    canonical: Optional[str]


class Health(TypedDict):
    ok: bool
