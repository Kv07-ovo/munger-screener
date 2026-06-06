# ============================================================
# api/ai_scoring_schema.py  —  AI 证据驱动评分（ai_evidence_v1）输出契约
#
# 职责：定义新一代「AI 作为最终评分执行者」的输出结构、固定 rubric 维度、
#       取值范围、评级枚举、合规黑名单，以及不可用/失败时的标准占位输出构造器。
#       纯数据 + 纯函数：仅依赖 re，无第三方依赖、无 I/O、不联网、不可变常量。
#
# 与根目录 ai_score_schema.py（legacy 启发式旁路，8 字段、confidence≤0.5）的区别：
#   - 本模块面向 LLM 输出，含 7 维 breakdown、score_drivers、evidence_used；
#   - total_score 由 breakdown 校验得出（见 api/ai_output_validator.py）；
#   - 是新主分链路的契约，legacy ai_score_schema.py 不变、不参与本链路。
#
# 契约边界说明（重要）：
#   REQUIRED_KEYS 仅为 *LLM 原始输出* 的契约。source_dates / stale_fields /
#   scoring_method / generated_at 等属 evidence packet 输入元数据或服务侧产出，
#   绝不由 LLM 生成（让模型自报日期=允许编造），由 response adapter 从 packet 注入。
# ============================================================
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# rubric / 评分方法版本戳：任何 rubric 文本或维度权重变化都应 bump 此值
SCORING_RUBRIC_VERSION = "ai_evidence_v1"
SCORING_METHOD = "ai_evidence_v1"

# 评分方法（写入响应 scoring_method 字段，前端据此区分真实 AI / mock / 不可用）
METHOD_LLM = "ai_llm"            # 真实 LLM provider 产出
METHOD_MOCK = "ai_mock"          # 证据驱动的确定性 mock（dev/CI，非真实 AI，已显式标记）
METHOD_UNAVAILABLE = "unavailable"  # 无 key / provider 失败 / 校验失败

# validator_status
VALID_PASSED = "passed"
VALID_REPAIRED = "repaired"   # 校验器自动修复了可修复项（clamp confidence / 按 breakdown 重算 total）后通过
VALID_FAILED = "failed"

# 错误态标记：make_unavailable_output / make_failed_output 在返回 dict 中写入 ERROR_META_KEY，
# 下游用 is_error_output() 判定，无需硬编码字符串。
ERROR_META_KEY = "_meta"
ERROR_TYPE_UNAVAILABLE = "ai_unavailable"   # 无 key / provider 调用失败
ERROR_TYPE_VALIDATION = "validation_failed"  # AI 有响应但校验失败

# ---- 固定 rubric：7 维 breakdown（名称, 下限, 上限）。正向 6 维满分 95 + 数据质量调整 ±5 ----
DIMENSIONS: Tuple[Tuple[str, float, float], ...] = (
    ("business_quality", 0.0, 30.0),
    ("growth", 0.0, 15.0),
    ("balance_sheet", 0.0, 15.0),
    ("valuation", 0.0, 15.0),
    ("moat", 0.0, 15.0),
    ("management_governance", 0.0, 5.0),
    ("data_quality_adjustment", -5.0, 5.0),
)
DIMENSION_NAMES = tuple(name for name, _lo, _hi in DIMENSIONS)
DIMENSION_BOUNDS = {name: (lo, hi) for name, lo, hi in DIMENSIONS}

# total_score 理论范围：正向 6 维 [0,95] + data_quality_adjustment [-5,+5] = [-5,100]。
# 校验时按 [TOTAL_SCORE_MIN, TOTAL_SCORE_MAX] 检查 total≈Σbreakdown；对外展示再 clamp 到 [0,100]。
TOTAL_SCORE_MIN = -5.0
TOTAL_SCORE_MAX = 100.0
DISPLAY_SCORE_MIN = 0.0
DISPLAY_SCORE_MAX = 100.0

# total_score 与 breakdown 之和允许的最大偏差（容忍 AI 的微小算术误差，超出则判不一致）
TOTAL_BREAKDOWN_TOLERANCE = 1.0

# source_date 超过此天数（相对 as_of_date）即判 stale，触发 data_quality_adjustment 降分/降置信
SOURCE_DATE_STALE_DAYS = 400

# 评级枚举（AI 输出 rating 必须落在其中）
AI_RATING_ENUM = ["卓越", "优质", "良好", "中性", "偏弱", "数据不足", "AI不可用"]
RATING_DATA_INSUFFICIENT = "数据不足"
RATING_UNAVAILABLE = "AI不可用"

# 置信度上限：真实 AI 的可信度上限高于 legacy 启发式（0.5），但仍封顶以保持克制
CONFIDENCE_CAP = 0.9

# strengths / risks 的 evidence_metric 只能引用证据包中真实存在的量化字段名。
# 注意：必须与数据层真实列名一致（见 store.BASE_COLUMNS / financial_analyzer 输出），
# 否则 validator 会拒掉合法字段、放过非法简称。
EVIDENCE_METRICS = {
    "roe_5y_avg", "roic_5y_avg", "gross_margin_5y_avg", "net_margin_5y_avg",
    "fcf_positive_years", "debt_to_equity", "revenue_growth_5y_cagr",
    "eps_growth_5y_cagr", "pe", "fcf_yield", "pe_percentile_5y", "pb",
    "market_cap", "roe_trend", "roic_trend", "margin_trend", "revenue_trend",
}

