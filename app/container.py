"""Dependency container — builds every collaborator once per process (API lifespan, worker, tests)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.db.base import Database
from app.llm.base import LLMProvider
from app.llm.factory import build_llm
from app.memory.store import MemoryStore
from app.observability.logging import get_logger
from app.observability.trace_store import TraceStore
from app.orchestration.decision import PenaltyConfig, load_penalties
from app.orchestration.graph import CompiledGraph, build_graph
from app.orchestration.runtime import GraphContext
from app.rag.models import RetrievalModels
from app.rag.qdrant_store import QdrantStore
from app.rag.retriever import Retriever
from app.services.chat import ChatService
from app.services.web_search import WebSearchProvider, build_web_search
from app.tools.base import ToolRegistry
from app.tools.context import AuthContext
from app.tools.registry import build_registry

log = get_logger(__name__)


class RedisPublisher:
    """Fan-out of conversation events across replicas (Redis pub/sub → SSE)."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def publish(self, conversation_id: str, event: dict[str, Any]) -> None:
        try:
            await self.redis.publish(f"conv:{conversation_id}", json.dumps(event, default=str))
        except RedisError:
            log.warning("sse_publish_failed", conversation_id=conversation_id)


async def make_checkpointer(settings: Settings) -> tuple[AsyncPostgresSaver, AsyncConnectionPool[Any]]:
    """psycopg v3 pool for LangGraph's AsyncPostgresSaver (ADR 007)."""
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        settings.psycopg_dsn,
        min_size=1,
        max_size=max(4, settings.db_pool_size),
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open(wait=True, timeout=15)
    return AsyncPostgresSaver(pool), pool


@dataclass
class Container:
    settings: Settings
    db: Database
    redis: Redis
    llm: LLMProvider
    tools: ToolRegistry
    retriever: Retriever
    store: QdrantStore
    models: RetrievalModels
    web: WebSearchProvider
    memory: MemoryStore
    penalties: PenaltyConfig
    graph: CompiledGraph
    checkpointer: AsyncPostgresSaver
    pool: AsyncConnectionPool[Any]
    traces: TraceStore
    publisher: RedisPublisher
    chat: ChatService = field(init=False)

    def __post_init__(self) -> None:
        self.chat = ChatService(self.settings, self.graph, self.memory, self.graph_context, self.traces)

    def graph_context(self, auth: AuthContext) -> GraphContext:
        return GraphContext(
            settings=self.settings,
            llm=self.llm,
            tools=self.tools,
            retriever=self.retriever,
            web=self.web,
            memory=self.memory,
            db=self.db,
            auth=auth,
            penalties=self.penalties,
            publisher=self.publisher,
        )

    async def close(self) -> None:
        await self.store.close()
        await self.pool.close()
        await self.redis.aclose()
        await self.db.dispose()


async def build_container(
    settings: Settings, *, llm: LLMProvider | None = None, web: WebSearchProvider | None = None
) -> Container:
    db = Database(settings)
    redis: Redis = Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
        decode_responses=True,
    )
    store = QdrantStore(settings.qdrant_url, settings.qdrant_collection, settings.qdrant_timeout_seconds)
    models = RetrievalModels(settings, offline=True)
    checkpointer, pool = await make_checkpointer(settings)
    return Container(
        settings=settings,
        db=db,
        redis=redis,
        llm=llm or build_llm(settings),
        tools=build_registry(db),
        retriever=Retriever(settings, models, store),
        store=store,
        models=models,
        web=web or build_web_search(settings),
        memory=MemoryStore(db, settings.conversation_ttl_days, settings.conversation_lock_timeout_seconds),
        penalties=load_penalties(settings.escalation_penalties_file),
        graph=build_graph(checkpointer),
        checkpointer=checkpointer,
        pool=pool,
        traces=TraceStore(redis, settings.trace_store_ttl_seconds, settings.enable_debug_endpoints),
        publisher=RedisPublisher(redis),
    )
