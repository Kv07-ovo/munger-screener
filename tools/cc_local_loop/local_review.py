#!/usr/bin/env python3
"""CC local review — Phase G: real local-model reviewer (Ollama / LM Studio).

Usage:

    python tools/cc_local_loop/local_review.py <context.md>

What it does:

  * Loads config.json and reads the round's redacted ``context.md``.
  * Detects a local model backend (Ollama, then LM Studio). If neither is
    reachable it DEGRADES TO MOCK.
  * For a real backend it sends the (re-redacted, length-capped) context to the
    model and asks for a strict JSON review, then validates and clamps the
    result. For mock it emits a safe UNSURE template.
  * Writes ``review.md`` and ``next_cc_prompt.md`` next to the input context.md.

Hard safety guarantees (Phase G):

  * MOCK mode is always ``verdict=UNSURE`` — never PASS.
  * Any model verdict other than PASS / NEEDS_FIX / UNSURE, a missing verdict,
    an empty/garbage response, a parse failure, or a model/connection error is
    clamped to UNSURE.
  * PASS is only allowed when the context shows tests actually passed
    (``returncode=0`` and no skip marker); otherwise PASS is downgraded to
    UNSURE.
  * The model only ever returns TEXT. This script never executes a command from
    the model, never edits a file from the model, and never commits / pushes /
    deploys.
  * Every string that reaches the model, stdout, or a written file is passed
    through ``common.redact`` first — no API key / token is ever emitted.
  * Standard library only — no third-party dependency is added.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# Allow ``python tools/cc_local_loop/local_review.py`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402  (path tweak above must run first)

# Windows consoles / pipes often default to GBK; force UTF-8 so redacted status
# lines (and any CJK) never raise UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

MOCK_DISCLAIMER = (
    "本文件由 **mock 模板** 生成，不代表任何真实本地 AI 审查结果。"
    "请勿据此判断代码是否安全或正确。"
)

VALID_VERDICTS = ("PASS", "NEEDS_FIX", "UNSURE")

# Defaults for the new model-related config keys (all overridable in config.json).
DEFAULT_MODEL_TIMEOUT = 60
DEFAULT_MAX_CONTEXT_CHARS = 16000
DEFAULT_TEMPERATURE = 0
_MAX_LIST_ITEMS = 12
_MAX_LINE_CHARS = 500
_MAX_NEXT_PROMPT_CHARS = 1500

PROMPT_PATH = common.TOOL_DIR / "prompts" / "reviewer_system.md"

_ROUND_RE = re.compile(r"(?im)^\s*round(?:[_\s-]?index)?\s*[:=]\s*(\d+)\s*$")

DEFAULT_SYSTEM_PROMPT = (
    "你是一个严格、保守、以证据为先的本地代码审查者。只依据给定的 context.md "
    "（diff、改动范围、测试结果等）判断，不臆测未提供的信息。宁可 UNSURE，不可在"
    "证据不足时给 PASS。不输出任何 API Key / Token / 密钥。不建议 commit / push / "
    "部署，不建议改与任务无关的文件。"
)

# The JSON contract is appended to the system prompt regardless of the prompt
# file's contents, so a edited prompt file can never loosen the output format.
JSON_CONTRACT = (
    "你必须只输出一个 JSON 对象，不要输出任何额外文字、解释或代码块围栏。"
    "字段：\n"
    '  "verdict": 必须是 "PASS" / "NEEDS_FIX" / "UNSURE" 之一；\n'
    '  "evidence": 字符串数组（基于 context 可追溯的证据）；\n'
    '  "risks": 字符串数组（风险 / 边界 / 回归点）；\n'
    '  "next_prompt": 字符串（给 Claude Code 的下一步最小指令）。\n'
    "PASS 的充分必要条件（全部满足才可 PASS，否则给 NEEDS_FIX 或 UNSURE）：\n"
    "  1) context 中有测试通过的证据；2) diff 范围与任务一致；"
    "3) 未违反禁止事项（不 commit/push/部署、不改无关文件、不打印密钥）；"
    "4) 无明显风险。证据不足一律 UNSURE。"
)


# --------------------------------------------------------------------------- #
# Backend detection
# --------------------------------------------------------------------------- #

def detect_backend(cfg) -> tuple[str, str]:
    """Return ``(backend, detail)``. Probe Ollama, then LM Studio, else mock."""
    ollama_url = cfg.get("ollama_url") or ""
    lm_url = cfg.get("lmstudio_url") or ""

    if ollama_url:
        host, port = common.host_port_from_url(ollama_url, 11434)
        if common.probe_tcp(host, port):
            ok, _status = common.probe_http(ollama_url)
            if ok:
                return "ollama", f"detected at {host}:{port}"

    if lm_url:
        host, port = common.host_port_from_url(lm_url, 1234)
        # LM Studio's configured URL is the POST chat endpoint; an open TCP port
        # is a sufficient "is it listening?" signal.
        if common.probe_tcp(host, port):
            return "lmstudio", f"detected at {host}:{port}"

    return "mock", "no local model server detected"


def parse_round_index(context_text) -> str:
    m = _ROUND_RE.search(context_text or "")
    return m.group(1) if m else "unknown"


# --------------------------------------------------------------------------- #
# HTTP (stdlib only) — localhost model endpoints
# --------------------------------------------------------------------------- #

def _is_loopback_url(url) -> bool:
    """True only for localhost / 127.0.0.0/8 / ::1 endpoints."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, TypeError):
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"):
        return True
    return host.startswith("127.") or host.endswith(".localhost")


