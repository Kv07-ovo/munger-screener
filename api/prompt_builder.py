# ============================================================
# api/prompt_builder.py  —  固定 rubric + 系统提示词构造（ai_evidence_v1）
#
# 职责：把 evidence packet 转成给 LLM 的 {system, user} 消息。
#       system 编码固定 rubric/权重/铁律/输出契约；user 给证据 + 期望 JSON。
#       纯函数：不联网、无第三方依赖（stdlib + 本包 schema/evidence_packet）。
#
# 设计要点：
#   - 只把「可披露的证据」放进 prompt：metrics(值+单位+缺失标记)、human_notes(定性事实)、
#     source_dates/stale/warnings；packet._legacy_reference（人工数值分）绝不进 prompt。
#   - 强约束：只用证据、不得编造、缺失显式标注、按缺失/过期降 confidence、
#     输出严格 JSON（REQUIRED_KEYS）、rating 落枚举、evidence_metric 落 EVIDENCE_METRICS、
#     全中文、禁任何投资建议措辞。
# ============================================================
from __future__ import annotations

import json
from typing import Any, Dict

from api import ai_scoring_schema as schema

# rubric 维度中文名与权重说明（与 schema.DIMENSIONS 对齐）
_DIM_CN = {
    "business_quality": "生意质量（ROE/ROIC/利润率/现金流持续性）",
    "growth": "成长（营收/EPS 复合增速与连续性）",
    "balance_sheet": "资产负债（负债权益比、偿债与财务稳健）",
    "valuation": "估值（PE/PB/FCF 收益率，估值与质量匹配度）",
    "moat": "护城河（基于行业地位/品牌/网络效应/规模/切换成本 + 盈利持续性证据判断；不得照搬任何人工护城河分）",
    "management_governance": "管理层/治理（资本配置、回购分红、风险事件；不得照搬任何人工管理层分）",
    "data_quality_adjustment": "数据质量调整（缺失/过期/口径不一致时降分，最优数据可小幅加分）",
}


def _rubric_lines() -> str:
    lines = []
    for name, lo, hi in schema.DIMENSIONS:
        cn = _DIM_CN.get(name, name)
        if name == "data_quality_adjustment":
            lines.append(f"  - {name}（{cn}）：范围 [{lo:g}, {hi:g}]")
        else:
            lines.append(f"  - {name}（{cn}）：0 ~ {hi:g} 分")
    return "\n".join(lines)


def build_system_prompt() -> str:
    return f"""你是严谨的股票研究评分执行者。基于给定的「证据包（evidence packet）」和下述固定 rubric，对一家公司做结构化评分。你不是投资顾问。

【固定 rubric（满分 100 = 正向 6 维 95 + 数据质量调整 ±5）】
{_rubric_lines()}

【铁律 — 必须全部遵守】
1. 只能使用证据包中实际给出的事实。证据包没有的数据，绝不编造、绝不脑补、绝不臆测具体数字。
2. 缺失字段必须显式承认：在 missing_data_impact 与 warnings 中说明，并据此降低 confidence。
3. 数据越缺、越陈旧（stale_fields）、口径越不一致，confidence 越低；data_quality_adjustment 相应给负分。
4. moat 与 management_governance 必须基于证据中的行业/公司事实与盈利持续性证据推断，
   严禁直接采用任何「人工护城河分 / 人工管理层分」作为分数（证据包也不会提供它们作为分数）。
5. total_score 必须等于 7 个维度分之和（容差 ≤ {schema.TOTAL_BREAKDOWN_TOLERANCE:g}）。
6. strengths / risks 每条都必须绑定一个 evidence_metric，且该字段名必须出现在「允许的 evidence 指标」中、
   且在证据包中确有数值；不得引用缺失字段或编造指标名。
7. confidence ∈ [0, {schema.CONFIDENCE_CAP:g}]；rating 必须是给定枚举之一。
8. 全程中文输出。严禁任何投资建议措辞（买入/卖出/持有/加减仓/目标价等），只做研究性描述。
9. 必须只输出一个 JSON 对象，不要任何额外文字、解释或 Markdown 代码围栏。"""


def _render_metrics(packet: Dict[str, Any]) -> Dict[str, Any]:
    """把 metrics 渲染成 {field: "值 单位" 或 "缺失"}，供模型直观阅读。"""
    out = {}
    for field, info in packet.get("metrics", {}).items():
        v = info.get("value")
        if v is None:
            out[field] = "缺失"
        elif info.get("unit") == "percent":
            out[field] = f"{v:g}%"
        elif info.get("unit") in ("trend",):
            out[field] = str(v)
        elif info.get("unit") == "years":
            out[field] = f"{v:g} 年"
        elif info.get("unit") == "percentile":
            out[field] = f"{v:g} 分位"
        elif field == "market_cap" and info.get("unit"):
            out[field] = f"{v:g} {info['unit']}"
        else:
            out[field] = f"{v:g}"
    return out


def build_user_prompt(packet: Dict[str, Any]) -> str:
    evidence = {
        "ticker": packet.get("ticker"),
        "company_name": packet.get("company_name"),
        "market": packet.get("market"),
        "sector": packet.get("sector"),
        "industry": packet.get("industry"),
        "as_of_date": packet.get("as_of_date"),
        "metrics": _render_metrics(packet),
        "dimension_reference_fields": packet.get("dimension_fields"),
        "human_qualitative_notes": packet.get("human_notes") or {},
        "source_dates": packet.get("source_dates"),
        "missing_fields": packet.get("missing_fields"),
        "stale_fields": packet.get("stale_fields"),
        "data_warnings": packet.get("data_warnings"),
        "data_confidence_hint": packet.get("data_confidence"),
    }
    output_contract = {
        "total_score": "number，0-100，且≈breakdown 各维之和",
        "rating": schema.AI_RATING_ENUM,
        "confidence": f"number，0-{schema.CONFIDENCE_CAP:g}",
        "breakdown": {name: f"[{lo:g},{hi:g}]" for name, lo, hi in schema.DIMENSIONS},
        "summary": "string，2-4 句中文研究性总结",
        "strengths": [{"point": "string", "evidence_metric": "见允许指标"}],
        "risks": [{"point": "string", "evidence_metric": "见允许指标"}],
        "missing_data_impact": "string，缺失/过期数据对评分与置信度的影响",
        "score_drivers": ["string，最关键的几条打分驱动"],
        "evidence_used": ["string，实际引用到的 evidence_metric 字段名"],
        "warnings": ["string，数据/口径风险提示"],
    }
    return (
        "【证据包】\n"
        + json.dumps(evidence, ensure_ascii=False, indent=2)
        + "\n\n【允许的 evidence 指标（evidence_metric 与 evidence_used 只能取自此集合）】\n"
        + json.dumps(sorted(schema.EVIDENCE_METRICS), ensure_ascii=False)
        + "\n\n【必须输出的 JSON 结构（仅输出这个 JSON 对象，键名完全一致）】\n"
        + json.dumps(output_contract, ensure_ascii=False, indent=2)
    )


def build_scoring_prompt(packet: Dict[str, Any]) -> Dict[str, Any]:
    """返回 {system, user, rubric_version}，供 AI client 调用。"""
    return {
        "system": build_system_prompt(),
        "user": build_user_prompt(packet),
        "rubric_version": schema.SCORING_RUBRIC_VERSION,
    }
