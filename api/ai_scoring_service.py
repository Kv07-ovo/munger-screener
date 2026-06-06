# ============================================================
# api/ai_scoring_service.py  —  AI 证据评分编排（ai_evidence_v1 主分链路）
#
# 链路：row → evidence packet → prompt → AI client → validator → 结构化可信结果
#
# 关键约束：
#   - 这是新的*主分来源*；legacy scorer 不再作为响应 total_score。
#   - 无 provider/无 key/provider 异常/校验失败 → 明确 ai_unavailable / failed 占位
#     （total_score=None），绝不静默回落 legacy 分、绝不伪装真实 AI。
#   - 永远不把未校验的 AI 原文放行：必经 validator。
#   - generated_at 可注入以保证测试可复现。
# ============================================================
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from api import ai_scoring_schema as schema
from api.evidence_packet import build_evidence_packet
from api.prompt_builder import build_scoring_prompt
from api.ai_output_validator import validate_ai_output
from api.ai_client import get_client

# 对外结果中 AI 输出契约的 11 键
_OUTPUT_KEYS = (
    "total_score", "rating", "confidence", "breakdown", "summary", "strengths",
    "risks", "missing_data_impact", "score_drivers", "evidence_used", "warnings",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _assemble(out: Dict[str, Any], packet: Dict[str, Any], method: str, status: str,
              ai_generated: bool, generated_at: Optional[str]) -> Dict[str, Any]:
    """合并 AI 输出（11 键）+ 服务元数据 + 证据包的数据透明度字段。剥离内部 _meta。"""
    result = {k: out.get(k) for k in _OUTPUT_KEYS}
    result.update({
        "ai_generated": ai_generated,
        "scoring_method": method,
        "scoring_rubric_version": packet.get("scoring_rubric_version"),
        "validator_status": status,
        "generated_at": generated_at or _now_iso(),
        "evidence_packet_id": packet.get("evidence_packet_id"),
        # 数据透明度（来自证据包，权威；非 AI 自报，避免幻觉）
        "source_dates": packet.get("source_dates"),
        "missing_fields": packet.get("missing_fields"),
        "stale_fields": packet.get("stale_fields"),
        "data_confidence": packet.get("data_confidence"),
        "disclaimer": "AI 研究评分，仅供研究参考，不构成任何投资建议。",
    })
    return result


def score_with_ai(row: Dict[str, Any], client: Any = None, as_of_date: Optional[str] = None,
                  generated_at: Optional[str] = None) -> Dict[str, Any]:
    """对一行（已合并年度财务的 stocks row）执行 AI 证据评分，返回可信结构化结果。

    client：可注入（测试/自定义 provider）；None 时按 env 选择（默认 mock）。
    """
    packet = build_evidence_packet(row, as_of_date=as_of_date)

    if client is None:
        client = get_client()

    if client is None or not client.available():
        reason = ("未配置 AI provider 或缺少 API key" if client is None
                  else f"provider({getattr(client, 'method', '?')}) 不可用")
        out = schema.make_unavailable_output(reason)
        return _assemble(out, packet, schema.METHOD_UNAVAILABLE, schema.VALID_FAILED,
                         False, generated_at)

    prompt = build_scoring_prompt(packet)
    try:
        raw = client.score(packet, prompt)
    except Exception as e:  # provider 调用失败：明确不可用，绝不静默回落
        out = schema.make_unavailable_output(f"provider 调用失败：{type(e).__name__}")
        return _assemble(out, packet, schema.METHOD_UNAVAILABLE, schema.VALID_FAILED,
                         False, generated_at)

    status, validated, _errors = validate_ai_output(raw, packet)
    if status == schema.VALID_FAILED:
        # validated 已是 make_failed_output 占位；对外标 unavailable，绝不展示坏结果
        return _assemble(validated, packet, schema.METHOD_UNAVAILABLE, schema.VALID_FAILED,
                         False, generated_at)

    method = getattr(client, "method", schema.METHOD_MOCK)
    ai_generated = (method == schema.METHOD_LLM)
    return _assemble(validated, packet, method, status, ai_generated, generated_at)
