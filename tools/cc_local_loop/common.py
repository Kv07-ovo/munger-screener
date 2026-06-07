"""Shared helpers for the CC local review loop (Phase 1).

Standard-library only by design: this tool must never add a third-party
dependency (no edits to requirements*.txt are allowed). Every helper is safe by
default —

  * subprocess calls never use a shell (no shell-injection surface);
  * HTTP/TCP probes never log credentials;
  * any string leaving this module toward stdout or a file should first pass
    through ``redact``.

The five helpers requested for Phase 1 live here:

  1. ``run_command``  - safe subprocess runner
  2. ``load_config``  - read config.json (with defaults)
  3. ``probe_tcp`` / ``probe_http`` - detect a local HTTP service / open port
  4. ``redact``       - mask sensitive tokens / API keys
  5. ``ensure_dir``   - create a directory path
"""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# --------------------------------------------------------------------------- #
# Paths & config
# --------------------------------------------------------------------------- #

TOOL_DIR = Path(__file__).resolve().parent
CONFIG_PATH = TOOL_DIR / "config.json"

# Mirrors config.json. Used as a fallback so the loop can always run (and
# degrade to mock) even if config.json is missing or corrupt.
DEFAULT_CONFIG = {
    "backend_default": "auto_mock",
    "ollama_url": "http://localhost:11434",
    "lmstudio_url": "http://localhost:1234/v1/chat/completions",
    "max_rounds": 5,
    "test_default_command": r".venv\Scripts\python.exe -m pytest tests/ -q",
    "mock_verdict": "UNSURE",
}


def ensure_dir(path) -> Path:
    """Create ``path`` (and any missing parents); return it as a ``Path``."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_config(config_path=CONFIG_PATH) -> dict:
    """Load config.json, filling missing keys from ``DEFAULT_CONFIG``.

    Never raises on a missing or broken file — a corrupt config must not be able
    to stop the review loop, so we always return a usable dict.
    """
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            cfg.update({k: v for k, v in loaded.items() if v is not None})
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError):
        # Corrupt config falls back to defaults rather than crashing.
        pass
    return cfg


# --------------------------------------------------------------------------- #
# Secret redaction
# --------------------------------------------------------------------------- #

_REDACTED = "***REDACTED***"

# key=value / key: value style secrets (api_key, token, password, ...).
_KV_SECRET = re.compile(
    r"(?i)\b(api[-_]?key|access[-_]?token|auth[-_]?token|secret[-_]?key|secret|token|password|authorization)"
    r"(\s*[=:]\s*)\S+"
)
# "Authorization: Bearer xxx" style headers.
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
# Provider-shaped opaque tokens (OpenAI sk-, GitHub ghp_/gho_, Slack xox...,
# AWS AKIA..., HuggingFace hf_...).
_TOKENISH = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9]{6,}"
    r"|gh[pousr]_[A-Za-z0-9]{6,}"
    r"|xox[baprs]-[A-Za-z0-9-]{6,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|hf_[A-Za-z0-9]{20,}"
    r")\b"
)


def redact(text) -> str:
    """Mask anything that looks like an API key / token / password.

    Applied to every string before it is printed to stdout or written into a
    review file, so a stray credential in captured tool output never leaks.
    """
    if text is None:
        return ""
    s = str(text)
    s = _KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", s)
    s = _BEARER.sub(f"bearer {_REDACTED}", s)
    s = _TOKENISH.sub(_REDACTED, s)
    return s


# --------------------------------------------------------------------------- #
# Safe subprocess
# --------------------------------------------------------------------------- #

def run_command(command, cwd=None, timeout=900, env=None) -> dict:
    """Run a command WITHOUT a shell and return its (redacted) result.

    ``command`` may be a list (preferred) or a string. A string is split with
    ``shlex`` using POSIX rules off on Windows, so a path like
    ``.venv\\Scripts\\python.exe`` survives intact. ``shell=False`` always, so
    there is no shell-injection surface.

    Returns a dict: ``{returncode, stdout, stderr, timed_out}`` where stdout and
    stderr are already redacted.
    """
    if isinstance(command, str):
        argv = shlex.split(command, posix=(os.name != "nt"))
    else:
        argv = list(command)

    if not argv:
        return {"returncode": None, "stdout": "", "stderr": "empty command", "timed_out": False}

    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": redact(proc.stdout),
            "stderr": redact(proc.stderr),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": None,
            "stdout": redact(exc.stdout or ""),
            "stderr": redact(f"timeout after {timeout}s"),
            "timed_out": True,
        }
    except (FileNotFoundError, OSError) as exc:
        return {"returncode": None, "stdout": "", "stderr": redact(str(exc)), "timed_out": False}


# --------------------------------------------------------------------------- #
# Service probing
# --------------------------------------------------------------------------- #

def host_port_from_url(url, default_port=80) -> tuple[str, int]:
    """Extract ``(host, port)`` from a URL, applying scheme-aware defaults."""
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = default_port
    return host, port


def probe_tcp(host, port, timeout=0.5) -> bool:
    """Return ``True`` if a TCP connection to ``host:port`` succeeds quickly."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def probe_http(url, timeout=1.0) -> tuple[bool, "int | None"]:
    """GET ``url`` and return ``(ok, status)``.

    Only used to detect whether a local model server is listening; the response
    body is ignored and no request/response headers are logged (credentials are
    never emitted). A 4xx/5xx still counts as "something is listening".
    """
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (localhost only)
            return True, getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as exc:
        return True, exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return False, None
