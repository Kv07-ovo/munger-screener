"""ASGI app exposing the read-only research API for the React frontend.

Run:  uvicorn api.main:app --reload --port 8000
Check: http://localhost:8000/health   and   http://localhost:8000/api/research?ticker=AAPL

Prefers FastAPI when installed; otherwise falls back to Starlette (already present in this
environment) so the skeleton runs WITHOUT adding a dependency. Both backends expose the
same routes and delegate to the framework-agnostic logic in api.adapters. All responses
are HTTP 200 with a structured body; the frontend reads the `ok` flag (errors never throw).
"""
from __future__ import annotations

import os

from api import adapters

# CORS：本地默认放行 Vite 5173；线上经环境变量 ALLOWED_ORIGINS（逗号分隔）配置；绝不放开到 "*"。
_LOCAL_DEFAULT_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def _parse_allowed_origins(raw: str | None = None) -> list[str]:
    """解析允许的前端来源（CORS allow_origins）。
      - 逗号分隔；去首尾空格、去空项、去重（保序）；
      - 安全：丢弃通配 "*"（绝不把 CORS 放开到任意来源）；
      - 为空（未设 / 空串 / 只有空格逗号 / 仅 "*"）→ 回退本地默认（localhost+127.0.0.1:5173）。
    例：ALLOWED_ORIGINS="https://your-frontend.vercel.app, https://your-custom-domain.com"
    """
    if raw is None:
        raw = os.getenv("ALLOWED_ORIGINS", "")
    seen, out = set(), []
    for part in str(raw).split(","):
        o = part.strip()
        if not o or o == "*":
            continue
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out or list(_LOCAL_DEFAULT_ORIGINS)


_ALLOW_ORIGINS = _parse_allowed_origins()

try:  # ---- preferred: FastAPI (install with: .venv/bin/pip install fastapi) ----
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    BACKEND = "fastapi"
    app = FastAPI(title="Kv Stock Cat API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=_ALLOW_ORIGINS,
        allow_methods=["GET"], allow_headers=["*"],
    )

    @app.get("/health")
    def health():  # noqa: D401
        return adapters.health_payload()

    @app.get("/api/research")
    def research(ticker: str = ""):
        return JSONResponse(adapters.build_research_payload(ticker))

except ImportError:  # ---- fallback: Starlette (already installed) ----
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    BACKEND = "starlette"

    async def health(request):
        return JSONResponse(adapters.health_payload())

    async def research(request):
        ticker = request.query_params.get("ticker", "")
        return JSONResponse(adapters.build_research_payload(ticker))

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/api/research", research, methods=["GET"]),
        ],
        middleware=[Middleware(
            CORSMiddleware, allow_origins=_ALLOW_ORIGINS,
            allow_methods=["GET"], allow_headers=["*"],
        )],
    )
