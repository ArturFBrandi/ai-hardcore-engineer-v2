"""Migration job: Alembic schema + LangGraph checkpointer tables (idempotent). Run by the ``migrate`` service."""

from __future__ import annotations

import asyncio

from alembic.config import Config

from alembic import command
from app.config import get_settings
from app.container import make_checkpointer


async def setup_checkpointer() -> None:
    saver, pool = await make_checkpointer(get_settings())
    try:
        await saver.setup()
    finally:
        await pool.close()


def main() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    asyncio.run(setup_checkpointer())
    print("migrations applied; checkpointer tables ready")


if __name__ == "__main__":
    main()
