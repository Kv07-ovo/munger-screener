# ============================================================
# api/ai_output_validator.py  —  AI 输出校验器（ai_evidence_v1 链路核心闸门）
#
# 职责：把 LLM/mock 的*原始输出*校验/规范化为可信结果。
#       - 不可修复（缺键 / 结构非法 / 合规违规）→ (failed, make_failed_output, errors)。
#       - 可修复（confidence 略超 / total 与 breakdown 不符 / rating 非枚举 /
#         个别 strengths/risks 引用了缺失或非法 evidence）→ 修复并标 repaired。
#       - 全部合规且无需修复 → passed。
#       绝不把未校验的 AI 原文直接放行。
#
# 纯函数：无 I/O、无第三方依赖（stdlib + 本包 schema）。
# ============================================================
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from api import ai_scoring_schema as schema

_NUM = (int, float)


def _is_num(v: Any) -> bool:
    # 排除 bool（Python 中 bool 是 int 子类）/ NaN / ±inf
    return isinstance(v, _NUM) and not isinstance(v, bool) and math.isfinite(v)


def _present_metrics(packet: Dict[str, Any]) -> set:
    """证据包中*确有数值*的指标字段名集合（用于 evidence 防幻觉）。"""
    out = set()
    for field, info in (packet.get("metrics") or {}).items():
        if isinstance(info, dict) and info.get("value") is not None:
            out.add(field)
    return out


def _clean_points(items: Any, present: set, repairs: List[str], key: str) -> List[Dict[str, str]]:
    """规范化 strengths/risks：丢弃 point 空 / evidence_metric 非法或证据缺失的项（防幻觉）。"""
    cleaned = []
    if not isinstance(items, list):
        repairs.append(f"{key} 非 list，已置空")
        return cleaned
    for it in items:
        if not isinstance(it, dict):
            repairs.append(f"{key} 含非 dict 项，已丢弃"); continue
        point = str(it.get("point", "")).strip()
        em = it.get("evidence_metric")
        if not point:
            repairs.append(f"{key} 含空 point，已丢弃"); continue
        if em not in schema.EVIDENCE_METRICS:
            repairs.append(f"{key} 引用非法 evidence_metric={em!r}，已丢弃"); continue
        if em not in present:
            repairs.append(f"{key} 引用缺失证据 {em}（证据包中无该数值），已丢弃（防幻觉）"); continue
        cleaned.append({"point": point, "evidence_metric": em})
    return cleaned


def _advice_hit(output: Dict[str, Any]) -> Optional[str]:
    """任一对外文本含投资建议措辞 → 返回命中位置（合规硬失败）。"""
    if schema.has_advice(output.get("summary", "")):
        return "summary"
    if schema.has_advice(output.get("rating", "")):
        return "rating"
    if schema.has_advice(output.get("missing_data_impact", "")):
        return "missing_data_impact"
    for key in ("strengths", "risks"):
        for it in (output.get(key) or []):
            if isinstance(it, dict) and schema.has_advice(it.get("point", "")):
                return f"{key}.point"
    for key in ("score_drivers", "warnings", "evidence_used"):
        for s in (output.get(key) or []):
            if schema.has_advice(s):
                return key
    return None


def _str_list(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]


