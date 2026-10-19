"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import install_error_handlers
from app.api.routes_health import router as health_router
from app.config import Settings, get_settings
from app.observability.logging import configure_logging, get_logger

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("startup", settings=settings.safe_dict())
        yield
        log.info("shutdown")

    app = FastAPI(
        title="Getnet Multi-Agent Support API",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
    )
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(health_router)
    return app
