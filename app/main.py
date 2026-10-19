"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth import TokenVerifier
from app.api.errors import install_error_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.protection import IdempotencyStore, RateLimiter
from app.api.routes_chat import router as chat_router
from app.api.routes_conversations import router as conversations_router
from app.api.routes_health import router as health_router
from app.api.routes_operator import router as operator_router
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.hitl.service import HitlService
from app.observability.logging import configure_logging, get_logger
from app.observability.tracing import instrument_app, setup_tracing

log = get_logger(__name__)
STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    setup_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("startup", settings=settings.safe_dict())
        c = container or await build_container(settings)
        app.state.container = c
        app.state.verifier = TokenVerifier(settings)
        app.state.rate_limiter = RateLimiter(c.redis, settings)
        app.state.idempotency = IdempotencyStore(c.redis, settings)
        app.state.hitl = HitlService(c.db, c.chat)
        app.state.models_warm = False

        async def warm() -> None:
            try:
                await asyncio.to_thread(c.models.warmup)
                app.state.models_warm = True
                log.info("models_warm")
            except Exception as exc:
                log.error("models_warmup_failed", error=type(exc).__name__, detail=str(exc)[:200])

        warm_task = asyncio.create_task(warm())
        yield
        warm_task.cancel()
        if container is None:
            await c.close()
        log.info("shutdown")

    app = FastAPI(
        title="Getnet Multi-Agent Support API",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware, max_body_bytes=settings.max_request_body_bytes)
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
            allow_credentials=False,
        )
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(conversations_router)
    app.include_router(operator_router)
    if not settings.is_production:
        app.mount("/ui", StaticFiles(directory=STATIC / "ui", html=True), name="ui")
        app.mount("/operator", StaticFiles(directory=STATIC / "operator", html=True), name="operator")

        @app.get("/", include_in_schema=False)
        async def root() -> Any:
            return RedirectResponse("/ui/")

    instrument_app(app)
    return app


def app_factory() -> FastAPI:
    """uvicorn entrypoint: ``uvicorn app.main:app_factory --factory``."""
    return create_app()
