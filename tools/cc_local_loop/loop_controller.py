#!/usr/bin/env python3
"""CC local loop controller — Phase D, ``safe_auto`` semi-automatic mode.

This is the deterministic glue that wires the existing pieces together:

    cc-intake -> cc-plan -> cc-implement -> cc-collect -> cc-local-review
    -> verdict gate -> (<= 5 rounds) -> stop and wait for a human.

It is *semi-automatic by design*. It never edits business code, never calls
Claude to change code, and never commits / pushes / deploys. It only:

  * creates a per-task run directory under ``tools/cc_local_loop/runs/``;
  * maintains ``STATE.json`` (round index, status, last verdict, flags);
  * generates ``task.md`` / ``plan_prompt.md`` / ``implement_prompt.md`` (these
    are prompts for a human / Claude to act on — the controller does NOT run
    them);
  * collects a redacted ``context.md`` (git status/diff/log + test output);
  * calls ``local_review.py`` and parses the PASS / NEEDS_FIX / UNSURE verdict;
  * applies the verdict gate and stops at the right places;
  * writes ``final_report.md`` and caps the loop at ``max_rounds = 5``.

Hard guarantees baked into the code (not just config):

  * There is NO code path that runs ``git commit`` / ``git push`` / any deploy
    command. The only subprocesses are read-only git (status/diff/log/rev-parse/
    branch), the configured test command, and ``python local_review.py``.
  * Every file write is confined to the task's ``runs/<task>/`` subtree; writes
    outside the runs dir are refused.
  * A ``mock`` backend or an ``UNSURE`` verdict always stops for a human — the
    loop never auto-advances on them.
  * A ``PASS`` verdict stops and waits for human acceptance; it never commits.
  * The only file the controller ever removes is its own ``.active_task`` lock,
    and only via ``finish`` from a safe terminal state. ``allow_delete`` (which
    governs business / run-artifact files) stays ``false``.

Commands:

    python tools/cc_local_loop/loop_controller.py start "<task description>"
    python tools/cc_local_loop/loop_controller.py round
    python tools/cc_local_loop/loop_controller.py collect [--no-tests]
    python tools/cc_local_loop/loop_controller.py review
    python tools/cc_local_loop/loop_controller.py decide
    python tools/cc_local_loop/loop_controller.py status
    python tools/cc_local_loop/loop_controller.py finish
    python tools/cc_local_loop/loop_controller.py dry-run "<task description>"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Allow ``python tools/cc_local_loop/loop_controller.py`` from the repo root by
# making sibling modules importable regardless of the current working dir
# (mirrors local_review.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402  (path tweak above must run first)

# On Windows the console code page is often GBK; force UTF-8 so the Chinese
# status output never raises UnicodeEncodeError when captured or redirected.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

TOOL_DIR = common.TOOL_DIR
LOCAL_REVIEW = TOOL_DIR / "local_review.py"


def _detect_repo_root() -> Path:
    """Find the repo root by walking up for a ``.git`` marker.

    Deliberately avoids a subprocess so importing this module has no side
    effects (better testability, works in non-git / sandbox environments) and
    so a repo path is never accidentally rewritten by output redaction. Falls
    back to two levels above the tool dir (``tools/cc_local_loop`` -> repo
    root).
    """
    for cand in (TOOL_DIR, *TOOL_DIR.parents):
        if (cand / ".git").exists():
            return cand
    return TOOL_DIR.parent.parent


REPO_ROOT = _detect_repo_root()


# --------------------------------------------------------------------------- #
# Status constants & state machine
# --------------------------------------------------------------------------- #

S_STARTED = "STARTED"
S_ROUND_READY = "ROUND_READY"
S_ROUND_PENDING = "ROUND_PENDING"
S_COLLECTED = "COLLECTED"
S_REVIEWED = "REVIEWED"
S_WAIT_ACCEPT = "WAITING_HUMAN_ACCEPTANCE"
S_WAIT_DECISION = "WAITING_HUMAN_DECISION"
S_MAX_ROUNDS = "MAX_ROUNDS_REACHED"
S_FINISHED = "FINISHED"

# `finish` is only allowed from these safe / terminal states.
SAFE_FINISH_STATES = {S_WAIT_ACCEPT, S_WAIT_DECISION, S_MAX_ROUNDS, S_FINISHED}

VALID_VERDICTS = ("PASS", "NEEDS_FIX", "UNSURE")

# Exit codes: 0 ok, 2 usage error, 3 safety/gate refusal.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GATE = 3


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slugify(text, maxlen: int = 40) -> str:
    """Filesystem-safe slug; keeps unicode word chars (incl. CJK)."""
    s = (text or "").strip().lower()
    s = re.sub(r"[^\w]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"_+", "-", s).strip("-")
    return (s[:maxlen].strip("-") or "task")


def _within(child: Path, parent: Path) -> bool:
    """True iff ``child`` is ``parent`` or sits inside it (after resolving)."""
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


def _assert_within(path: Path, runs_dir: Path) -> None:
    """Refuse any write target that escapes the runs subtree."""
    if not _within(path, runs_dir):
        raise RuntimeError(f"refusing to write outside runs dir: {path}")


def _safe_task_id(task_id) -> bool:
    if not task_id:
        return False
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        return False
    return task_id not in (".", "..")


def _read_if(path: Path):
    try:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return None


def info(msg: str) -> None:
    print(common.redact(msg))


# --------------------------------------------------------------------------- #
# Config-derived paths & flags
# --------------------------------------------------------------------------- #

def resolve_runs_dir(cfg) -> Path:
    """Resolve ``runs_dir`` from config, clamped inside the tool dir."""
    default = TOOL_DIR / "runs"
    raw = cfg.get("runs_dir")
    if not raw:
        return default
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        p = p.resolve()
    except OSError:
        return default
    # Safety clamp: never operate outside tools/cc_local_loop/.
    return p if _within(p, TOOL_DIR) else default


def effective_max_rounds(cfg) -> int:
    """Config ``max_rounds`` capped hard at 5 (Phase D invariant)."""
    try:
        n = int(cfg.get("max_rounds", 5) or 5)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, 5))


def safe_flags(cfg) -> dict:
    """The safe_auto policy flags, read from config but always safe."""
    return {
        "safe_auto": str(cfg.get("loop_mode", "safe_auto")).lower() == "safe_auto",
        "auto_commit": bool(cfg.get("auto_commit", False)),
        "auto_push": bool(cfg.get("auto_push", False)),
        "auto_deploy": bool(cfg.get("auto_deploy", False)),
        "allow_delete": bool(cfg.get("allow_delete", False)),
    }


# --------------------------------------------------------------------------- #
# Active task lock & state
# --------------------------------------------------------------------------- #

def active_task_path(runs_dir: Path) -> Path:
    return runs_dir / ".active_task"


def read_active_task(runs_dir: Path):
    """Return the active ``task_id``, or ``None``.

    ``.active_task`` is JSON (``{"task_id": ..., "run_dir": ...}``) so the
    Phase C Stop hook (collect_onstop.py, which Phase D must not modify) can
    resolve its snapshot dir from ``run_dir``. A bare-string file is still
    accepted for backward compatibility.
    """
    val = _read_if(active_task_path(runs_dir))
    if val is None:
        return None
    val = val.strip()
    if not val:
        return None
    try:
        data = json.loads(val)
        if isinstance(data, dict):
            tid = data.get("task_id")
            return tid.strip() if isinstance(tid, str) and tid.strip() else None
    except (json.JSONDecodeError, ValueError):
        pass
    return val  # legacy plain-text task_id


def write_active_task(runs_dir: Path, task_id: str, task_dir: Path) -> None:
    """Write the active-task lock as JSON understood by the Stop hook.

    ``run_dir`` is the absolute task dir, so collect_onstop.py writes its
    snapshot to ``<task_dir>/stop_hook_snapshots.log`` — exactly where
    ``do_collect`` later reads it from.
    """
    common.ensure_dir(runs_dir)
    payload = {"task_id": task_id, "run_dir": str(task_dir.resolve())}
    active_task_path(runs_dir).write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def remove_active_task(runs_dir: Path) -> bool:
    """Remove ONLY the controller's own ``.active_task`` lock.

    This is the single sanctioned deletion in the whole controller and is
    distinct from ``allow_delete`` (which governs business / run-artifact
    files, and stays false). Used by ``finish`` from a safe terminal state.
    """
    p = active_task_path(runs_dir)
    if p.exists():
        p.unlink()
        return True
    return False


def state_path(task_dir: Path) -> Path:
    return task_dir / "STATE.json"


def load_state(task_dir: Path) -> dict:
    with open(state_path(task_dir), "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(task_dir: Path, runs_dir: Path, state: dict) -> None:
    p = state_path(task_dir)
    _assert_within(p, runs_dir)
    state["updated_at"] = _now_iso()
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def new_state(task_id, description, cfg) -> dict:
    flags = safe_flags(cfg)
    state = {
        "task_id": task_id,
        "task_description": description,
        "created_at": _now_iso(),
        "base_commit": git_head(),
        "branch": git_branch(),
        "round_index": 1,
        "max_rounds": effective_max_rounds(cfg),
        "status": S_STARTED,
        "last_verdict": None,
        "backend": None,
        "safe_auto": flags["safe_auto"],
        "auto_commit": flags["auto_commit"],
        "auto_push": flags["auto_push"],
        "auto_deploy": flags["auto_deploy"],
        "allow_delete": flags["allow_delete"],
        # Extra (allowed) bookkeeping fields:
        "loop_mode": cfg.get("loop_mode", "safe_auto"),
        "test_command": cfg.get("test_default_command", ""),
        "history": [],
    }
    return state


# --------------------------------------------------------------------------- #
# Read-only git (NO commit / push / deploy anywhere in this file)
# --------------------------------------------------------------------------- #

def _git(args) -> dict:
    return common.run_command(["git", *args], cwd=REPO_ROOT)


def git_head() -> str:
    return (_git(["rev-parse", "HEAD"]).get("stdout") or "").strip()


def git_branch() -> str:
    return (_git(["rev-parse", "--abbrev-ref", "HEAD"]).get("stdout") or "").strip()


def _runs_rel_posix(runs_dir: Path):
    try:
        return runs_dir.resolve().relative_to(REPO_ROOT.resolve()).as_posix() + "/"
    except (ValueError, OSError):
        return None


def working_tree_clean(runs_dir: Path):
    """``(clean, dirty_lines)`` ignoring anything under the runs dir."""
    out = _git(["status", "--porcelain"]).get("stdout") or ""
    runs_prefix = _runs_rel_posix(runs_dir)
    dirty = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        path = ln[3:] if len(ln) > 3 else ln
        if " -> " in path:  # rename: "orig -> new" (porcelain v1)
            path = path.split(" -> ", 1)[1]
        path_posix = path.strip().strip('"').replace("\\", "/")
        if runs_prefix and path_posix.startswith(runs_prefix):
            continue
        dirty.append(ln)
    return (not dirty), dirty


# --------------------------------------------------------------------------- #
# Active-task resolution + branch gate (for round/collect/review/decide)
# --------------------------------------------------------------------------- #

def require_active(runs_dir: Path):
    """Return ``(task_dir, state, None)`` or ``(None, None, error)``."""
    task_id = read_active_task(runs_dir)
    if not task_id:
        return None, None, "没有活跃任务（先运行 `start`）。"
    if not _safe_task_id(task_id):
        return None, None, f"非法的 active_task 名: {task_id!r}"
    task_dir = runs_dir / task_id
    if not _within(task_dir, runs_dir) or not task_dir.exists():
        return None, None, f"活跃任务目录缺失: {task_id}"
    if not state_path(task_dir).exists():
        return None, None, f"STATE.json 缺失: {task_id}"
    try:
        state = load_state(task_dir)
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, f"无法读取 STATE.json: {exc}"
    return task_dir, state, None


def branch_gate(state):
    """``(ok, current_branch)`` — current branch must equal STATE.branch."""
    cur = git_branch()
    return (cur == state.get("branch")), cur


# --------------------------------------------------------------------------- #
# Round dirs
# --------------------------------------------------------------------------- #

def round_dir(task_dir: Path, n: int) -> Path:
    return task_dir / f"round_{int(n):02d}"


# --------------------------------------------------------------------------- #
# Prompt / report renderers
# --------------------------------------------------------------------------- #

FORBIDDEN_BLOCK = (
    "## 禁止事项\n"
    "\n"
    "- 不修改与本任务无关的文件。\n"
    "- 不执行 git commit / git push / 任何部署动作（由人工显式决定）。\n"
    "- 不删除文件。\n"
    "- 不在输出中打印任何 API Key / Token / 密钥。\n"
    "- 不擅自进入下一个 Phase。\n"
)


def render_task_md(state: dict) -> str:
    return (
        "# 任务卡（Task Card）\n"
        "\n"
        f"task_id: {state['task_id']}\n"
        f"branch: {state['branch']}\n"
        f"base_commit: {state['base_commit']}\n"
        f"created_at: {state['created_at']}\n"
        f"max_rounds: {state['max_rounds']}\n"
        f"mode: safe_auto（半自动；实现由人工/Claude 按 cc-implement 规则执行）\n"
        "\n"
        "## 1. 目标\n"
        "\n"
        f"{common.redact(state['task_description'])}\n"
        "\n"
        "## 2. 范围（由 cc-plan 细化）\n"
        "\n"
        "- 仅做完成目标所需的最小改动。\n"
        "- 改动可回滚、可测试、可审查。\n"
        "\n"
        "## 3. 验收标准\n"
        "\n"
        "- 目标行为达成且有测试 / 证据支撑。\n"
        "- 本地审查 verdict 为 PASS，并经人工验收。\n"
        "\n"
        "## 4. 受影响模块\n"
        "\n"
        "- 待 cc-plan 在 plan_prompt.md 指引下产出 plan.md 后确定。\n"
        "\n"
        + FORBIDDEN_BLOCK
    )


def render_plan_prompt(state: dict, n: int) -> str:
    return (
        "# Plan Prompt（cc-plan）\n"
        "\n"
        f"round_index: {n}/{state['max_rounds']}\n"
        f"task_id: {state['task_id']}\n"
        "\n"
        "> safe_auto 半自动：本文件是给 Claude / 人工的“计划”提示词，"
        "loop_controller 不会自动执行它。\n"
        "\n"
        "## 目标\n"
        "\n"
        f"{common.redact(state['task_description'])}\n"
        "\n"
        "## 产出要求（plan.md）\n"
        "\n"
        "- 文件级、最小步骤、可回滚、可测试的实施计划。\n"
        "- 明确：将新增/修改哪些文件、每步做什么、如何验证、如何回滚。\n"
        "- 标注与本任务允许范围之外的“禁止改动”清单。\n"
        "\n"
        + FORBIDDEN_BLOCK
    )


def render_implement_prompt(state: dict, n: int, carry_in: str, test_command: str) -> str:
    carry_block = ""
    if carry_in and carry_in.strip():
        carry_block = (
            "## 0. 上一轮审查给出的下一步提示（next_cc_prompt.md）\n"
            "\n"
            "```\n"
            f"{common.redact(carry_in).strip()}\n"
            "```\n"
            "\n"
        )
    else:
        carry_block = (
            "## 0. 本轮输入\n"
            "\n"
            "- 依据 task.md 与本轮 plan.md（如已产出）实施当前最小步骤。\n"
            "\n"
        )

    return (
        "# Implement Prompt（cc-implement）\n"
        "\n"
        f"round_index: {n}/{state['max_rounds']}\n"
        f"task_id: {state['task_id']}\n"
        "\n"
        "> safe_auto 半自动：本文件是给 Claude / 人工的“实现”提示词，"
        "loop_controller 不会自动改代码、不会自动调用 Claude。\n"
        "\n"
        + carry_block
        + "## 1. 本轮目标\n"
        "\n"
        "- 严格按 plan.md / 上一轮提示，实施当前这一轮的最小改动。\n"
        "- 保持范围最小、可回滚、可审查。\n"
        "\n"
        "## 2. 必须运行的测试\n"
        "\n"
        f"- `{test_command}`\n"
        "- 如改动有针对性用例，补充运行对应测试。\n"
        "\n"
        "## 3. 完成后\n"
        "\n"
        "- 运行 `loop_controller.py collect` 收集本轮上下文（脱敏）。\n"
        "- 再运行 `review` 与 `decide`，由 verdict 闸门决定走停。\n"
        "\n"
        + FORBIDDEN_BLOCK
    )


def render_context(state, n, head, status, diffstat, diff, log5, test_cmd,
                   test_out, task_md, plan_md, impl_md, hook_snap, dry_run) -> str:
    def block(title, body):
        body = (body or "").rstrip()
        return f"## {title}\n\n```\n{body}\n```\n\n" if body else f"## {title}\n\n(空)\n\n"

    banner = ""
    if dry_run:
        banner = "> ⚠️ DRY-RUN：模拟运行，未改业务代码、未调用 Claude、未运行真实测试。\n\n"

    parts = [
        "# Round Context\n\n",
        banner,
        f"round_index: {n}\n",
        f"task_id: {state['task_id']}\n",
        f"branch: {state['branch']}\n",
        f"base_commit: {state['base_commit']}\n",
        f"current_head: {head}\n",
        f"generated_at: {_now_iso()}\n",
        "\n",
        f"## pwd\n\n```\n{REPO_ROOT}\n```\n\n",
        block("git status --porcelain", status),
        block("git diff --stat", diffstat),
        block("git diff", diff),
        block("git log --oneline -5", log5),
        f"## 最近 commit\n\n```\n{head}\n```\n\n",
        f"## 测试命令\n\n```\n{test_cmd}\n```\n\n",
        block("测试输出", test_out),
        f"## task.md\n\n{task_md.strip()}\n\n" if task_md else "## task.md\n\n(缺失)\n\n",
    ]
    if plan_md:
        parts.append(f"## plan.md\n\n{plan_md.strip()}\n\n")
    if impl_md:
        parts.append(f"## implement_prompt.md\n\n{impl_md.strip()}\n\n")
    if hook_snap:
        parts.append(block("hook 快照（stop_hook_snapshots.log）", hook_snap))
    return "".join(parts)


def render_final_report(state: dict, final_status: str) -> str:
    rows = []
    for h in state.get("history", []):
        rows.append(
            f"- round {h.get('round')}: verdict={h.get('verdict')} "
            f"backend={h.get('backend')} at {h.get('at')}"
        )
    history = "\n".join(rows) if rows else "- （无审查记录）"

    return (
        "# Final Report\n"
        "\n"
        f"task_id: {state['task_id']}\n"
        f"branch: {state['branch']}\n"
        f"base_commit: {state['base_commit']}\n"
        f"current_head: {git_head()}\n"
        f"rounds_run: {state['round_index']} / {state['max_rounds']}\n"
        f"final_status: {final_status}\n"
        f"last_verdict: {state.get('last_verdict')}\n"
        f"backend: {state.get('backend')}\n"
        f"generated_at: {_now_iso()}\n"
        "\n"
        "## 目标\n"
        "\n"
        f"{common.redact(state['task_description'])}\n"
        "\n"
        "## 各轮审查记录\n"
        "\n"
        f"{history}\n"
        "\n"
        "## 安全声明\n"
        "\n"
        "- 本控制器未执行 git commit、git push 或任何部署动作。\n"
        "- 未修改任何业务文件；产物只写在本任务 runs/ 目录内。\n"
        "- 是否提交 / 推进，均需人工显式决定。\n"
        "\n"
        "## 人工待办\n"
        "\n"
        "* [ ] 复核本轮改动与测试结果\n"
        "* [ ] 决定是否提交 / 继续 / 终止\n"
    )


# --------------------------------------------------------------------------- #
# Verdict parsing (from local_review.py's review.md)
# --------------------------------------------------------------------------- #

def parse_review(review_text: str):
    """Return ``(backend, verdict)`` parsed from a review.md."""
    backend = None
    m = re.search(r"(?im)^backend=(\S+)\s*$", review_text or "")
    if m:
        backend = m.group(1).strip()

    verdict = None
    lines = (review_text or "").splitlines()
    idx = -1
    for i, ln in enumerate(lines):
        if ln.strip().lower() == "## verdict":
            idx = i
            break
    if idx >= 0:
        for ln in lines[idx + 1:]:
            t = ln.strip()
            if not t:
                continue
            if t in VALID_VERDICTS:
                verdict = t
                break
            if t.startswith("#"):  # next heading, stop
                break
    if verdict is None:
        m2 = re.search(r"(?m)^\s*(PASS|NEEDS_FIX|UNSURE)\s*$", review_text or "")
        if m2:
            verdict = m2.group(1)
    return backend, verdict


# --------------------------------------------------------------------------- #
# Core steps (operate directly on task_dir/state; reused by dry-run)
# --------------------------------------------------------------------------- #

def do_round(task_dir, state, runs_dir, cfg):
    """Ensure the current round's dir + prompts exist. Idempotent."""
    n = int(state["round_index"])
    rd = round_dir(task_dir, n)
    _assert_within(rd, runs_dir)
    common.ensure_dir(rd)
    test_cmd = state.get("test_command") or cfg.get("test_default_command", "")

    created = []
    if n == 1:
        pp = rd / "plan_prompt.md"
        if not pp.exists():
            pp.write_text(render_plan_prompt(state, n), encoding="utf-8")
            created.append(pp.name)

    ip = rd / "implement_prompt.md"
    if not ip.exists():
        carry = ""
        if n > 1:
            carry = _read_if(round_dir(task_dir, n - 1) / "next_cc_prompt.md") or ""
        ip.write_text(render_implement_prompt(state, n, carry, test_cmd), encoding="utf-8")
        created.append(ip.name)

    state["status"] = S_ROUND_READY
    return rd, created


