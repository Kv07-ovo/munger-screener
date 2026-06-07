#!/usr/bin/env python3
"""PreToolUse safety guard for the cc_local_loop workflow.

Reads a Claude Code *PreToolUse* hook event on stdin, inspects the tool name and
its input, and DENIES dangerous commands or edits to protected paths by emitting
a ``permissionDecision: deny`` JSON object (exit 0). Safe calls pass through
silently (exit 0, no stdout) so the normal permission flow is unaffected.

Security properties:

  * The CORE deny rules are HARD-CODED in this file (``BUILTIN_*``). config.json
    can only ADD rules, never remove a built-in — a tampered config can never
    re-enable ``rm -rf`` / ``git push`` / editing ``.env``.
  * Every reason string is passed through ``common.redact``; the hook never
    echoes a secret value.
  * On internal error the hook fails CLOSED by default (deny). This is
    configurable via config.json ``"hook_fail_mode": "fail_open" | "fail_closed"``.

This hook never runs git, never writes files, never touches the network.

Note: this is a defense-in-depth net, NOT a hard sandbox. If the interpreter is
missing the harness cannot run the hook at all (it then fails open at the harness
level). The loop_controller (Phase D) remains the primary control by never
issuing dangerous commands in the first place.
"""

from __future__ import annotations

import json
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

# --------------------------------------------------------------------------- #
# Import shared helpers (redact / load_config) from the parent tool dir.
# Fall back to a minimal local redactor if common.py cannot be imported, so the
# guard still functions (and still redacts) under any circumstance.
# --------------------------------------------------------------------------- #

HOOK_DIR = Path(__file__).resolve().parent
TOOL_DIR = HOOK_DIR.parent
sys.path.insert(0, str(TOOL_DIR))

try:
    import common  # type: ignore

    def redact(text):
        return common.redact(text)

    def load_config():
        return common.load_config()
except Exception:  # pragma: no cover - defensive only
    _FALLBACK_KV = re.compile(
        r"(?i)\b(api[-_]?key|token|secret|password|authorization)(\s*[=:]\s*)\S+"
    )
    _FALLBACK_TOK = re.compile(
        r"\b(sk-[A-Za-z0-9]{6,}|gh[pousr]_[A-Za-z0-9]{6,}|AKIA[A-Z0-9]{16}|hf_[A-Za-z0-9]{20,})\b"
    )

    def redact(text):
        if text is None:
            return ""
        s = str(text)
        s = _FALLBACK_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", s)
        s = _FALLBACK_TOK.sub("***REDACTED***", s)
        return s

    def load_config():
        try:
            return json.loads((TOOL_DIR / "config.json").read_text(encoding="utf-8"))
        except Exception:
            return {}


# --------------------------------------------------------------------------- #
# Built-in (non-removable) policy
# --------------------------------------------------------------------------- #

# Destructive / exfiltration commands. These are ALWAYS enforced regardless of
# config; config.command_denylist can only add more.
BUILTIN_COMMAND_DENYLIST = [
    # --- git destructive / publishing ---
    r"\bgit\s+push\b",
    r"\bgit\s+reset\b[^\n]*--hard\b",
    r"\bgit\s+clean\b[^\n]*-[a-zA-Z]*f",          # -f, -fd, -xdf, -xfd ...
    # --- recursive force delete (POSIX) ---
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f",              # rm -rf / -Rf / -rdf
    r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r",              # rm -fr
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+-[a-zA-Z]*f",  # rm -r ... -f
    r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*\s+-[a-zA-Z]*r",  # rm -f ... -r
    # --- recursive force delete (PowerShell / cmd) ---
    r"(?i)\bremove-item\b[^\n]*-recurse[^\n]*-force",
    r"(?i)\bremove-item\b[^\n]*-force[^\n]*-recurse",
    r"(?i)\bri\b[^\n]*-recurse[^\n]*-force",
    r"(?i)\bdel\b[^\n]*/s",
    r"(?i)\b(rmdir|rd)\b[^\n]*/s",
    # --- deploy verbs / common deploy tools ---
    r"(?i)(^|[;&|]\s*)deploy\b",
    r"(?i)\bvercel\b(\s|$)",
    r"(?i)\bnetlify\s+deploy\b",
    r"(?i)\brender\s+deploy\b",
    # --- secret read / print ---
    r"(?i)\bprintenv\b",
    r"(?i)\benv\s*\|",
    r"(?i)(get-childitem|gci|dir|ls)\s+env:",
    r"(?i)\b(cat|type|less|more|bat|nl|head|tail|strings|gc|get-content)\b[^\n]*\.env(\.|\b)",
    # generic "echo/print a *_KEY/_TOKEN/_SECRET env var"
    r"(?i)\b(echo|print|printf|write-output|write-host)\b[^\n]*\$\{?\w*(api[_-]?key|token|secret|password|cookie)\w*",
    r"(?i)\$env:\w*(key|token|secret|password|cookie)\w*",
    # any command carrying a literal provider-shaped secret (echo sk-..., etc.)
    r"\b(sk-[A-Za-z0-9]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|hf_[A-Za-z0-9]{20,})\b",
]

