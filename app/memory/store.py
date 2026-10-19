"""Durable, customer-scoped conversation memory (PostgreSQL).

* ``conversations`` / ``conversation_turns`` are the conversational source of truth; content is always
  redacted before insert.
* Every method takes the customer id from the verified principal and filters by it; a foreign or
  missing conversation is the same ``NotFoundError`` (no existence oracle).
* ``conversation_lock`` serializes concurrent turns on one conversation across replicas with a
  PostgreSQL advisory lock held for the duration of the graph run.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.base import Database
from app.db.models import Conversation, ConversationStatus, ConversationTurn, TurnRole
from app.errors import ConflictError, NotFoundError
from app.guardrails.redaction import redact_text
from app.observability import metrics


def new_conversation_id() -> str:
    return f"conv_{secrets.token_hex(12)}"


class MemoryStore:
    def __init__(self, db: Database, ttl_days: int, lock_timeout_seconds: float) -> None:
        self.db = db
        self.ttl_days = ttl_days
        self.lock_timeout = lock_timeout_seconds

    # ------------------------------------------------------------------ ownership & lifecycle
    async def get_owned(self, customer_id: str, conversation_id: str) -> Conversation:
        async with self.db.session() as s:
            conv = await s.scalar(
                select(Conversation).where(Conversation.id == conversation_id, Conversation.customer_id == customer_id)
            )
        if conv is None or conv.expires_at < datetime.now(UTC):
            raise NotFoundError("conversation not found")
        return conv

    async def create(self, customer_id: str, language: str | None) -> Conversation:
        now = datetime.now(UTC)
        conv = Conversation(
            id=new_conversation_id(),
            customer_id=customer_id,
            status=ConversationStatus.ACTIVE,
            language=language,
            slots_json={},
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=self.ttl_days),
        )
        async with self.db.transaction() as s:
            s.add(conv)
        return conv

    @asynccontextmanager
    async def conversation_lock(self, conversation_id: str) -> AsyncIterator[None]:
        """Session-level advisory lock; waits up to ``lock_timeout`` then raises ``ConflictError`` (409)."""
        async with self.db.engine.connect() as conn:
            acquired = await self._acquire(conn, conversation_id)
            if not acquired:
                raise ConflictError("another turn is still being processed for this conversation; retry shortly")
            try:
                yield
            finally:
                await conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": conversation_id})
                await conn.commit()

    async def _acquire(self, conn: AsyncConnection, key: str) -> bool:
        deadline = asyncio.get_running_loop().time() + self.lock_timeout
        while True:
            got = await conn.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": key})
            await conn.commit()
            if got:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.2)

    # ------------------------------------------------------------------------------- reads
    async def load(self, customer_id: str, conversation_id: str, window: int) -> dict[str, Any]:
        conv = await self.get_owned(customer_id, conversation_id)
        async with self.db.session() as s:
            rows = (
                await s.scalars(
                    select(ConversationTurn)
                    .where(ConversationTurn.conversation_id == conv.id)
                    .order_by(ConversationTurn.id.desc())
                    .limit(window * 2)
                )
            ).all()
            older_count = await s.scalar(
                select(func.count()).select_from(ConversationTurn).where(ConversationTurn.conversation_id == conv.id)
            )
        metrics.MEMORY_OPS.labels(op="load").inc()
        history = [
            {"turn": r.turn, "role": r.role.value, "content": r.content, "intent": r.intent, "case_id": r.case_id}
            for r in reversed(rows)
        ]
        return {
            "conversation": conv,
            "history": history,
            "summary": conv.summary_json,
            "slots": dict(conv.slots_json or {}),
            "total_turn_rows": int(older_count or 0),
        }

    async def turns_for_summary(self, conversation_id: str, upto_turn: int) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            rows = (
                await s.scalars(
                    select(ConversationTurn)
                    .where(ConversationTurn.conversation_id == conversation_id, ConversationTurn.turn <= upto_turn)
                    .order_by(ConversationTurn.id)
                )
            ).all()
        return [{"turn": r.turn, "role": r.role.value, "content": r.content} for r in rows]

    async def list_conversations(self, customer_id: str, limit: int, offset: int) -> list[Conversation]:
        async with self.db.session() as s:
            rows = await s.scalars(
                select(Conversation)
                .where(Conversation.customer_id == customer_id, Conversation.expires_at > datetime.now(UTC))
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(rows)

    async def turns(self, customer_id: str, conversation_id: str) -> tuple[Conversation, list[ConversationTurn]]:
        conv = await self.get_owned(customer_id, conversation_id)
        async with self.db.session() as s:
            rows = await s.scalars(
                select(ConversationTurn)
                .where(ConversationTurn.conversation_id == conv.id)
                .order_by(ConversationTurn.id)
            )
            return conv, list(rows)

    # ------------------------------------------------------------------------------ writes
    async def append_turn(
        self,
        conversation_id: str,
        *,
        turn: int,
        role: TurnRole,
        content: str,
        intent: str | None = None,
        agents: list[str] | None = None,
        sources: list[dict[str, Any]] | None = None,
        status: str | None = None,
        case_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        async with self.db.transaction() as s:
            s.add(
                ConversationTurn(
                    conversation_id=conversation_id,
                    turn=turn,
                    role=role,
                    content=redact_text(content),
                    intent=intent,
                    agents=agents or [],
                    sources_json=sources or [],
                    status=status,
                    case_id=case_id,
                    request_id=request_id,
                )
            )
            await s.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(days=self.ttl_days))
            )
        metrics.MEMORY_OPS.labels(op="append").inc()

    async def update_conversation(self, conversation_id: str, **values: Any) -> None:
        values["updated_at"] = datetime.now(UTC)
        async with self.db.transaction() as s:
            await s.execute(update(Conversation).where(Conversation.id == conversation_id).values(**values))
        metrics.MEMORY_OPS.labels(op="save").inc()

    async def erase(self, customer_id: str, conversation_id: str) -> None:
        conv = await self.get_owned(customer_id, conversation_id)
        async with self.db.transaction() as s:
            await s.execute(delete(Conversation).where(Conversation.id == conv.id))  # turns cascade
        metrics.MEMORY_OPS.labels(op="erase").inc()

    async def expired(self, now: datetime | None = None, limit: int = 500) -> list[tuple[str, str]]:
        async with self.db.session() as s:
            rows = await s.execute(
                select(Conversation.id, Conversation.customer_id)
                .where(Conversation.expires_at < (now or datetime.now(UTC)))
                .limit(limit)
            )
            return [(r[0], r[1]) for r in rows]

    async def delete_ids(self, ids: list[str]) -> int:
        if not ids:
            return 0
        async with self.db.transaction() as s:
            res = await s.execute(delete(Conversation).where(Conversation.id.in_(ids)))
        return int(getattr(res, "rowcount", 0) or 0)