def do_collect(task_dir, state, runs_dir, cfg, run_tests=True, dry_run=False):
    """Build the redacted context.md for the current round."""
    # Precondition: only collect while a round is open. Refusing from REVIEWED /
    # terminal states keeps the state machine forward-only (no silent regress to
    # COLLECTED that would drop an already-recorded verdict).
    if state.get("status") not in (S_ROUND_READY, S_ROUND_PENDING, S_COLLECTED):
        return None, (f"当前状态 {state.get('status')} 不允许 collect；"
                      "先运行 `round` 准备本轮。")
    n = int(state["round_index"])
    rd = round_dir(task_dir, n)
    if not rd.exists():
        return None, "本轮 round 目录不存在；先运行 `round`。"
    _assert_within(rd, runs_dir)

    status = _git(["status", "--porcelain"]).get("stdout") or ""
    diffstat = _git(["diff", "--stat"]).get("stdout") or ""
    diff = _git(["diff"]).get("stdout") or ""
    log5 = _git(["log", "--oneline", "-5"]).get("stdout") or ""
    head = git_head()
    test_cmd = state.get("test_command") or cfg.get("test_default_command", "")

    if run_tests and test_cmd and not dry_run:
        tr = common.run_command(test_cmd, cwd=REPO_ROOT)
        test_out = (
            f"$ {test_cmd}\n"
            f"(returncode={tr['returncode']}, timed_out={tr['timed_out']})\n"
            f"{tr.get('stdout','')}\n{tr.get('stderr','')}"
        )
    elif dry_run:
        test_out = "(dry-run) 跳过真实测试。"
    else:
        test_out = "(--no-tests) 跳过测试。"

    task_md = _read_if(task_dir / "task.md")
    plan_md = _read_if(rd / "plan.md") or _read_if(task_dir / "plan.md")
    impl_md = _read_if(rd / "implement_prompt.md")
    hook_snap = _read_if(task_dir / "stop_hook_snapshots.log")

    content = render_context(
        state, n, head, status, diffstat, diff, log5, test_cmd,
        test_out, task_md, plan_md, impl_md, hook_snap, dry_run,
    )
    # Defence in depth: redact the whole assembled document once more.
    content = common.redact(content)

    out = rd / "context.md"
    _assert_within(out, runs_dir)
    out.write_text(content, encoding="utf-8")
    state["status"] = S_COLLECTED
    return out, None


