"""Async SQLAlchemy engine/session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        pool_pre_ping=True,
        pool_timeout=10,
        connect_args={
            "timeout": 5,
            "command_timeout": settings.db_statement_timeout_ms / 1000,
            "server_settings": {"statement_timeout": str(settings.db_statement_timeout_ms)},
        },
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class Database:
    """Owns the engine and hands out sessions; one per process, created in the app lifespan."""

    def __init__(self, settings: Settings) -> None:
        self.engine = make_engine(settings)
        self.sessionmaker = make_sessionmaker(self.engine)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session, session.begin():
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
