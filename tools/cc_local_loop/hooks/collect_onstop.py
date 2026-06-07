#!/usr/bin/env python3
"""Stop hook: lightweight snapshot preparation for the cc_local_loop workflow.

Fires when Claude Code finishes a response. Phase C scope is a SAFE SKELETON
only:

  * It does NOT run a full context collect (that is Phase D's collect logic).
  * It does NOT continue the loop, NEVER emits ``decision: block``, and never
    calls any model — so it cannot create a self-driving loop.
  * It only ever appends a single redacted, timestamped marker line to a log
    UNDER ``tools/cc_local_loop/runs/``.

Guard rails:

  1. If ``runs/.active_task`` does not exist -> exit 0 immediately (no-op).
  2. If the event's ``stop_hook_active`` is true -> exit 0 (anti-recursion).
  3. If config ``stop_hook_enabled`` is false -> exit 0.
  4. All file writes are confined to ``runs/`` (path is re-checked before write).
  5. Never writes business files, never commits/pushes/deploys, never blocks.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
TOOL_DIR = HOOK_DIR.parent
RUNS_DIR = TOOL_DIR / "runs"
ACTIVE_TASK = RUNS_DIR / ".active_task"

sys.path.insert(0, str(TOOL_DIR))
try:
    import common  # type: ignore

    def redact(text):
        return common.redact(text)

    def load_config():
        return common.load_config()
except Exception:  # pragma: no cover - only when common.py cannot be imported
    # Local fallback so a snapshot is STILL redacted even if common.py cannot be
    # imported. This must NEVER be a no-op: a credential must never reach the
    # snapshot log unmasked. Mirrors guard_pretooluse.py's fallback (stdlib only).
    _FB_KV = re.compile(
        r"(?i)\b(api[-_]?key|access[-_]?token|auth[-_]?token|secret[-_]?key|secret|token|password|authorization)"
        r"(\s*[=:]\s*)\S+"
    )
    _FB_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
    _FB_TOK = re.compile(
        r"\b(?:"
        r"sk-[A-Za-z0-9]{6,}"          # OpenAI-style
        r"|gh[pousr]_[A-Za-z0-9]{6,}"  # GitHub ghp_/gho_/...
        r"|xox[a-zA-Z]?-?[A-Za-z0-9-]{6,}"  # Slack xox...
        r"|AKIA[A-Z0-9]{16}"           # AWS access key id
        r"|hf_[A-Za-z0-9]{6,}"         # HuggingFace
        r")\b"
    )

    def redact(text):
        if text is None:
            return ""
        s = str(text)
        # Bearer first so the token after "Bearer" is masked before the KV rule
        # consumes only the word "Bearer".
        s = _FB_BEARER.sub("bearer ***REDACTED***", s)
        s = _FB_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", s)
        s = _FB_TOK.sub("***REDACTED***", s)
        return s

    def load_config():
        try:
            return json.loads((TOOL_DIR / "config.json").read_text(encoding="utf-8"))
        except Exception:
            return {}


def _within(child: Path, parent: Path) -> bool:
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except Exception:
        return False


def _resolve_run_dir() -> Path:
    """Pick the snapshot target dir from .active_task, always confined to runs/."""
    target = RUNS_DIR
    try:
        data = json.loads(ACTIVE_TASK.read_text(encoding="utf-8"))
        rd = data.get("run_dir") or data.get("task_dir")
        if isinstance(rd, str) and rd:
            cand = Path(rd) if Path(rd).is_absolute() else (TOOL_DIR / rd)
            if _within(cand, RUNS_DIR):
                target = cand
    except Exception:
        # .active_task may be plain text or malformed; fall back to runs/.
        pass
    return target


def main() -> int:
    # Read as UTF-8 (see guard_pretooluse.py): Windows stdin defaults to GBK and
    # would raise on UTF-8 non-ASCII hook payloads.
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        event = {}

    # (2) Anti-recursion: never act when we are already inside a stop-hook turn.
    if event.get("stop_hook_active") is True:
        return 0

    # (3) Respect the kill switch.
    try:
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    if cfg.get("stop_hook_enabled") is False:
        return 0

    # (1) No active task -> no-op.
    if not ACTIVE_TASK.exists():
        return 0

    run_dir = _resolve_run_dir()
    snapshot = run_dir / "stop_hook_snapshots.log"

    # (4) Re-check confinement right before writing.
    if not _within(snapshot, RUNS_DIR):
        return 0
    try:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        session = redact(str(event.get("session_id", "")))[:32]
        line = f"[{int(time.time())}] stop hook fired; session={session}; ready_for_collect=1\n"
        with open(snapshot, "a", encoding="utf-8") as fh:
            fh.write(redact(line))
    except Exception:
        # Snapshotting is best-effort; never fail the stop.
        return 0

    # (5) Always allow the stop to proceed. No stdout, no decision:block.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