def validate_ai_output(raw: Any, packet: Optional[Dict[str, Any]] = None
                       ) -> Tuple[str, Dict[str, Any], List[str]]:
    """校验/规范化 AI 原始输出。

    返回 (status, output, errors)：
      - status ∈ {passed, repaired, failed}
      - failed 时 output = schema.make_failed_output(errors)
      - passed/repaired 时 output 为规范化后的 11 键结果（含展示用 total_score∈[0,100]）
    """
    packet = packet or {}
    repairs: List[str] = []

    # 0) 允许传入 JSON 文本
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            errs = ["AI 输出非合法 JSON，无法解析"]
            return schema.VALID_FAILED, schema.make_failed_output(errs), errs
    if not isinstance(raw, dict):
        errs = ["AI 输出不是 JSON 对象"]
        return schema.VALID_FAILED, schema.make_failed_output(errs), errs

    # 1) 必填键
    missing_keys = [k for k in schema.REQUIRED_KEYS if k not in raw]
    if missing_keys:
        errs = [f"缺少必填键：{k}" for k in missing_keys]
        return schema.VALID_FAILED, schema.make_failed_output(errs), errs

    # 2) breakdown：必须含全部维度且为数值；越界则 clamp（repaired）
    bd_in = raw.get("breakdown")
    if not isinstance(bd_in, dict):
        errs = ["breakdown 必须为 dict"]
        return schema.VALID_FAILED, schema.make_failed_output(errs), errs
    breakdown: Dict[str, float] = {}
    for name in schema.DIMENSION_NAMES:
        v = bd_in.get(name)
        if not _is_num(v):
            errs = [f"breakdown.{name} 缺失或非数值：{v!r}"]
            return schema.VALID_FAILED, schema.make_failed_output(errs), errs
        lo, hi = schema.DIMENSION_BOUNDS[name]
        cv = schema.clamp(float(v), lo, hi)
        if abs(cv - float(v)) > 1e-9:
            repairs.append(f"breakdown.{name}={v:g} 越界[{lo:g},{hi:g}]，已 clamp 至 {cv:g}")
        breakdown[name] = round(cv, 2)

    # 3) total_score：与 breakdown 之和一致性（容差内放行，超出按和重算 → repaired）
    raw_total = raw.get("total_score")
    bd_sum = round(sum(breakdown.values()), 2)
    if not _is_num(raw_total):
        repairs.append(f"total_score 非数值（{raw_total!r}），按 breakdown 之和取 {bd_sum:g}")
        effective_total = bd_sum
    else:
        if abs(float(raw_total) - bd_sum) > schema.TOTAL_BREAKDOWN_TOLERANCE:
            repairs.append(f"total_score={float(raw_total):g} 与 breakdown 之和 {bd_sum:g} "
                           f"不一致（容差 {schema.TOTAL_BREAKDOWN_TOLERANCE:g}），已按和修正")
            effective_total = bd_sum
        else:
            effective_total = float(raw_total)
    display_total = round(schema.clamp(effective_total, schema.DISPLAY_SCORE_MIN,
                                       schema.DISPLAY_SCORE_MAX), 2)

    # 4) rating：枚举；非法则按分数推断（repaired）
    rating = raw.get("rating")
    if rating not in schema.AI_RATING_ENUM or rating in (schema.RATING_UNAVAILABLE,):
        new_rating = schema.rating_for_score(display_total)
        repairs.append(f"rating={rating!r} 非法/不可用，按总分推断为「{new_rating}」")
        rating = new_rating

    # 5) confidence：数值 + [0,CAP]；并按数据完整度/陈旧度二次封顶（强约束）
    conf_in = raw.get("confidence")
    if not _is_num(conf_in):
        errs = [f"confidence 非数值：{conf_in!r}"]
        return schema.VALID_FAILED, schema.make_failed_output(errs), errs
    confidence = schema.clamp(float(conf_in), 0.0, schema.CONFIDENCE_CAP)
    if abs(confidence - float(conf_in)) > 1e-9:
        repairs.append(f"confidence={float(conf_in):g} 越界，已 clamp 至 {confidence:g}")
    coverage = packet.get("data_confidence")
    cap_by_data = schema.CONFIDENCE_CAP * (coverage if _is_num(coverage) else 1.0)
    if packet.get("stale_fields"):
        cap_by_data *= 0.85
    if confidence > cap_by_data:
        repairs.append(f"confidence 依据缺失/陈旧数据从 {confidence:g} 降至 {round(cap_by_data,2):g}")
        confidence = cap_by_data
    confidence = round(confidence, 2)

    # 6) summary / missing_data_impact：强制 str
    summary = str(raw.get("summary", "")).strip()
    missing_impact = str(raw.get("missing_data_impact", "")).strip()
    if not isinstance(raw.get("summary"), str):
        repairs.append("summary 非 str，已强制转换")

    # 7) strengths / risks：防幻觉清洗
    present = _present_metrics(packet)
    strengths = _clean_points(raw.get("strengths"), present, repairs, "strengths")
    risks = _clean_points(raw.get("risks"), present, repairs, "risks")

    # 8) evidence_used：仅保留合法指标名
    ev_in = _str_list(raw.get("evidence_used"))
    evidence_used = [e for e in ev_in if e in schema.EVIDENCE_METRICS]
    if len(evidence_used) != len(ev_in):
        repairs.append("evidence_used 含非法/未知指标名，已剔除")
    score_drivers = _str_list(raw.get("score_drivers"))
    warnings = _str_list(raw.get("warnings"))

    output = {
        "total_score": display_total,
        "rating": rating,
        "confidence": confidence,
        "breakdown": breakdown,
        "summary": summary,
        "strengths": strengths,
        "risks": risks,
        "missing_data_impact": missing_impact,
        "score_drivers": score_drivers,
        "evidence_used": evidence_used,
        "warnings": warnings,
    }

    # 9) 合规：任一对外文本含投资建议措辞 → 硬失败（在清洗后检查，确保放行文本干净）
    hit = _advice_hit(output)
    if hit is not None:
        errs = [f"输出含投资建议措辞（位置：{hit}）"]
        return schema.VALID_FAILED, schema.make_failed_output(errs), errs

    status = schema.VALID_REPAIRED if repairs else schema.VALID_PASSED
    return status, output, repairs