def do_review(task_dir, state, runs_dir, cfg):
    """Run local_review.py on this round's context.md and parse the verdict.

    Returns ``(backend, verdict, error)`` where ``error`` is ``None`` on
    success. On ANY failure (non-zero exit, missing review.md, unparseable
    verdict) the state machine is left UNTOUCHED and an error string is
    returned, so a broken review can never silently advance status to REVIEWED
    or double-append to history.
    """
    if state.get("status") != S_COLLECTED:
        return None, None, (f"当前状态 {state.get('status')} 不允许 review；"
                            "先运行 `collect`。")
    n = int(state["round_index"])
    rd = round_dir(task_dir, n)
    ctx = rd / "context.md"
    if not ctx.exists():
        return None, None, "本轮 context.md 不存在；先运行 `collect`。"

    res = common.run_command([sys.executable, str(LOCAL_REVIEW), str(ctx)], cwd=REPO_ROOT)
    rc = res.get("returncode")
    review_path = rd / "review.md"
    review_text = _read_if(review_path) or ""
    backend, verdict = parse_review(review_text)

    # Defensive invariant: a mock backend can never yield PASS.
    if backend == "mock" and verdict == "PASS":
        verdict = "UNSURE"

    if rc != 0 or not review_path.exists() or verdict not in VALID_VERDICTS:
        # Failure path: do NOT mutate the state machine; surface returncode +
        # stderr (not a stdout-prefix heuristic) so the failure is visible.
        return backend, verdict, (
            f"local_review 未产出有效结果（returncode={rc}, verdict={verdict}）；"
            f"请人工检查 {review_path}。stderr: {(res.get('stderr') or '')[:300]}"
        )

    state["backend"] = backend
    state["last_verdict"] = verdict
    state["status"] = S_REVIEWED
    state.setdefault("history", []).append(
        {"round": n, "verdict": verdict, "backend": backend, "at": _now_iso()}
    )
    return backend, verdict, None


