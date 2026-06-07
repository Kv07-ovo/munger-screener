#!/usr/bin/env python3
"""CC local review loop — Phase 1 minimal runnable skeleton.

Usage:

    python tools/cc_local_loop/local_review.py <context.md>

What it does (Phase 1 scope only):

  * Loads config.json.
  * Detects a local model backend (Ollama / LM Studio). If none responds it
    DEGRADES TO MOCK.
  * When detected, Phase 1 only *probes* the backend and lays out the
    placeholder call structure — it does NOT yet perform a real review. Real
    inference lands in Phase 2.
  * Writes ``review.md`` and ``next_cc_prompt.md`` next to the input context.md.

Honesty / safety guarantees baked in:

  * Mock mode is clearly labelled ``backend=mock`` and its verdict is forced to
    UNSURE — never PASS.
  * Phase 1 never emits PASS for any backend, because no real analysis exists
    yet.
  * No API key / token is ever printed (all output is redacted via common.redact).
  * This script never commits, pushes, deploys, or edits business files.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Allow ``python tools/cc_local_loop/local_review.py`` from the repo root by
# making sibling modules importable regardless of the current working dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402  (path tweak above must run first)

MOCK_DISCLAIMER = (
    "本文件由 **mock 模板** 生成，不代表任何真实本地 AI 审查结果。"
    "请勿据此判断代码是否安全或正确。"
)

_ROUND_RE = re.compile(r"(?im)^\s*round(?:[_\s-]?index)?\s*[:=]\s*(\d+)\s*$")


# --------------------------------------------------------------------------- #
# Backend detection
# --------------------------------------------------------------------------- #

def detect_backend(cfg) -> tuple[str, str]:
    """Return ``(backend, detail)``.

    Probe order: Ollama, then LM Studio. Falls back to ``mock`` when neither
    local server answers. In Phase 1 a detected server is only probed — no
    inference is performed.
    """
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
        # is a sufficient "is it listening?" signal for Phase 1.
        if common.probe_tcp(host, port):
            return "lmstudio", f"detected at {host}:{port}"

    return "mock", "no local model server detected"


def compute_verdict(cfg, backend) -> str:
    """Phase 1 verdict policy.

    Phase 1 performs no real analysis, so it never returns PASS. Mock mode reads
    the configured ``mock_verdict`` (UNSURE); detected backends are also UNSURE
    because only a placeholder probe has run.
    """
    if backend == "mock":
        verdict = str(cfg.get("mock_verdict", "UNSURE")).strip() or "UNSURE"
    else:
        verdict = "UNSURE"
    # Hard Phase 1 invariant: never emit PASS for ANY backend, even if a tampered
    # config sets mock_verdict=PASS. Clamp it here so the guarantee can't be
    # configured away.
    if verdict.upper() == "PASS":
        verdict = "UNSURE"
    return verdict


def parse_round_index(context_text) -> str:
    """Pull ``round_index`` from the context, else ``unknown``."""
    m = _ROUND_RE.search(context_text or "")
    return m.group(1) if m else "unknown"


# --------------------------------------------------------------------------- #
# Output rendering
# --------------------------------------------------------------------------- #

def render_review(backend, detail, verdict, round_index, max_rounds, next_prompt_name) -> str:
    is_mock = backend == "mock"

    banner = ""
    if is_mock:
        banner = f"> ⚠️ MOCK 模式：{MOCK_DISCLAIMER}\n\n"
    else:
        banner = (
            f"> ℹ️ 已探测到本地后端（{backend}，{detail}），"
            "但 Phase 1 仅做探测与占位调用，尚未执行真实审查。\n\n"
        )

    if is_mock:
        evidence = (
            "- (mock) 未连接任何本地模型，无法提供真实证据。\n"
            "- 本节内容为模板占位，仅用于打通端到端流程。\n"
        )
        risks = (
            "- ⚠️ 当前结论无效：不得据此判定代码 PASS。\n"
            "- 若需真实审查，请启动 Ollama 或 LM Studio 后重跑。\n"
        )
    else:
        evidence = (
            f"- 已探测到 {backend}（{detail}）。\n"
            "- (Phase 1 占位) 真实模型调用与证据提取将在 Phase 2 接入。\n"
        )
        risks = (
            "- ⚠️ Phase 1 尚未做真实分析，本轮结论仅为占位，不能作为通过依据。\n"
        )

    # Fixed format: the three metadata lines come immediately after the title
    # (so a downstream "read first N lines" parser can rely on them); the mock /
    # probe banner follows right below, still near the top.
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
        "> Phase 1 永远不会输出 PASS（尚未实现真实审查逻辑）。\n"
        "\n"
        "## Evidence\n"
        "\n"
        f"{evidence}"
        "\n"
        "## Risks\n"
        "\n"
        f"{risks}"
        "\n"
        "## Recommended Next CC Prompt\n"
        "\n"
        f"- 详见同目录下 `{next_prompt_name}`。\n"
        "- 该文件包含下一轮交给 Claude Code 的完整提示词（含禁止事项与必须运行的测试）。\n"
        "\n"
        "## Human Decision\n"
        "\n"
        "* [ ] 接受结果\n"
        "* [ ] 复制下一轮提示词给 Claude Code\n"
        "* [ ] 停止工作流\n"
    )


def render_next_prompt(backend, verdict, round_index, max_rounds, test_command, context_name) -> str:
    is_mock = backend == "mock"

    header = ""
    if is_mock:
        header = (
            f"> ⚠️ 注意：{MOCK_DISCLAIMER}\n"
            "> 这是 mock 模板生成的提示词，不代表真实本地 AI 审查结论。\n\n"
        )
    else:
        header = (
            f"> ℹ️ 来源：本地后端 {backend} 的 Phase 1 占位输出（尚未真实推理）。\n\n"
        )

    issues = (
        "- (mock) 本轮未执行真实审查，无法列出具体问题。\n"
        if is_mock
        else f"- (Phase 1 占位) {backend} 已就绪，但真实问题清单将在 Phase 2 由模型给出。\n"
    )

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
    """True iff ``child`` is ``parent`` or sits inside it (after resolving)."""
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
        print(f"error: context file not found: {context_path}")
        return 2

    # Safety boundary: this tool must only ever read/write inside
    # tools/cc_local_loop/. Refuse anything outside (e.g. ../../api/x.md) BEFORE
    # creating directories or writing files.
    out_dir = context_path.resolve().parent
    if not _within(out_dir, common.TOOL_DIR):
        print(common.redact(
            "error: refusing to read/write outside the tool dir "
            f"({common.TOOL_DIR}): {out_dir}"
        ))
        return 2

    cfg = common.load_config()
    common.ensure_dir(out_dir)

    try:
        context_text = context_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(common.redact(f"error: cannot read context: {exc}"))
        return 2

    backend, detail = detect_backend(cfg)
    verdict = compute_verdict(cfg, backend)
    round_index = parse_round_index(context_text)
    max_rounds = cfg.get("max_rounds", 5)
    test_command = cfg.get("test_default_command", "")

    review_path = out_dir / "review.md"
    next_prompt_path = out_dir / "next_cc_prompt.md"

    review_md = render_review(
        backend, detail, verdict, round_index, max_rounds, next_prompt_path.name
    )
    next_prompt_md = render_next_prompt(
        backend, verdict, round_index, max_rounds, test_command, context_path.name
    )

    review_path.write_text(review_md, encoding="utf-8")
    next_prompt_path.write_text(next_prompt_md, encoding="utf-8")

    # Safe status only — everything is redacted, no secrets, no API keys.
    print(common.redact(f"backend={backend} ({detail})"))
    print(common.redact(f"verdict={verdict}  round_index={round_index}/{max_rounds}"))
    print(common.redact(f"wrote: {review_path}"))
    print(common.redact(f"wrote: {next_prompt_path}"))
    if backend == "mock":
        print("note: MOCK mode — verdict forced to UNSURE, not a real review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