def _http_post_json(url, payload, timeout) -> dict:
    """POST a JSON body and return the parsed JSON response (loopback only)."""
    if not _is_loopback_url(url):
        raise ValueError("refusing HTTP to a non-loopback host")
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (loopback only)
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def _http_get_json(url, timeout) -> dict:
    if not _is_loopback_url(url):
        raise ValueError("refusing HTTP to a non-loopback host")
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (loopback only)
        return json.loads(resp.read().decode("utf-8", "replace"))


def resolve_ollama_model(base_url, cfg, timeout):
    """Configured model, else the first model from ``/api/tags``, else None."""
    m = cfg.get("ollama_model")
    if isinstance(m, str) and m.strip():
        return m.strip()
    try:
        data = _http_get_json(base_url.rstrip("/") + "/api/tags", min(timeout, 5))
        for entry in (data.get("models") or []):
            name = entry.get("name") or entry.get("model")
            if name:
                return name
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return None


def resolve_lmstudio_model(chat_url, cfg, timeout):
    """Configured model, else the first id from ``/v1/models``, else a default."""
    m = cfg.get("lmstudio_model")
    if isinstance(m, str) and m.strip():
        return m.strip()
    try:
        base = chat_url.split("/chat/completions")[0]
        data = _http_get_json(base.rstrip("/") + "/models", min(timeout, 5))
        arr = data.get("data") or []
        if arr and arr[0].get("id"):
            return arr[0]["id"]
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return "local-model"


def call_ollama(base_url, model, messages, timeout, temperature) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model, "messages": messages, "stream": False,
        "options": {"temperature": temperature},
    }
    resp = _http_post_json(url, payload, timeout)
    return ((resp.get("message") or {}).get("content")) or ""


def call_lmstudio(chat_url, model, messages, timeout, temperature) -> str:
    payload = {
        "model": model, "messages": messages, "stream": False,
        "temperature": temperature,
    }
    resp = _http_post_json(chat_url, payload, timeout)
    choices = resp.get("choices") or []
    if choices:
        return ((choices[0].get("message") or {}).get("content")) or ""
    return ""


# --------------------------------------------------------------------------- #
# Prompt / context handling
# --------------------------------------------------------------------------- #

def load_system_prompt() -> str:
    try:
        txt = PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        txt = ""
    return txt.strip() or DEFAULT_SYSTEM_PROMPT


def truncate_context(text, max_chars) -> tuple[str, bool]:
    text = text or ""
    if max_chars and len(text) > max_chars:
        return (text[:max_chars]
                + f"\n\n...[TRUNCATED {len(text) - max_chars} chars over max_context_chars]\n"), True
    return text, False


