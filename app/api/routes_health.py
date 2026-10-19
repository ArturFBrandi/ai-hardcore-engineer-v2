"""Health, readiness, Prometheus metrics, dev-only debug traces / token minting, admin KB status."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.auth import Principal, require_scopes
from app.api.dev_idp import mint
from app.api.schemas import DevTokenIn
from app.errors import AuthorizationError, NotFoundError

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


async def _check(name: str, coro: Any, limit_s: float = 3.0) -> tuple[str, dict[str, Any]]:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(limit_s):
            detail = await coro
        return name, {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1), **(detail or {})}
    except Exception as exc:
        return name, {"ok": False, "error": type(exc).__name__}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    """PostgreSQL, Qdrant (collection populated), Redis and local models. Deliberately NOT MiniMax:
    an LLM outage degrades answers (deterministic fallbacks) but must not take replicas out of rotation."""
    c = request.app.state.container

    async def pg() -> dict[str, Any]:
        async with c.db.session() as s:
            await s.execute(text("SELECT 1"))
        return {}

    async def qdrant() -> dict[str, Any]:
        return {"chunks": await c.store.count()}

    async def redis() -> dict[str, Any]:
        await c.redis.ping()
        return {}

    async def models() -> dict[str, Any]:
        return {"warm": bool(getattr(request.app.state, "models_warm", False))}

    results = dict(
        await asyncio.gather(
            _check("postgres", pg()), _check("qdrant", qdrant()), _check("redis", redis()), _check("models", models())
        )
    )
    ok = (
        all(r["ok"] for r in results.values())
        and results["qdrant"].get("chunks", 0) > 0
        and results["models"].get("warm")
    )
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": results}, status_code=200 if ok else 503)


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/v1/debug/traces/{request_id}")
async def debug_trace(
    request_id: str, request: Request, principal: Principal = require_scopes("cases:read")
) -> dict[str, Any]:
    """Sanitized step trace (development only; force-disabled in production)."""
    if not request.app.state.settings.enable_debug_endpoints:
        raise NotFoundError("not found")
    doc: dict[str, Any] | None = await request.app.state.container.traces.get(request_id)
    if doc is None:
        raise NotFoundError("trace not found")
    return doc


@router.post("/v1/dev/token")
async def dev_token(body: DevTokenIn, request: Request) -> dict[str, Any]:
    """Local development identity provider for the demo UI/console. Unavailable in production."""
    settings = request.app.state.settings
    if settings.is_production or not settings.enable_debug_endpoints:
        raise NotFoundError("not found")
    allowed = {"chat", "conversations:read", "conversations:delete", "cases:read", "cases:write", "approvals:write"}
    scopes = body.scope.split()
    if set(scopes) - allowed:
        raise AuthorizationError("scope not available from the dev identity provider")
    token = mint(body.sub, " ".join(scopes), settings=settings, ttl_seconds=3600)
    return {"access_token": token, "token_type": "Bearer", "expires_in": 3600}


@router.get("/v1/admin/kb")
async def kb_status(request: Request, principal: Principal = require_scopes("ingest:run")) -> dict[str, Any]:
    c = request.app.state.container
    report, snapshot = await asyncio.to_thread(_kb_files)
    return {
        "collection": c.settings.qdrant_collection,
        "chunks": await c.store.count(),
        "embedding_model": c.settings.embedding_model,
        "reranker_model": c.settings.reranker_model,
        "last_ingest": report,
        "snapshot": snapshot,
    }


def _kb_files() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    report_path, manifest = Path("data/ingest-report.json"), Path("data/snapshots/manifest.json")
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    snap = json.loads(manifest.read_text()) if manifest.exists() else None
    return report, ({k: v for k, v in snap.items() if k != "source_urls"} if snap else None)
