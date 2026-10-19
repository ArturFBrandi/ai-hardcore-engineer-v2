"""Prepare datastores for in-process test/evaluation runs (shared by tests/conftest.py and run_eval.py).

Creates the dedicated database, applies Alembic migrations + checkpointer setup, seeds the synthetic
backend and indexes the committed snapshot into the dedicated Qdrant collection (skipped when complete).
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse

import asyncpg

from app.config import Settings
from app.db.base import Database


async def ensure_database(settings: Settings, admin_db: str = "getnet") -> None:
    url = urlparse(settings.psycopg_dsn)
    name = url.path.lstrip("/")
    admin = url._replace(path=f"/{admin_db}").geturl()
    conn = await asyncpg.connect(admin, timeout=5)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


def migrate(settings: Settings) -> None:
    from alembic.config import Config

    from alembic import command
    from app.config import get_settings

    os.environ["DATABASE_URL"] = settings.database_url
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")


async def setup_checkpointer(settings: Settings) -> None:
    from app.container import make_checkpointer

    saver, pool = await make_checkpointer(settings)
    try:
        await saver.setup()
    finally:
        await pool.close()


async def seed(settings: Settings) -> None:
    from app.db.seed import seed as run_seed

    db = Database(settings)
    try:
        async with db.transaction() as s:
            await run_seed(s)
    finally:
        await db.dispose()


async def index_snapshot(settings: Settings, *, offline: bool = False) -> int:
    from app.rag.models import RetrievalModels
    from app.rag.qdrant_store import QdrantStore
    from ingestion.pipeline import index_chunks
    from ingestion.snapshot import load_snapshot

    chunks = load_snapshot()
    store = QdrantStore(settings.qdrant_url, settings.qdrant_collection)
    try:
        if await store.count() != len(chunks):
            await index_chunks(chunks, RetrievalModels(settings, offline=offline), store, recreate=True)
        return len(chunks)
    finally:
        await store.close()


async def prepare(settings: Settings) -> None:
    await ensure_database(settings)
    await asyncio.to_thread(migrate, settings)
    await setup_checkpointer(settings)
    await seed(settings)
    await index_snapshot(settings)