def build_messages(system_prompt, context_text, round_index, max_rounds, truncated) -> list:
    trunc = "（注意：context 已被截断，结论需谨慎）" if truncated else ""
    user = (
        f"round_index={round_index}/{max_rounds}{trunc}\n\n"
        "以下是本轮审查上下文 context.md（已脱敏）：\n\n"
        f"{context_text}\n\n"
        f"{JSON_CONTRACT}"
    )
    return [
        {"role": "system", "content": system_prompt + "\n\n" + JSON_CONTRACT},
        {"role": "user", "content": user},
    ]


# --------------------------------------------------------------------------- #
# Model-output parsing & verdict clamping
# --------------------------------------------------------------------------- #

def _coerce_lines(value) -> list:
    if isinstance(value, list):
        out = [str(v).strip() for v in value if str(v).strip()]
    elif isinstance(value, str) and value.strip():
        out = [value.strip()]
    else:
        out = []
    return [ln[:_MAX_LINE_CHARS] for ln in out[:_MAX_LIST_ITEMS]]


def parse_model_output(text):
    """Extract ``{verdict, evidence, risks, next_prompt}`` from a model reply.

    Returns ``None`` if no usable JSON object with a ``verdict`` field is found
    (caller then treats it as UNSURE).
    """
    if not text or not str(text).strip():
        return None
    s = str(text).strip()
    # Strip a leading/trailing ```json fence if present.
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)

    obj = _try_json(s)
    if obj is None:
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            obj = _try_json(s[i:j + 1])
    if not isinstance(obj, dict):
        return None
    if "verdict" not in obj or obj.get("verdict") is None:
        return None  # missing required field -> caller clamps to UNSURE
    return {
        "verdict": obj.get("verdict"),
        "evidence": _coerce_lines(obj.get("evidence")),
        "risks": _coerce_lines(obj.get("risks")),
        "next_prompt": (str(obj.get("next_prompt")).strip()[:_MAX_NEXT_PROMPT_CHARS]
                        if obj.get("next_prompt") else ""),
    }


def _try_json(s):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


def clamp_verdict(value, backend) -> str:
    """Only PASS/NEEDS_FIX/UNSURE survive; mock and anything else -> UNSURE."""
    if backend == "mock":
        return "UNSURE"
    s = str(value or "").strip().upper()
    return s if s in VALID_VERDICTS else "UNSURE"


def tests_passed(context_text) -> bool:
    """True only if the context's REAL test-output section shows a clean pass.

    ``_section`` is fence-aware so a crafted git diff that embeds a fake
    ``## 测试输出`` / ``returncode=0`` inside the (fenced) diff block cannot be
    mistaken for the real test-output section and bypass PASS strictness.
    Failure detection keys on NON-ZERO counts so a passing run's ``0 errors``
    summary never trips a false UNSURE.
    """
    section = _section(context_text, "测试输出")
    if not section:
        return False
    if re.search(r"(?i)\(--no-tests\)|\(dry-run\)|跳过测试|跳过真实测试", section):
        return False
    m = re.search(r"returncode=(-?\d+)", section)
    if not m or m.group(1) != "0":
        return False
    if re.search(r"(?im)\b[1-9]\d*\s+failed\b|\b[1-9]\d*\s+errors?\b|timed_out=True", section):
        return False
    return True


def enforce_pass_strictness(verdict, context_text) -> tuple[str, "str | None"]:
    """Downgrade an unjustified PASS (no in-context test-pass evidence)."""
    if verdict == "PASS" and not tests_passed(context_text):
        return "UNSURE", ("PASS 需要 context 中存在测试通过（returncode=0）的证据，"
                          "但本轮未见，已按严格策略降级为 UNSURE。")
    return verdict, None


