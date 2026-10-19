"""`make purge-expired`: one-off retention purge (the worker also runs it hourly)."""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.container import make_checkpointer
from app.db.base import Database
from app.memory.store import MemoryStore
from app.worker.main import purge_expired


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    saver, pool = await make_checkpointer(settings)
    try:
        n = await purge_expired(MemoryStore(db, settings.conversation_ttl_days, 30), saver)
        print(f"purged {n} expired conversations (turns, summaries, checkpoints)")
    finally:
        await pool.close()
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