def do_decide(task_dir, state, runs_dir, cfg):
    """Apply the verdict gate. Returns ``(new_status, message, advanced)``."""
    # Precondition: only decide straight after a successful review. This makes
    # decide non-repeatable, so a second call can never double-increment
    # round_index or burn through the round budget.
    if state.get("status") != S_REVIEWED:
        return state.get("status"), (
            f"当前状态 {state.get('status')} 不允许 decide；先运行 `review`。"), False

    n = int(state["round_index"])
    # Re-clamp the hard cap on every decide so a tampered STATE.json can never
    # push max_rounds above 5.
    max_rounds = min(int(state["max_rounds"]), 5)
    state["max_rounds"] = max_rounds
    backend = state.get("backend")
    verdict = state.get("last_verdict")

    if verdict not in VALID_VERDICTS:
        return state.get("status"), "尚无有效 verdict；先运行 `review`。", False

    # A mock backend can never be trusted to advance the loop: coerce ANY mock
    # verdict to UNSURE so mock always stops for a human (covers mock+PASS and
    # mock+NEEDS_FIX alike).
    if backend == "mock" and verdict != "UNSURE":
        verdict = "UNSURE"
        state["last_verdict"] = "UNSURE"

    if verdict == "PASS":
        state["status"] = S_WAIT_ACCEPT
        (task_dir / "final_report.md").write_text(
            render_final_report(state, S_WAIT_ACCEPT), encoding="utf-8")
        return S_WAIT_ACCEPT, "PASS → 停止，等待人工验收（不自动 commit）。", False

    if verdict == "UNSURE":
        state["status"] = S_WAIT_DECISION
        return S_WAIT_DECISION, "UNSURE（或 mock）→ 停止，等待人工判断。", False

    # verdict == NEEDS_FIX
    if n >= max_rounds:
        state["status"] = S_MAX_ROUNDS
        (task_dir / "final_report.md").write_text(
            render_final_report(state, S_MAX_ROUNDS), encoding="utf-8")
        return S_MAX_ROUNDS, f"NEEDS_FIX 且已达 max_rounds={max_rounds} → 停止。", False

    # Prepare the next round's implement_prompt.md (no Claude call).
    new_n = n + 1
    state["round_index"] = new_n
    nd = round_dir(task_dir, new_n)
    _assert_within(nd, runs_dir)
    common.ensure_dir(nd)
    carry = _read_if(round_dir(task_dir, n) / "next_cc_prompt.md") or ""
    test_cmd = state.get("test_command") or cfg.get("test_default_command", "")
    ip = nd / "implement_prompt.md"
    if not ip.exists():
        ip.write_text(render_implement_prompt(state, new_n, carry, test_cmd), encoding="utf-8")
    state["status"] = S_ROUND_PENDING
    return S_ROUND_PENDING, f"NEEDS_FIX → 进入 round {new_n}（已备好 implement_prompt.md，未调用 Claude）。", True