def _section(text, header) -> str:
    """Return the body following the real ``## {header}`` up to the next header.

    Fence- and column-aware: only a ``## `` line at column 0 that is NOT inside
    a ``` code fence counts as a section header. context.md is rendered with all
    real headers at column 0 and all captured bodies (git diff, test output, ...)
    wrapped in column-0 ``` fences, while diff/body lines are always prefixed
    (``+``/``-``/space/``@@``). So an injected ``## 测试输出`` living inside the
    fenced git-diff block is correctly ignored.
    """
    target = f"## {header}"
    capturing = False
    in_fence = False
    out = []
    for ln in (text or "").splitlines(keepends=True):
        if ln.startswith("```"):
            in_fence = not in_fence
            if capturing:
                out.append(ln)
            continue
        if not in_fence and ln.startswith("## "):
            if capturing:
                break  # next real header ends the section
            if ln.rstrip() == target:
                capturing = True
                continue
        if capturing:
            out.append(ln)
    return "".join(out)


# --------------------------------------------------------------------------- #
# Review execution
# --------------------------------------------------------------------------- #

def mock_result(detail) -> dict:
    return {
        "backend": "mock",
        "detail": detail,
        "verdict": "UNSURE",
        "evidence": [
            "(mock) 未连接任何本地模型，无法提供真实证据。",
            "本节为模板占位，仅用于打通端到端流程。",
        ],
        "risks": [
            "⚠️ 当前结论无效：不得据此判定代码 PASS。",
            "若需真实审查，请启动 Ollama 或 LM Studio 后重跑。",
        ],
        "next_prompt": "",
        "note": None,
        "truncated": False,
    }


def run_real_review(backend, detail, cfg, context_text, round_index, max_rounds) -> dict:
    """Call the detected local model and return a validated result dict.

    Never raises: any connection / parse / validation problem degrades to a
    safe UNSURE result with a redacted note.
    """
    timeout = _int(cfg.get("model_timeout_seconds"), DEFAULT_MODEL_TIMEOUT)
    max_chars = _int(cfg.get("max_context_chars"), DEFAULT_MAX_CONTEXT_CHARS)
    temperature = cfg.get("model_temperature", DEFAULT_TEMPERATURE)

    if backend == "ollama":
        endpoint = cfg.get("ollama_url") or "http://localhost:11434"
    else:
        endpoint = cfg.get("lmstudio_url") or "http://localhost:1234/v1/chat/completions"

    ctx, truncated = truncate_context(context_text, max_chars)
    ctx = common.redact(ctx)  # re-redact before anything leaves toward the model

    result = {
        "backend": backend, "detail": detail, "verdict": "UNSURE",
        "evidence": [], "risks": [], "next_prompt": "", "note": None,
        "truncated": truncated,
    }

    # SSRF guard: never POST the context anywhere but a loopback endpoint, even
    # if config.json is tampered to point ollama_url/lmstudio_url at a remote
    # host.
    if not _is_loopback_url(endpoint):
        result["note"] = "后端 URL 非 localhost/loopback，已拒绝外发 context 并降级 UNSURE。"
        result["evidence"] = [f"({backend}) 端点非本机回环地址，按安全策略不调用、UNSURE。"]
        return result

    # Phase G invariant: ANY backend / parse problem degrades safely to UNSURE —
    # this function must never raise (a crash would leave review.md unwritten).
    try:
        messages = build_messages(load_system_prompt(), ctx, round_index, max_rounds, truncated)
        if backend == "ollama":
            model = resolve_ollama_model(endpoint, cfg, timeout)
            if not model:
                result["note"] = "未找到可用的 Ollama 模型（/api/tags 为空），按 UNSURE 处理。"
                result["evidence"] = ["(ollama) 探测到服务但无已安装模型，无法真实审查。"]
                return result
            content = call_ollama(endpoint, model, messages, timeout, temperature)
        else:  # lmstudio
            model = resolve_lmstudio_model(endpoint, cfg, timeout)
            content = call_lmstudio(endpoint, model, messages, timeout, temperature)
        result["detail"] = f"{detail}; model={model}"

        parsed = parse_model_output(content)
        if not parsed:
            result["note"] = "模型输出无法解析为合法 JSON（或缺 verdict 字段），已按 UNSURE 处理。"
            result["evidence"] = [f"({backend}) 模型返回不可解析，按安全策略 UNSURE。"]
            return result

        verdict = clamp_verdict(parsed["verdict"], backend)
        verdict, downgrade_note = enforce_pass_strictness(verdict, context_text)
        evidence = [common.redact(x) for x in parsed["evidence"]] or [f"({backend}) 模型未提供 evidence。"]
        risks = [common.redact(x) for x in parsed["risks"]] or ["（模型未提供 risks）"]
        if truncated:
            risks.append("⚠️ 输入 context 超长被截断，结论基于截断内容，请人工复核。")

        result.update({
            "verdict": verdict,
            "evidence": evidence,
            "risks": risks,
            "next_prompt": common.redact(parsed["next_prompt"]),
            "note": downgrade_note,
        })
        return result
    except Exception as exc:  # noqa: BLE001 — invariant: never crash on a backend error
        result["verdict"] = "UNSURE"
        result["note"] = common.redact(f"模型调用/解析异常，已降级 UNSURE：{exc}")[:200]
        result["evidence"] = [f"({backend}) 调用或解析异常，按安全策略 UNSURE。"]
        return result


