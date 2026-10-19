"""Customer conversation endpoints (owner-scoped; foreign ids → the same generic 404)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.api.auth import Principal, require_scopes
from app.api.schemas import ConversationListOut, ConversationOut, TurnOut
from app.db.models import Conversation
from app.orchestration.graph import thread_id

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


def _conv_out(c: Conversation, turns: list[TurnOut] | None = None) -> ConversationOut:
    return ConversationOut(
        conversation_id=c.id,
        status=c.status.value,
        language=c.language,
        created_at=c.created_at,
        updated_at=c.updated_at,
        open_case_id=c.open_case_id,
        turns=turns or [],
    )


@router.get("", response_model=ConversationListOut)
async def list_conversations(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    principal: Principal = require_scopes("conversations:read", allow_demo=True),
) -> ConversationListOut:
    rows = await request.app.state.container.memory.list_conversations(principal.customer_id, limit + 1, offset)
    items = [_conv_out(c) for c in rows[:limit]]
    return ConversationListOut(items=items, next_offset=offset + limit if len(rows) > limit else None)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: str, request: Request, principal: Principal = require_scopes("conversations:read", allow_demo=True)
) -> ConversationOut:
    conv, turns = await request.app.state.container.memory.turns(principal.customer_id, conversation_id)
    return _conv_out(
        conv,
        [
            TurnOut(
                turn=t.turn,
                role=t.role.value,
                content=t.content,
                intent=t.intent,
                agents=list(t.agents or []),
                case_id=t.case_id,
                sources=list(t.sources_json or []),
                created_at=t.created_at,
            )
            for t in turns
        ],
    )


@router.delete("/{conversation_id}", status_code=200)
async def erase_conversation(
    conversation_id: str,
    request: Request,
    principal: Principal = require_scopes("conversations:delete", allow_demo=True),
) -> dict[str, Any]:
    """LGPD erasure: turns, summary, slots and the graph checkpoints of the thread."""
    container = request.app.state.container
    await container.memory.erase(principal.customer_id, conversation_id)
    await container.checkpointer.adelete_thread(thread_id(principal.customer_id, conversation_id))
    return {"conversation_id": conversation_id, "erased": True}


@router.get("/{conversation_id}/events")
async def conversation_events(
    conversation_id: str, request: Request, principal: Principal = require_scopes("conversations:read", allow_demo=True)
) -> EventSourceResponse:
    """SSE stream (agent replies, human replies, case status) fanned out through Redis pub/sub."""
    container = request.app.state.container
    await container.memory.get_owned(principal.customer_id, conversation_id)
    pubsub = container.redis.pubsub()
    await pubsub.subscribe(f"conv:{conversation_id}")

    async def stream() -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"event": "ready", "data": json.dumps({"conversation_id": conversation_id})}
            while not await request.is_disconnected():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if msg is None:
                    yield {"event": "ping", "data": "{}"}
                    continue
                data = json.loads(msg["data"])
                yield {"event": data.get("type", "message"), "data": json.dumps(data, ensure_ascii=False)}
                await asyncio.sleep(0)
        finally:
            await pubsub.unsubscribe(f"conv:{conversation_id}")
            await pubsub.aclose()

    return EventSourceResponse(stream(), ping=None)