# --------------------------------------------------------------------------- #
# Next-step hints
# --------------------------------------------------------------------------- #

NEXT_HINT = {
    S_STARTED: "运行 `round` 准备本轮计划/实现提示。",
    S_ROUND_READY: "按 implement_prompt.md 实施（人工/Claude），完成后运行 `collect`。",
    S_ROUND_PENDING: "运行 `round`（幂等）确认本轮，再按 implement_prompt.md 实施，然后 `collect`。",
    S_COLLECTED: "运行 `review` 调用本地审查。",
    S_REVIEWED: "运行 `decide` 应用 verdict 闸门。",
    S_WAIT_ACCEPT: "PASS：等待人工验收；可运行 `finish` 收尾（不会 commit）。",
    S_WAIT_DECISION: "UNSURE：等待人工判断；可运行 `finish` 收尾或人工处置后重启回环。",
    S_MAX_ROUNDS: "已达最大轮次：人工复核；可运行 `finish` 收尾。",
    S_FINISHED: "任务已结束。",
}


# --------------------------------------------------------------------------- #
# CLI command handlers
# --------------------------------------------------------------------------- #

def cmd_start(args) -> int:
    cfg = common.load_config()
    runs_dir = resolve_runs_dir(cfg)
    common.ensure_dir(runs_dir)

    description = (args.description or "").strip()
    if not description:
        info("error: start 需要任务描述。")
        return EXIT_USAGE

    # Gate: working tree must be clean (ignoring runs/ artefacts).
    clean, dirty = working_tree_clean(runs_dir)
    if not clean:
        info("✗ 工作区不干净，拒绝开工。脏文件：")
        for ln in dirty:
            info("  " + ln)
        return EXIT_GATE

    # Gate: refuse if a task is already active.
    existing = read_active_task(runs_dir)
    if existing:
        info(f"✗ 已有活跃任务：{existing}。请先 `finish` 再开新任务。")
        return EXIT_GATE

    task_id = f"{_now_stamp()}-{_slugify(description)}"
    if not _safe_task_id(task_id):
        info(f"error: 生成的 task_id 非法: {task_id!r}")
        return EXIT_USAGE
    task_dir = runs_dir / task_id
    _assert_within(task_dir, runs_dir)
    common.ensure_dir(round_dir(task_dir, 1))

    state = new_state(task_id, description, cfg)
    (task_dir / "task.md").write_text(render_task_md(state), encoding="utf-8")
    save_state(task_dir, runs_dir, state)
    write_active_task(runs_dir, task_id, task_dir)

    info(f"✓ 已创建任务：{task_id}")
    info(f"  目录：{task_dir}")
    info(f"  branch={state['branch']}  base_commit={state['base_commit'][:10]}  max_rounds={state['max_rounds']}")
    info(f"  下一步：{NEXT_HINT[S_STARTED]}")
    return EXIT_OK