# 投资建议措辞黑名单。
#   - CN 多字词组用精确子串即可（不会误伤）；'持有' 单独用正则（排除 持有人/持有量/持有者…）。
#   - EN 单词用词边界 \b 匹配，避免 buy→buyback、sell→selling、hold→shareholder 等误报。
ADVICE_BLACKLIST_CN = ["买入", "卖出", "加仓", "减仓", "目标价", "建仓", "抄底", "清仓", "满仓"]
ADVICE_BLACKLIST_EN_WORDS = ["buy", "sell", "hold", "overweight", "underweight"]
ADVICE_BLACKLIST_EN_PHRASES = ["target price", "price target"]

# 预编译：EN 词边界（含连字符形式如 buy-side 不算建议；仅整词命中）；
# CN '持有' 排除常见中性名词/术语后缀（持有人/持有量/持有期/持有比例/持有至到期/持有仓位…），
# 仅在作"持有评级/建议"语义时命中，避免误杀银行/保险研究文本。
_RE_EN_WORDS = re.compile(r"\b(" + "|".join(ADVICE_BLACKLIST_EN_WORDS) + r")\b", re.IGNORECASE)
_RE_CN_HOLD = re.compile(r"持有(?!人|者|量|股|份|有|期|比|至|仓|型|率|成本)")

# AI 原始输出必填键（仅 LLM 输出契约，见文件头「契约边界说明」）
REQUIRED_KEYS = [
    "total_score", "rating", "confidence", "breakdown", "summary",
    "strengths", "risks", "missing_data_impact", "score_drivers",
    "evidence_used", "warnings",
]

_NUM = (int, float)


def has_advice(text: Any) -> bool:
    """文本是否含投资建议措辞（CN 精确子串 + '持有' 正则；EN 词边界 + 短语）。"""
    s = str(text)
    low = s.lower()
    if any(w in s for w in ADVICE_BLACKLIST_CN):
        return True
    if _RE_CN_HOLD.search(s):
        return True
    if any(p in low for p in ADVICE_BLACKLIST_EN_PHRASES):
        return True
    return bool(_RE_EN_WORDS.search(s))


def clamp(v: float, lo: float, hi: float) -> float:
    """夹到 [lo, hi]。"""
    return max(lo, min(hi, v))


def rating_for_score(score: Optional[float]) -> str:
    """由 total_score 推荐评级。

    仅用于 mock / 一致性兜底；AI 可自报 rating 但须落在枚举。
    输入约定为展示分 [0,100]；负数（理论下界 -5）按最低档「偏弱」处理，调用方通常已 clamp。
    """
    if score is None:
        return RATING_DATA_INSUFFICIENT
    if score >= 85:
        return "卓越"
    if score >= 70:
        return "优质"
    if score >= 60:
        return "良好"
    if score >= 45:
        return "中性"
    return "偏弱"


def is_error_output(output: Any) -> bool:
    """判定是否为 make_unavailable_output / make_failed_output 产出的错误态占位。"""
    return (isinstance(output, dict)
            and isinstance(output.get(ERROR_META_KEY), dict)
            and output[ERROR_META_KEY].get("error_type") in
            (ERROR_TYPE_UNAVAILABLE, ERROR_TYPE_VALIDATION))


def _empty_breakdown() -> Dict[str, None]:
    """所有维度置 None（非 0），防止下游误把不可用分当有效分。"""
    return {name: None for name in DIMENSION_NAMES}


def make_unavailable_output(reason: str) -> Dict[str, Any]:
    """无 API key / provider 调用失败时的标准占位输出。

    total_score=None：绝不回落 legacy 分作为主分；前端据 scoring_method=unavailable 明确提示。
    """
    return {
        "total_score": None,
        "rating": RATING_UNAVAILABLE,
        "confidence": 0.0,
        "breakdown": _empty_breakdown(),
        "summary": "AI 评分当前不可用，未生成评分。",
        "strengths": [],
        "risks": [],
        "missing_data_impact": f"AI 评分不可用（{reason}）：本次未产生 AI 评分，请勿据此判断。",
        "score_drivers": [],
        "evidence_used": [],
        "warnings": [f"ai_unavailable: {reason}"],
        ERROR_META_KEY: {"scoring_method": METHOD_UNAVAILABLE, "validator_status": VALID_FAILED,
                         "ai_generated": False, "error_type": ERROR_TYPE_UNAVAILABLE,
                         "reason": reason, "errors": [reason]},
    }


def make_failed_output(errors: List[str]) -> Dict[str, Any]:
    """AI 有响应但校验失败（无法解析 / 结构非法 / 合规违规）时的标准占位输出。"""
    err_list = [str(e) for e in (errors or [])]
    detail = "; ".join(err_list[:5]) or "未知校验错误"
    return {
        "total_score": None,
        "rating": RATING_UNAVAILABLE,
        "confidence": 0.0,
        "breakdown": _empty_breakdown(),
        "summary": "AI 评分输出未通过校验，已拒绝展示。",
        "strengths": [],
        "risks": [],
        "missing_data_impact": "AI 输出未通过校验，本次未产生可信评分。",
        "score_drivers": [],
        "evidence_used": [],
        "warnings": [f"validation_failed: {detail}"],
        ERROR_META_KEY: {"scoring_method": METHOD_UNAVAILABLE, "validator_status": VALID_FAILED,
                         "ai_generated": False, "error_type": ERROR_TYPE_VALIDATION,
                         "reason": detail, "errors": err_list},
    }