def _int(value, default) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Output rendering (fixed format)
# --------------------------------------------------------------------------- #

def _bullets(lines) -> str:
    return "".join(f"- {ln}\n" for ln in lines) if lines else "- （无）\n"


def render_review(result, round_index, max_rounds, next_prompt_name) -> str:
    backend = result["backend"]
    verdict = result["verdict"]
    is_mock = backend == "mock"

    if is_mock:
        banner = f"> ⚠️ MOCK 模式：{MOCK_DISCLAIMER}\n\n"
    else:
        banner = f"> ℹ️ 本地后端 {backend}（{result.get('detail', '')}）的真实审查结果。\n\n"
    if result.get("note"):
        banner += f"> 注意：{result['note']}\n\n"

    mock_line = "> MOCK 模式 verdict 恒为 UNSURE，非真实审查结论。\n" if is_mock else ""

    next_block = f"- 详见同目录下 `{next_prompt_name}`（含禁止事项与必须运行的测试）。\n"
    if result.get("next_prompt"):
        first = result["next_prompt"].splitlines()[0] if result["next_prompt"].splitlines() else ""
        if first:
            next_block += f"- 模型建议：{first[:_MAX_LINE_CHARS]}\n"

    return (
        "# Local AI Review\n"
        "\n"
        f"backend={backend}\n"
        f"round_index={round_index}\n"
        f"max_rounds={max_rounds}\n"
        "\n"
        f"{banner}"
        "## Verdict\n"
        "\n"
        f"{verdict}\n"
        "\n"
        "> 可选值: PASS / NEEDS_FIX / UNSURE\n"
        f"{mock_line}"
        "\n"
        "## Evidence\n"
        "\n"
        f"{_bullets(result.get('evidence'))}"
        "\n"
        "## Risks\n"
        "\n"
        f"{_bullets(result.get('risks'))}"
        "\n"
        "## Recommended Next CC Prompt\n"
        "\n"
        f"{next_block}"
        "\n"
        "## Human Decision\n"
        "\n"
        "* [ ] 接受结果\n"
        "* [ ] 复制下一轮提示词给 Claude Code\n"
        "* [ ] 停止工作流\n"
    )