def _resolve_and_gate(runs_dir):
    """Shared resolution + branch gate for round/collect/review/decide."""
    task_dir, state, err = require_active(runs_dir)
    if err:
        info("✗ " + err)
        return None, None, EXIT_GATE
    ok, cur = branch_gate(state)
    if not ok:
        info(f"✗ 分支不一致：当前 {cur!r} != STATE.branch {state.get('branch')!r}，停止。")
        return None, None, EXIT_GATE
    return task_dir, state, None


def cmd_round(args) -> int:
    cfg = common.load_config()
    runs_dir = resolve_runs_dir(cfg)
    task_dir, state, err = _resolve_and_gate(runs_dir)
    if err is not None:
        return err
    rd, created = do_round(task_dir, state, runs_dir, cfg)
    save_state(task_dir, runs_dir, state)
    info(f"✓ round {state['round_index']} 就绪：{rd}")
    info(f"  生成：{', '.join(created) if created else '（已存在，未覆盖）'}")
    info(f"  下一步：{NEXT_HINT[state['status']]}")
    return EXIT_OK


def cmd_collect(args) -> int:
    cfg = common.load_config()
    runs_dir = resolve_runs_dir(cfg)
    task_dir, state, err = _resolve_and_gate(runs_dir)
    if err is not None:
        return err
    out, cerr = do_collect(task_dir, state, runs_dir, cfg, run_tests=not args.no_tests)
    if cerr:
        info("✗ " + cerr)
        return EXIT_GATE
    save_state(task_dir, runs_dir, state)
    info(f"✓ 已收集（脱敏）：{out}")
    info(f"  下一步：{NEXT_HINT[state['status']]}")
    return EXIT_OK


