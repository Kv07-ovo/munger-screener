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
import os
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

# --------------------------------------------------------------------------- #
# Anti-recursion policy (Full Auto Driver guard)
# --------------------------------------------------------------------------- #
#
# The (future) Full Auto Driver runs Claude Code head-less and exports
# ``CC_AUTO_DRIVER=1`` into that subprocess. While that flag is set, this guard
# DENIES any attempt to launch the ``claude`` CLI again from inside a Bash /
# PowerShell command, so the automated run can never recursively spawn another
# Claude / nest an agent. Per the user's design choice, EVEN probes such as
# ``claude --version`` / ``Get-Command claude`` are denied inside the auto flow
# (maximum strictness: zero recursion, zero nested discovery).
#
# This trigger is BUILT-IN and non-removable, mirroring the command/path
# denylists: config.json may ADD more trigger env vars / patterns, never remove
# this one. When the flag is NOT set, ordinary human use of ``claude --version``
# / ``claude --help`` passes through untouched.
BUILTIN_AUTO_DRIVER_ENV = "CC_AUTO_DRIVER"
AUTO_DRIVER_ACTIVE_VALUE = "1"

# The ``claude`` CLI executable token: bare name, optionally path-prefixed,
# quoted, or carrying a Windows extension (claude.exe / claude.cmd / ...).
_CLAUDE_EXE = r"(?:[^\s'\"|&;()]*[\\/])?claude(?:\.(?:exe|cmd|bat|ps1|com))?"
# Right boundary: end-of-token (space / EOL / quote / separator / closing paren),
# so a substring such as ``claude_stdout`` (word char follows) is NOT the CLI,
# but ``$(claude)`` IS.
_CLAUDE_END = r"(?=$|\s|['\"|&;)])"

# "claude" appearing in COMMAND POSITION — invoked, not merely mentioned. This
# keeps prose / commit messages / log file names (``echo \"use claude\"``,
# ``git commit -m \"fix claude bug\"``, ``claude_stdout.log``) from matching,
# while still catching every real launch form, including via a runner, a command
# substitution ``$(...)`` / backtick, or a PowerShell eval (iex / &).
BUILTIN_AUTO_DRIVER_CLAUDE_PATTERNS = [
    # 1) start of command / right after a separator OR substitution opener
    #    (; | & ` ( newline), skipping leading FOO=bar / FOO="b c" env-assigns.
    r"(?im)(?:^|[\n;|&`(])\s*(?:[A-Za-z_]\w*=(?:\"[^\"]*\"|'[^']*'|\S*)\s+)*['\"]?"
    + _CLAUDE_EXE + _CLAUDE_END,
    # 2) right after a launcher / runner / eval keyword, anywhere later in the
    #    segment (npx, cmd /c, powershell, bash -c, env, sudo, start, iex /
    #    Invoke-Expression, Invoke-Command, get-command / where / which, ...).
    r"(?i)\b(?:npx|bunx|npm\s+exec|pnpm\s+dlx|pnpm\s+exec|yarn\s+dlx"
    r"|powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd(?:\.exe)?"
    r"|sh|bash|zsh|env|sudo|nohup|time|xargs|exec|command|start|start-process|saps|call"
    r"|invoke-expression|iex|invoke-command|icm|start-job|start-threadjob"
    r"|get-command|gcm|where(?:\.exe)?|which)\b[^\n;|&]*?[\s(]['\"]?"
    + _CLAUDE_EXE + _CLAUDE_END,
    # 3) opaque encoded PowerShell command (-e / -enc / -EncodedCommand) could
    #    hide a claude launch under base64 — denied outright in the auto flow.
    r"(?i)\b(?:powershell|pwsh)(?:\.exe)?\b[^\n;|&]*?\s-e(?:nc(?:odedcommand)?)?\s",
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


def _auto_driver_active(cfg) -> bool:
    """True when running inside the Full Auto Driver (recursion guard armed).

    The built-in ``CC_AUTO_DRIVER`` trigger is always honoured; config.json may
    list ADDITIONAL trigger env var names via ``auto_driver_trigger_envs`` but can
    never disable the built-in one.
    """
    names = [BUILTIN_AUTO_DRIVER_ENV]
    for name in (cfg.get("auto_driver_trigger_envs") or []):
        if isinstance(name, str) and name:
            names.append(name)
    return any(os.environ.get(n) == AUTO_DRIVER_ACTIVE_VALUE for n in names)


def check_auto_driver_recursion(command: str, cfg) -> "str | None":
    """Deny any ``claude`` launch while the auto-driver flag is set.

    Returns a (redactable, command-free) reason string on a hit, else ``None``.
    Outside the auto flow this is a no-op, so manual ``claude --version`` /
    ``claude --help`` are never affected.
    """
    if not command or not _auto_driver_active(cfg):
        return None
    extra = [p for p in (cfg.get("auto_driver_claude_patterns") or []) if isinstance(p, str)]
    for pat in BUILTIN_AUTO_DRIVER_CLAUDE_PATTERNS + extra:
        try:
            if re.search(pat, command):
                return ("自动驾驶（CC_AUTO_DRIVER=1）下禁止再次调用 claude，"
                        "已拦截以避免递归启动 Claude / 嵌套 agent。")
        except re.error:
            continue
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
    # Read as UTF-8: Claude Code sends hook events as UTF-8, but Windows stdin
    # defaults to the ANSI code page (GBK), which raises UnicodeDecodeError on
    # non-ASCII payloads. Decode bytes explicitly with errors="replace".
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
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
            cmd = tool_input.get("command", "") or ""
            # Anti-recursion FIRST: in the auto flow, a claude launch is denied
            # with a specific reason before the generic denylist runs.
            reason = check_auto_driver_recursion(cmd, cfg)
            if reason:
                _deny(reason)
            reason = check_command(cmd, cfg)
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