def render_next_prompt(result, round_index, max_rounds, test_command, context_name) -> str:
    backend = result["backend"]
    verdict = result["verdict"]
    is_mock = backend == "mock"

    if is_mock:
        header = (
            f"> ⚠️ 注意：{MOCK_DISCLAIMER}\n"
            "> 这是 mock 模板生成的提示词，不代表真实本地 AI 审查结论。\n\n"
        )
    else:
        header = f"> ℹ️ 来源：本地后端 {backend} 的真实审查输出。\n\n"

    if is_mock:
        issues = "- (mock) 本轮未执行真实审查，无法列出具体问题。\n"
    elif result.get("risks"):
        issues = "".join(f"- {r}\n" for r in result["risks"])
    else:
        issues = f"- ({backend}) 模型未列出具体问题。\n"

    model_step = ""
    if result.get("next_prompt"):
        model_step = f"- 模型建议的下一步：{result['next_prompt'][:_MAX_NEXT_PROMPT_CHARS]}\n"

    return (
        "# Next CC Prompt\n"
        "\n"
        f"{header}"
        f"上一轮 backend={backend}，verdict={verdict}，round_index={round_index}/{max_rounds}。\n"
        f"输入上下文文件：`{context_name}`。\n"
        "\n"
        "## 1. 当前发现的问题\n"
        "\n"
        f"{issues}"
        "\n"
        "## 2. 下一步目标\n"
        "\n"
        f"{model_step}"
        "- 按本轮上下文继续推进既定任务的下一个最小步骤。\n"
        "- 保持改动范围最小、可回滚、可审查。\n"
        "\n"
        "## 3. 禁止事项\n"
        "\n"
        "- 不修改与本任务无关的文件。\n"
        "- 不执行 git commit / push / 部署。\n"
        "- 不在输出中打印任何 API Key / Token / 密钥。\n"
        "- 不擅自进入下一个 Phase。\n"
        "\n"
        "## 4. 必须检查的文件\n"
        "\n"
        "- 本轮实际改动涉及的文件（以 `git status` / `git diff` 为准）。\n"
        "- 与改动直接相关的调用方与测试。\n"
        "\n"
        "## 5. 必须运行的测试\n"
        "\n"
        f"- `{test_command}`\n"
        "- 如改动有针对性测试，补充运行对应用例。\n"
        "\n"
        "## 6. git 安全要求\n"
        "\n"
        "- 动手前先 `git status`：工作区不干净则停止并汇报。\n"
        "- 只新增/修改本任务允许范围内的文件。\n"
        "- 不 commit、不 push、不部署，除非人工明确批准。\n"
        "\n"
        "## 7. 报告格式\n"
        "\n"
        "- 用短标题 + bullet list，避免宽表格/框线表。\n"
        "- 汇报：新增/修改文件列表、运行命令、输出结果、`git status`、是否 commit/push。\n"
        "- 默认使用中文。\n"
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def _within(child: Path, parent: Path) -> bool:
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: python tools/cc_local_loop/local_review.py <context.md>")
        return 2

    context_path = Path(argv[1])
    if not context_path.exists():
        print(common.redact(f"error: context file not found: {context_path}"))
        return 2

    out_dir = context_path.resolve().parent
    if not _within(out_dir, common.TOOL_DIR):
        print(common.redact(
            f"error: refusing to read/write outside the tool dir ({common.TOOL_DIR}): {out_dir}"
        ))
        return 2

    cfg = common.load_config()
    common.ensure_dir(out_dir)

    try:
        context_text = context_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(common.redact(f"error: cannot read context: {exc}"))
        return 2

    round_index = parse_round_index(context_text)
    max_rounds = cfg.get("max_rounds", 5)
    test_command = cfg.get("test_default_command", "")

    backend, detail = detect_backend(cfg)
    if backend == "mock":
        result = mock_result(detail)
    else:
        result = run_real_review(backend, detail, cfg, context_text, round_index, max_rounds)

    review_path = out_dir / "review.md"
    next_prompt_path = out_dir / "next_cc_prompt.md"

    review_path.write_text(
        render_review(result, round_index, max_rounds, next_prompt_path.name),
        encoding="utf-8",
    )
    next_prompt_path.write_text(
        render_next_prompt(result, round_index, max_rounds, test_command, context_path.name),
        encoding="utf-8",
    )

    # Safe status only — redacted, no secrets.
    print(common.redact(f"backend={result['backend']} ({result.get('detail', '')})"))
    print(common.redact(f"verdict={result['verdict']}  round_index={round_index}/{max_rounds}"))
    if result.get("note"):
        print(common.redact(f"note: {result['note']}"))
    print(common.redact(f"wrote: {review_path}"))
    print(common.redact(f"wrote: {next_prompt_path}"))
    if backend == "mock":
        print("note: MOCK mode — verdict forced to UNSURE, not a real review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
