#!/usr/bin/env python3
"""cc_local_loop local monitoring Dashboard — Phase H-UI-B (READ-ONLY).

A tiny standard-library HTTP server that exposes a read-only view of the
cc_local_loop run state (loop_controller / local_review / hooks artefacts under
``runs/`` + read-only git status). It NEVER mutates anything.

Hard guarantees:

  * Binds 127.0.0.1 only (host restricted to 127.0.0.1 / localhost).
  * GET only — every other method returns 405. No write / exec / delete /
    commit / push / deploy endpoint exists.
  * Host header must be 127.0.0.1:<port> / localhost:<port> (DNS-rebind guard).
  * Reads only the curated ``runs/`` artefacts + ``dashboard/static`` + read-only
    git (status/diff). Every path is resolved and confined; ``..`` / separators
    are rejected. Secret files (.env / keys / cookies) are never reachable.
  * Every JSON body passes through ``common.redact`` and every field is length
    capped, so no credential and no giant log can leak / freeze the page.

Standard library only — no third-party dependency.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

VERSION = "h-c0-1.0"

DASHBOARD_DIR = Path(__file__).resolve().parent
TOOL_DIR = DASHBOARD_DIR.parent
STATIC_DIR = DASHBOARD_DIR / "static"
RUNS_DIR = TOOL_DIR / "runs"

# Reuse the project's redaction / safe subprocess; fall back to a local redactor
# so the dashboard still redacts even if common.py cannot be imported.
sys.path.insert(0, str(TOOL_DIR))
try:
    import common  # type: ignore

    def redact(text):
        return common.redact(text)

    def run_command(cmd, cwd=None):
        return common.run_command(cmd, cwd=cwd)
except Exception:  # pragma: no cover - defensive only
    import subprocess

    _FB_KV = re.compile(
        r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*"
        r"(?:api[_-]?key|access[_-]?key|secret[_-]?key|api[_-]?secret|client[_-]?secret"
        r"|access[_-]?token|auth[_-]?token|refresh[_-]?token"
        r"|key|token|secret|password|passwd|passphrase|authorization|credentials?))"
        r"(\s*[=:]\s*)(\S+)"
    )
    _FB_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
    _FB_TOK = re.compile(
        r"\b(?:sk-[A-Za-z0-9]{6,}|gh[pousr]_[A-Za-z0-9]{6,}|xox[baprs]-[A-Za-z0-9-]{6,}"
        r"|AKIA[A-Z0-9]{16}|hf_[A-Za-z0-9]{16,})\b"
    )

    def redact(text):
        if text is None:
            return ""
        s = str(text)
        s = _FB_BEARER.sub("bearer ***REDACTED***", s)
        s = _FB_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", s)
        s = _FB_TOK.sub("***REDACTED***", s)
        return s

    def run_command(cmd, cwd=None):
        try:
            p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=20, shell=False)
            return {"returncode": p.returncode, "stdout": redact(p.stdout), "stderr": redact(p.stderr)}
        except Exception as exc:  # noqa: BLE001
            return {"returncode": None, "stdout": "", "stderr": redact(str(exc))}


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #

MAX_FILE_BYTES = 64 * 1024
MAX_LOG_LINES = 2000
MAX_LOG_BYTES = 200 * 1024
MAX_DIFF_BYTES = 200 * 1024
MAX_RUNS = 200
MAX_STATUS_ENTRIES = 400
MAX_LIST_ITEMS = 30
MAX_LINE = 800
MAX_SUMMARY = 800

PIPELINE = ["start", "round", "implement", "collect", "review", "decide", "finish"]
STATUS_STEP = {
    "STARTED": 0,
    "ROUND_READY": 1,
    "ROUND_PENDING": 1,
    "COLLECTED": 3,
    "REVIEWED": 4,
    "WAITING_HUMAN_ACCEPTANCE": 5,
    "WAITING_HUMAN_DECISION": 5,
    "MAX_ROUNDS_REACHED": 5,
    "FINISHED": 6,
}
ROUND_ARTIFACTS = ("plan_prompt.md", "implement_prompt.md", "context.md",
                   "review.md", "next_cc_prompt.md")
LOG_WHICH = {"hook", "test", "claude_stdout", "claude_stderr", "driver"}
# Curated whitelist of fields surfaced from runs/<task>/DRIVER.json (written by
# the future Full Auto Driver). Only these keys are ever read out; everything
# else in the file is ignored, and the whole payload still passes through redact.
DRIVER_FIELDS = ("full_auto_enabled", "driver_started_at", "driver_deadline",
                 "auto_round_count", "current_claude_pid", "last_claude_exit_code",
                 "stop_reason", "auto_commit", "max_total_seconds",
                 "per_round_seconds", "model", "updated_at")
STATIC_FILES = {"index.html", "app.js", "style.css"}
STATIC_CTYPE = {".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8"}

# Set after CLI parse; used for Host-header validation.
SERVER_PORT = 8765
ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost"}


# --------------------------------------------------------------------------- #
# Path safety & readers
# --------------------------------------------------------------------------- #

def _within(child: Path, parent: Path) -> bool:
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


def _runs_subdir(task_id):
    """Resolve ``runs/<task_id>`` only if it is a real, in-bounds run dir."""
    if not task_id or "/" in task_id or "\\" in task_id or ".." in task_id:
        return None
    d = RUNS_DIR / task_id
    if not _within(d, RUNS_DIR) or not d.is_dir():
        return None
    return d


def _round_dir(task_dir, round_str):
    try:
        n = int(round_str)
    except (TypeError, ValueError):
        return None
    if n < 1 or n > 99:
        return None
    return task_dir / f"round_{n:02d}"


def _read_head(path, limit=MAX_FILE_BYTES):
    try:
        data = path.read_bytes()
    except OSError:
        return None
    truncated = len(data) > limit
    text = data[:limit].decode("utf-8", "replace")
    if truncated:
        text += "\n…[truncated]"
    return text


def _read_tail(path, max_lines=MAX_LOG_LINES, max_bytes=MAX_LOG_BYTES):
    try:
        data = path.read_bytes()
    except OSError:
        return None
    prefix = ""
    if len(data) > max_bytes:
        data = data[-max_bytes:]
        prefix = "…[truncated head]\n"
    text = prefix + data.decode("utf-8", "replace")
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = [f"…[truncated to last {max_lines} lines]"] + lines[-max_lines:]
    return "\n".join(lines)


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _cap(s, n=MAX_LINE):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _now():
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Data layer (all read-only)
# --------------------------------------------------------------------------- #

def read_active_task():
    p = RUNS_DIR / ".active_task"
    try:
        raw = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            tid = data.get("task_id")
            return tid if isinstance(tid, str) and tid else None
    except (json.JSONDecodeError, ValueError):
        pass
    return raw


def state_summary(state):
    if not isinstance(state, dict):
        return {}
    keys = ["task_id", "task_description", "created_at", "updated_at", "base_commit",
            "branch", "round_index", "max_rounds", "status", "last_verdict", "backend",
            "safe_auto", "auto_commit", "auto_push", "auto_deploy", "allow_delete",
            "loop_mode"]
    out = {k: state.get(k) for k in keys}
    out["task_description"] = _cap(out.get("task_description"), 300)
    hist = state.get("history") or []
    out["history"] = [
        {"round": h.get("round"), "verdict": h.get("verdict"),
         "backend": h.get("backend"), "at": h.get("at")}
        for h in hist[-MAX_LIST_ITEMS:] if isinstance(h, dict)
    ]
    return out


def derive_stage(status):
    step = STATUS_STEP.get(status)
    return {"pipeline": PIPELINE, "step": step, "label": status}


def list_runs():
    runs = []
    if not RUNS_DIR.is_dir():
        return runs
    active = read_active_task()
    for d in RUNS_DIR.iterdir():
        if not d.is_dir():
            continue
        st = _load_json(d / "STATE.json")
        if not st:
            continue  # only dirs with a STATE.json count as runs
        try:
            mtime = (d / "STATE.json").stat().st_mtime
        except OSError:
            mtime = 0
        runs.append({
            "task_id": st.get("task_id", d.name),
            "kind": "dryrun" if d.name.startswith("dryrun-") else "real",
            "status": st.get("status"),
            "round_index": st.get("round_index"),
            "created_at": st.get("created_at"),
            "updated_at": st.get("updated_at"),
            "is_active": st.get("task_id") == active,
            "_mtime": mtime,
        })
    runs.sort(key=lambda r: r.get("_mtime", 0), reverse=True)
    runs = runs[:MAX_RUNS]
    for r in runs:
        r.pop("_mtime", None)
    return runs


def round_artifacts(task_dir, n):
    rd = task_dir / f"round_{int(n):02d}"
    present = {}
    for name in ROUND_ARTIFACTS:
        present[name] = (rd / name).exists()
    return present


def driver_summary(task_dir):
    """Read ``runs/<task>/DRIVER.json`` (curated, capped, read-only).

    Returns ``{"exists": False}`` when the task dir or DRIVER.json is missing or
    unreadable — the dashboard degrades gracefully before the Full Auto Driver
    ever writes the file. The path is confined to ``RUNS_DIR`` (the same guard as
    every other reader); the filename is a fixed literal, so no user input ever
    reaches the path. Values flow out through the normal ``redact`` JSON pass.
    """
    if task_dir is None:
        return {"exists": False}
    p = task_dir / "DRIVER.json"
    if not _within(p, RUNS_DIR) or not p.is_file():
        return {"exists": False}
    data = _load_json(p)
    if not isinstance(data, dict):
        return {"exists": False}
    out = {"exists": True}
    for k in DRIVER_FIELDS:
        v = data.get(k)
        if isinstance(v, str):
            v = _cap(v, MAX_LINE)
        elif isinstance(v, (list, dict)):
            v = _cap(json.dumps(v, ensure_ascii=False), MAX_LINE)
        # bool / int / float / None pass through as-is.
        out[k] = v
    return out


def parse_review(text):
    out = {"backend": None, "model": None, "verdict": None,
           "evidence": [], "risks": [], "next_prompt_summary": ""}
    if not text:
        return out
    m = re.search(r"(?im)^backend=(\S+)\s*$", text)
    if m:
        out["backend"] = m.group(1).strip()
    mm = re.search(r"model=([^)\s;，)]+)", text)
    if mm:
        out["model"] = mm.group(1).strip()
    out["verdict"] = _parse_verdict(text)
    out["evidence"] = _bullets(text, "Evidence")
    out["risks"] = _bullets(text, "Risks")
    nxt = _section_body(text, "Recommended Next CC Prompt")
    out["next_prompt_summary"] = _cap(" ".join(nxt.split()), MAX_SUMMARY)
    return out


def _parse_verdict(text):
    lines = text.splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip().lower() == "## verdict"), -1)
    if idx >= 0:
        for l in lines[idx + 1:]:
            t = l.strip()
            if not t:
                continue
            if t in ("PASS", "NEEDS_FIX", "UNSURE"):
                return t
            if t.startswith("#"):
                break
    m = re.search(r"(?m)^\s*(PASS|NEEDS_FIX|UNSURE)\s*$", text)
    return m.group(1) if m else None


def _section_body(text, header):
    lines = text.splitlines()
    target = f"## {header}"
    out, capturing = [], False
    for l in lines:
        if l.startswith("## "):
            if capturing:
                break
            if l.strip() == target:
                capturing = True
                continue
        if capturing:
            out.append(l)
    return "\n".join(out).strip()


def _bullets(text, header):
    body = _section_body(text, header)
    items = [l.strip()[2:].strip() for l in body.splitlines() if l.strip().startswith("- ")]
    return [_cap(x) for x in items[:MAX_LIST_ITEMS]]


def git_status():
    res = run_command(["git", "status", "--porcelain"], cwd=TOOL_DIR)
    out = (res.get("stdout") or "")
    entries = []
    for ln in out.splitlines()[:MAX_STATUS_ENTRIES]:
        if not ln.strip():
            continue
        entries.append({"code": _cap(ln[:2], 4), "path": _cap(ln[3:], 300)})
    return entries


def git_diff_stat():
    res = run_command(["git", "diff", "--stat"], cwd=TOOL_DIR)
    out = (res.get("stdout") or "")
    if len(out) > MAX_DIFF_BYTES:
        out = out[:MAX_DIFF_BYTES] + "\n…[truncated]"
    return out


def git_head():
    res = run_command(["git", "rev-parse", "--short", "HEAD"], cwd=TOOL_DIR)
    return (res.get("stdout") or "").strip()[:40]


def log_content(task_dir, round_str, which):
    """Return ``(exists, content)`` for a curated, capped log source."""
    if which == "hook":
        p = task_dir / "stop_hook_snapshots.log"
        if not _within(p, RUNS_DIR) or not p.exists():
            return False, ""
        return True, _read_tail(p) or ""
    if which == "test":
        rd = _round_dir(task_dir, round_str)
        if rd is None:
            return False, ""
        ctx = rd / "context.md"
        if not _within(ctx, RUNS_DIR) or not ctx.exists():
            return False, ""
        body = _section_body(_read_head(ctx) or "", "测试输出")
        return True, _cap(body, MAX_LOG_BYTES)
    # claude_stdout / claude_stderr / driver -> per-round log files (future)
    rd = _round_dir(task_dir, round_str)
    if rd is None:
        return False, ""
    name = {"claude_stdout": "claude_stdout.log",
            "claude_stderr": "claude_stderr.log",
            "driver": "driver.log"}[which]
    p = rd / name
    if not _within(p, RUNS_DIR) or not p.exists():
        return False, ""
    return True, _read_tail(p) or ""


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = "ccll-dashboard/" + VERSION
    protocol_version = "HTTP/1.1"

    # --- helpers ---------------------------------------------------------- #
    def _host_ok(self):
        host = self.headers.get("Host")
        if not host:
            return False
        hostname = host.rsplit(":", 1)[0].strip("[]").lower()
        if hostname not in ALLOWED_HOSTNAMES:
            return False
        if ":" in host:
            port = host.rsplit(":", 1)[1]
            if port.isdigit() and int(port) != SERVER_PORT:
                return False
        return True

    def _send(self, body_bytes, ctype, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body_bytes)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _json(self, obj, status=200):
        body = redact(json.dumps(obj, ensure_ascii=False))
        self._send(body.encode("utf-8"), "application/json; charset=utf-8", status)

    def _err(self, status, msg):
        self._json({"error": msg, "status": status}, status)

    # --- methods ---------------------------------------------------------- #
    def do_GET(self):
        if not self._host_ok():
            return self._err(403, "forbidden host")
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            if path == "/" or path == "/static/index.html":
                return self._serve_static("index.html")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if path == "/api/health":
                return self._json({"ok": True, "server_time": _now(),
                                   "bind": f"127.0.0.1:{SERVER_PORT}", "version": VERSION})
            if path == "/api/status":
                return self._api_status()
            if path == "/api/runs":
                return self._json({"runs": list_runs(), "server_time": _now()})
            if path == "/api/current":
                return self._api_current()
            if path == "/api/driver":
                return self._api_driver(qs)
            if path == "/api/rounds":
                return self._api_rounds(qs)
            if path == "/api/review":
                return self._api_review(qs)
            if path == "/api/diff":
                return self._json({"head": git_head(), "git_status": git_status(),
                                   "diff_stat": git_diff_stat(), "server_time": _now()})
            if path == "/api/logs":
                return self._api_logs(qs)
            return self._err(404, "not found")
        except Exception as exc:  # noqa: BLE001 — never 500-crash the loop
            return self._err(500, _cap(redact(str(exc)), 200))

    def _serve_static(self, name):
        name = name.strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name not in STATIC_FILES:
            return self._err(404, "not found")
        p = STATIC_DIR / name
        if not _within(p, STATIC_DIR) or not p.is_file():
            return self._err(404, "not found")
        ctype = STATIC_CTYPE.get(p.suffix, "application/octet-stream")
        try:
            data = p.read_bytes()
        except OSError:
            return self._err(404, "not found")
        # Static UI assets are the dashboard's own files (no secrets) -> served
        # as-is; only API JSON is redacted.
        self._send(data, ctype)

    def _api_status(self):
        active = read_active_task()
        st = {}
        if active:
            d = _runs_subdir(active)
            if d:
                st = _load_json(d / "STATE.json") or {}
        self._json({
            "active": bool(active),
            "task_id": active,
            "status": st.get("status"),
            "stage": derive_stage(st.get("status")),
            "round_index": st.get("round_index"),
            "max_rounds": st.get("max_rounds"),
            "last_verdict": st.get("last_verdict"),
            "backend": st.get("backend"),
            "branch": st.get("branch"),
            "updated_at": st.get("updated_at"),
            "server_time": _now(),
        })

    def _api_current(self):
        active = read_active_task()
        if not active:
            return self._json({"active": False, "server_time": _now()})
        d = _runs_subdir(active)
        if not d:
            return self._json({"active": False, "server_time": _now()})
        st = _load_json(d / "STATE.json") or {}
        n = st.get("round_index") or 1
        self._json({
            "active": True,
            "state": state_summary(st),
            "stage": derive_stage(st.get("status")),
            "current_round": n,
            "round_artifacts": round_artifacts(d, n),
            "driver": driver_summary(d),
            "has_final_report": (d / "final_report.md").exists(),
            "has_task_card": (d / "task.md").exists(),
            "server_time": _now(),
        })

    def _api_driver(self, qs):
        """Read-only DRIVER.json view for ``?task=<id>`` (defaults to active)."""
        task = (qs.get("task") or [None])[0]
        if not task:
            task = read_active_task()
        if not task:
            return self._json({"active": False, "driver": {"exists": False},
                               "server_time": _now()})
        # Reject path traversal / separators outright (security boundary).
        if "/" in task or "\\" in task or ".." in task:
            return self._err(400, "invalid task")
        d = _runs_subdir(task)
        if not d:
            # Well-formed name but no such run dir yet -> graceful empty, no error.
            return self._json({"task_id": task, "driver": {"exists": False},
                               "server_time": _now()})
        self._json({"task_id": d.name, "driver": driver_summary(d),
                    "server_time": _now()})

    def _api_rounds(self, qs):
        task = (qs.get("task") or [None])[0]
        d = _runs_subdir(task)
        if not d:
            return self._err(400, "invalid task")
        st = _load_json(d / "STATE.json") or {}
        rounds = []
        for n in range(1, int(st.get("max_rounds") or 5) + 1):
            rd = d / f"round_{n:02d}"
            if not rd.is_dir():
                continue
            present = {name: (rd / name).exists() for name in ROUND_ARTIFACTS}
            rv = parse_review(_read_head(rd / "review.md") or "") if (rd / "review.md").exists() else {}
            rounds.append({"round": n, "artifacts": present,
                           "verdict": rv.get("verdict"), "backend": rv.get("backend")})
        self._json({"task_id": st.get("task_id", task), "rounds": rounds, "server_time": _now()})

    def _api_review(self, qs):
        task = (qs.get("task") or [None])[0]
        d = _runs_subdir(task)
        if not d:
            return self._err(400, "invalid task")
        st = _load_json(d / "STATE.json") or {}
        round_str = (qs.get("round") or [str(st.get("round_index") or 1)])[0]
        rd = _round_dir(d, round_str)
        if rd is None:
            return self._err(400, "invalid round")
        review_path = rd / "review.md"
        if not _within(review_path, RUNS_DIR) or not review_path.exists():
            return self._json({"task_id": st.get("task_id", task), "round": round_str,
                               "exists": False, "server_time": _now()})
        self._json({"task_id": st.get("task_id", task), "round": round_str, "exists": True,
                    "review": parse_review(_read_head(review_path) or ""), "server_time": _now()})

    def _api_logs(self, qs):
        task = (qs.get("task") or [None])[0]
        which = (qs.get("which") or [None])[0]
        round_str = (qs.get("round") or ["1"])[0]
        if which not in LOG_WHICH:
            return self._err(400, "invalid which")
        d = _runs_subdir(task)
        if not d:
            return self._err(400, "invalid task")
        exists, content = log_content(d, round_str, which)
        self._json({"task_id": d.name, "round": round_str, "which": which,
                    "exists": exists, "content": content, "server_time": _now()})

    # --- non-GET -> 405 --------------------------------------------------- #
    def _405(self):
        if not self._host_ok():
            return self._err(403, "forbidden host")
        self._json({"error": "method not allowed", "status": 405}, 405)

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _405

    def log_message(self, fmt, *args):  # quieter, redacted access log
        try:
            sys.stderr.write(redact(f"[dashboard] {self.address_string()} {fmt % args}\n"))
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main(argv):
    global SERVER_PORT
    ap = argparse.ArgumentParser(description="cc_local_loop read-only Dashboard")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind host (only 127.0.0.1 or localhost allowed)")
    ap.add_argument("--port", type=int, default=8765, help="bind port (default 8765)")
    args = ap.parse_args(argv[1:])

    if args.host not in ("127.0.0.1", "localhost"):
        print("error: --host must be 127.0.0.1 or localhost (loopback only)")
        return 2
    SERVER_PORT = args.port

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(redact(f"error: cannot bind {args.host}:{args.port}: {exc}"))
        return 1

    print(f"cc_local_loop Dashboard (read-only) on http://{args.host}:{args.port}")
    print("Ctrl+C to stop. This server is loopback-only; do not expose it.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
