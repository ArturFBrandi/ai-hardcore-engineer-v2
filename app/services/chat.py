"""Chat use case: one customer turn through the agent graph.

Ownership of the conversation is checked in code BEFORE the graph runs; the LangGraph thread id is built
by server code from the verified customer id; turns on the same conversation are serialized across
replicas by a PostgreSQL advisory lock.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from app.config import Settings
from app.errors import DependencyUnavailableError
from app.hitl import repository as hitl_repo
from app.memory.store import MemoryStore
from app.observability import metrics
from app.observability.cost import RequestUsage, current_usage
from app.observability.logging import get_logger
from app.observability.trace_store import TraceStore
from app.orchestration.graph import CompiledGraph, thread_id
from app.orchestration.runtime import GraphContext
from app.orchestration.state import reset_channels
from app.tools.context import AuthContext

log = get_logger(__name__)


@dataclass
class TurnResult:
    state: dict[str, Any]
    conversation_id: str
    turn: int
    usage: RequestUsage
    latency_ms: float


class ChatService:
    def __init__(
        self, settings: Settings, graph: CompiledGraph, memory: MemoryStore, context_factory: Any, traces: TraceStore
    ) -> None:
        self.settings = settings
        self.graph = graph
        self.memory = memory
        self.context_factory = context_factory  # (AuthContext) -> GraphContext
        self.traces = traces

    def _usage(self) -> RequestUsage:
        s = self.settings
        return RequestUsage(s.max_tokens_per_request, s.llm_price_input_per_mtok, s.llm_price_output_per_mtok)

    def _config(self, customer_id: str, conversation_id: str, checkpoint_id: str | None = None) -> RunnableConfig:
        configurable: dict[str, Any] = {"thread_id": thread_id(customer_id, conversation_id)}
        if checkpoint_id:
            configurable["checkpoint_id"] = checkpoint_id
        return RunnableConfig(configurable=configurable, recursion_limit=self.settings.graph_recursion_limit)

    async def run_turn(
        self, auth: AuthContext, message: str, conversation_id: str | None, request_id: str, trace_id: str
    ) -> TurnResult:
        started = time.perf_counter()
        if conversation_id:
            conv = await self.memory.get_owned(auth.customer_id, conversation_id)  # 404 for foreign/missing
        else:
            conv = await self.memory.create(auth.customer_id, None)
        usage = self._usage()
        token = current_usage.set(usage)
        try:
            async with self.memory.conversation_lock(conv.id):
                fresh = await self.memory.get_owned(auth.customer_id, conv.id)
                turn = int(fresh.turn_count or 0) + 1
                inputs: dict[str, Any] = {
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "customer_id": auth.customer_id,
                    "conversation_id": conv.id,
                    "turn": turn,
                    "message": message,
                    "status": "answered",
                    "case_id": None,
                    "blocked": False,
                    "needs_escalation": False,
                    "operator_decision": None,
                    "action_result": None,
                    "candidate_answer": "",
                    "final_answer": "",
                    "active_steps": [],
                    "supervisor_decision": {},
                    "priority": None,
                    "interrupt_kind": None,
                    "pending_action_id": None,
                    **reset_channels(),
                }
                ctx: GraphContext = self.context_factory(auth)
                try:
                    async with asyncio.timeout(self.settings.request_timeout_seconds):
                        state = await self.graph.ainvoke(inputs, self._config(auth.customer_id, conv.id), context=ctx)
                except TimeoutError as exc:
                    raise DependencyUnavailableError("the request took too long; please retry") from exc
                if state.get("__interrupt__") and state.get("case_id"):
                    snapshot = await self.graph.aget_state(self._config(auth.customer_id, conv.id))
                    checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
                    await hitl_repo.set_interrupt_checkpoint(ctx.db, state["case_id"], checkpoint_id, request_id)
        finally:
            current_usage.reset(token)
        latency = (time.perf_counter() - started) * 1000
        state = dict(state)
        state["token_usage"] = usage.as_dict()
        status = state.get("status", "answered")
        metrics.REQUESTS.labels(status=status, intent=state.get("intent", "unknown")).inc()
        metrics.REQUEST_DURATION.labels(status=status).observe(latency / 1000)
        metrics.REQUEST_COST.observe(usage.cost_usd)
        await self.traces.save(request_id, trace_id, state, usage.as_dict(), latency)
        log.info(
            "chat_turn",
            request_id=request_id,
            conversation_id=conv.id,
            turn=turn,
            intent=state.get("intent"),
            route_source=state.get("route_source"),
            status=status,
            confidence=state.get("confidence"),
            latency_ms=round(latency),
            llm_calls=usage.llm_calls,
            tokens=usage.total_tokens,
            cost_usd=usage.cost_usd,
        )
        return TurnResult(state=state, conversation_id=conv.id, turn=turn, usage=usage, latency_ms=latency)

    async def resume(
        self,
        owner_auth: AuthContext,
        conversation_id: str,
        checkpoint_id: str,
        decision: dict[str, Any],
        request_id: str,
        *,
        lock: bool = True,
    ) -> dict[str, Any]:
        """Resume the SAME thread from the interrupt checkpoint with the operator's decision."""
        usage = self._usage()
        token = current_usage.set(usage)
        started = time.perf_counter()
        try:
            ctx: GraphContext = self.context_factory(owner_auth)
            config = self._config(owner_auth.customer_id, conversation_id, checkpoint_id)
            if lock:
                async with self.memory.conversation_lock(conversation_id):
                    state = await self.graph.ainvoke(Command[Any](resume=decision), config, context=ctx)
            else:
                state = await self.graph.ainvoke(Command[Any](resume=decision), config, context=ctx)
        finally:
            current_usage.reset(token)
        state = dict(state)
        state["token_usage"] = usage.as_dict()
        await self.traces.save(
            request_id, state.get("trace_id", ""), state, usage.as_dict(), (time.perf_counter() - started) * 1000
        )
        return state