def cmd_review(args) -> int:
    cfg = common.load_config()
    runs_dir = resolve_runs_dir(cfg)
    task_dir, state, err = _resolve_and_gate(runs_dir)
    if err is not None:
        return err
    backend, verdict, rerr = do_review(task_dir, state, runs_dir, cfg)
    if rerr:
        info("✗ " + str(rerr))
        return EXIT_GATE
    save_state(task_dir, runs_dir, state)
    info(f"✓ 审查完成：backend={backend}  verdict={verdict}")
    if backend == "mock" or verdict == "UNSURE":
        info("  ⚠️ mock 或 UNSURE：必须停下等人工判断，回环不会自动推进。")
    info(f"  下一步：{NEXT_HINT[state['status']]}")
    return EXIT_OK


def cmd_decide(args) -> int:
    cfg = common.load_config()
    runs_dir = resolve_runs_dir(cfg)
    task_dir, state, err = _resolve_and_gate(runs_dir)
    if err is not None:
        return err
    status, msg, advanced = do_decide(task_dir, state, runs_dir, cfg)
    save_state(task_dir, runs_dir, state)
    info(f"✓ 决策：{msg}")
    info(f"  状态：{status}")
    info(f"  下一步：{NEXT_HINT.get(status, '（见状态）')}")
    return EXIT_OK


