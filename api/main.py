"""ASGI app exposing the read-only research API for the React frontend.

Run:  uvicorn api.main:app --reload --port 8000
Check: http://localhost:8000/health   and   http://localhost:8000/api/research?ticker=AAPL

Prefers FastAPI when installed; otherwise falls back to Starlette (already present in this
environment) so the skeleton runs WITHOUT adding a dependency. Both backends expose the
same routes and delegate to the framework-agnostic logic in api.adapters. All responses
are HTTP 200 with a structured body; the frontend reads the `ok` flag (errors never throw).
"""
from __future__ import annotations

from api import adapters

_ALLOW_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

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