# Hard secrets — never editable.
BUILTIN_SECRET_PATHS = [
    ".env", "*.env", ".env.*",
    "id_rsa", "id_rsa.pub", "id_ed25519", "id_ed25519.pub",
    "id_dsa", "id_ecdsa", "known_hosts", "authorized_keys",
    "*.pem", "*.key", "*.ppk",
    "*cookie*", "*cookies*",
]

# Default-protected manifests — editable only after explicit user approval.
BUILTIN_MANIFEST_PATHS = [
    ".gitignore",
    "package.json", "package-lock.json",
    "requirements*.txt", "requirements.txt", "requirements-dev.txt",
    "pyproject.toml",
]

_MAX_REASON = 300


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #

def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": redact(reason)[:_MAX_REASON],
        }
    }))
    sys.exit(0)


def _allow() -> None:
    # Emit nothing: defer to the normal permission flow (we only ever DENY).
    sys.exit(0)


def _snippet(text: str, limit: int = 120) -> str:
    return redact(text).replace("\n", " ")[:limit]


# --------------------------------------------------------------------------- #
# Command-class checks (Bash / PowerShell)
# --------------------------------------------------------------------------- #

def _secret_env_patterns(cfg) -> list:
    names = cfg.get("secret_env_names") or []
    pats = []
    for name in names:
        if isinstance(name, str) and name:
            pats.append(re.compile(r"(?i)(\$\{?|\$env:|%)" + re.escape(name)))
    return pats


def check_command(command: str, cfg) -> "str | None":
    if not command:
        return None
    extra = [p for p in (cfg.get("command_denylist") or []) if isinstance(p, str)]
    for pat in BUILTIN_COMMAND_DENYLIST + extra:
        try:
            if re.search(pat, command):
                return f"命令被安全策略拦截：{_snippet(command)}"
        except re.error:
            continue
    for rx in _secret_env_patterns(cfg):
        if rx.search(command):
            return f"疑似读取/打印密钥的命令被拦截：{_snippet(command)}"
    return None


# --------------------------------------------------------------------------- #
# Path-class checks (Edit / Write / MultiEdit / NotebookEdit)
# --------------------------------------------------------------------------- #

def _basename(path: str) -> str:
    return Path(str(path).replace("\\", "/")).name


def check_path(path: str, cfg) -> "tuple[str, str] | None":
    """Return (tier, basename) if protected, else None."""
    if not path:
        return None
    base = _basename(path)
    for pat in BUILTIN_SECRET_PATHS:
        if fnmatch(base, pat):
            return ("secret", base)
    for pat in BUILTIN_MANIFEST_PATHS:
        if fnmatch(base, pat):
            return ("manifest", base)
    for pat in (cfg.get("protected_paths") or []):
        if isinstance(pat, str) and fnmatch(base, pat):
            # Re-classify configured entries that are clearly manifests/secrets.
            if pat in BUILTIN_SECRET_PATHS:
                return ("secret", base)
            if pat in BUILTIN_MANIFEST_PATHS:
                return ("manifest", base)
            return ("configured", base)
    return None


def _path_reason(hit) -> str:
    tier, base = hit
    if tier == "secret":
        return (f"受保护的机密文件，禁止修改：{base}"
                "（.env / 密钥 / SSH / Cookie 等绝不允许改，也不会被打印）")
    if tier == "manifest":
        return (f"默认受保护的清单文件：{base}。"
                "需获得用户单独批准后才能修改"
                "（根 .gitignore / package.json / requirements*.txt / pyproject.toml 等）")
    return f"受保护路径（config.protected_paths）：{base}，已默认拦截。"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> int:
    raw = sys.stdin.read()
    cfg = {}
    try:
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    fail_mode = str(cfg.get("hook_fail_mode", "fail_closed")).lower()

    try:
        event = json.loads(raw) if raw.strip() else {}
        tool = event.get("tool_name", "") or ""
        tool_input = event.get("tool_input") or {}

        if tool in ("Bash", "PowerShell"):
            reason = check_command(tool_input.get("command", "") or "", cfg)
            if reason:
                _deny(reason)

        elif tool in ("Edit", "Write", "MultiEdit"):
            hit = check_path(tool_input.get("file_path") or tool_input.get("path") or "", cfg)
            if hit:
                _deny(_path_reason(hit))

        elif tool == "NotebookEdit":
            hit = check_path(tool_input.get("notebook_path") or tool_input.get("file_path") or "", cfg)
            if hit:
                _deny(_path_reason(hit))

        # Any other tool: not our concern -> allow (defer to normal flow).
        _allow()

    except SystemExit:
        raise
    except Exception as exc:
        if fail_mode == "fail_open":
            sys.stderr.write("guard hook error (fail_open): " + redact(str(exc))[:200] + "\n")
            return 0
        # fail closed: deny the operation we could not evaluate.
        _deny("安全 hook 内部错误，按 fail_closed 拦截该操作。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