def cmd_status(args) -> int:
    cfg = common.load_config()
    runs_dir = resolve_runs_dir(cfg)
    task_id = read_active_task(runs_dir)
    if not task_id:
        info("（无活跃任务）运行 `start \"<任务>\"` 开始。")
        return EXIT_OK
    task_dir, state, err = require_active(runs_dir)
    if err:
        info("✗ " + err)
        return EXIT_GATE
    info(f"active_task : {task_id}")
    info(f"目录        : {task_dir}")
    info(f"branch      : {state.get('branch')}")
    info(f"round       : {state.get('round_index')} / {state.get('max_rounds')}")
    info(f"status      : {state.get('status')}")
    info(f"last_verdict: {state.get('last_verdict')}  backend: {state.get('backend')}")
    info(f"safe_auto   : {state.get('safe_auto')}  auto_commit={state.get('auto_commit')} "
         f"auto_push={state.get('auto_push')} auto_deploy={state.get('auto_deploy')} "
         f"allow_delete={state.get('allow_delete')}")
    info(f"下一步      : {NEXT_HINT.get(state.get('status'), '（见状态）')}")
    return EXIT_OK


def cmd_finish(args) -> int:
    cfg = common.load_config()
    runs_dir = resolve_runs_dir(cfg)
    task_dir, state, err = require_active(runs_dir)
    if err:
        info("✗ " + err)
        return EXIT_GATE

    if state.get("status") not in SAFE_FINISH_STATES:
        info(f"✗ 当前状态 {state.get('status')} 不在安全收尾状态，拒绝 finish。")
        info(f"  安全状态：{', '.join(sorted(SAFE_FINISH_STATES))}")
        return EXIT_GATE

    (task_dir / "final_report.md").write_text(
        render_final_report(state, S_FINISHED), encoding="utf-8")
    state["status"] = S_FINISHED
    save_state(task_dir, runs_dir, state)
    removed = remove_active_task(runs_dir)

    info(f"✓ 任务收尾：{state['task_id']}")
    info(f"  final_report.md：{task_dir / 'final_report.md'}")
    info(f"  .active_task 锁：{'已移除' if removed else '原本不存在'}")
    info("  注意：未执行 commit / push / deploy。")
    return EXIT_OK


def cmd_dry_run(args) -> int:
    """End-to-end simulation: create task -> round -> collect -> review ->
    decide. No business-code change, no Claude call, no real tests, no
    .active_task lock (so a real task is never disturbed)."""
    cfg = common.load_config()
    runs_dir = resolve_runs_dir(cfg)
    common.ensure_dir(runs_dir)

    description = (args.description or "dry-run sample").strip()
    task_id = f"dryrun-{_now_stamp()}-{_slugify(description)}"
    task_dir = runs_dir / task_id
    _assert_within(task_dir, runs_dir)
    common.ensure_dir(round_dir(task_dir, 1))

    state = new_state(task_id, description, cfg)
    state["dry_run"] = True
    (task_dir / "task.md").write_text(render_task_md(state), encoding="utf-8")
    save_state(task_dir, runs_dir, state)
    info(f"[dry-run] 模拟任务：{task_id}")
    info(f"[dry-run] 目录：{task_dir}（不写 .active_task，不影响真实任务）")

    rd, created = do_round(task_dir, state, runs_dir, cfg)
    save_state(task_dir, runs_dir, state)
    info(f"[dry-run] round 1 就绪：生成 {', '.join(created)}")

    out, cerr = do_collect(task_dir, state, runs_dir, cfg, run_tests=False, dry_run=True)
    if cerr:
        info("[dry-run] ✗ collect 失败：" + cerr)
        return EXIT_GATE
    save_state(task_dir, runs_dir, state)
    info(f"[dry-run] context.md（脱敏）：{out}")

    backend, verdict, _ = do_review(task_dir, state, runs_dir, cfg)
    save_state(task_dir, runs_dir, state)
    info(f"[dry-run] 审查：backend={backend}  verdict={verdict}")

    status, msg, advanced = do_decide(task_dir, state, runs_dir, cfg)
    save_state(task_dir, runs_dir, state)
    info(f"[dry-run] 闸门：{msg}")
    info(f"[dry-run] 终态：{status}")
    info("[dry-run] ✓ 端到端链路验证完成（未改业务代码 / 未 commit / 未 push / 未 deploy）。")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# argparse wiring
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="loop_controller.py",
        description="CC local loop controller (Phase D, safe_auto semi-auto).",
    )
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("start", help="创建任务并初始化 round 1")
    sp.add_argument("description", help="任务描述")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("round", help="准备/确认当前轮的 plan/implement 提示")
    sp.set_defaults(func=cmd_round)

    sp = sub.add_parser("collect", help="收集当前轮的脱敏 context.md")
    sp.add_argument("--no-tests", action="store_true", help="跳过真实测试采集")
    sp.set_defaults(func=cmd_collect)

    sp = sub.add_parser("review", help="调用 local_review.py 并解析 verdict")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("decide", help="应用 verdict 闸门")
    sp.set_defaults(func=cmd_decide)

    sp = sub.add_parser("status", help="显示当前任务与下一步建议")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("finish", help="安全状态下收尾并移除 .active_task")
    sp.set_defaults(func=cmd_finish)

    sp = sub.add_parser("dry-run", help="端到端模拟（不改业务代码/不调用 Claude）")
    sp.add_argument("description", nargs="?", default="dry-run sample", help="模拟任务描述")
    sp.set_defaults(func=cmd_dry_run)

    return p


def main(argv) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.func(args)
    except RuntimeError as exc:  # safety-boundary violations etc.
        info("✗ " + str(exc))
        return EXIT_GATE


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
