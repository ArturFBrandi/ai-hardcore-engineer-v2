"""Graph runtime context and the shared node instrumentation helper.

Dependencies (LLM, tools, retriever, stores, the immutable ``AuthContext``) are passed through LangGraph's
runtime *context* — never through the checkpointed state — so identity is supplied by server code on
every run/resume and can't be altered by checkpoint contents.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Protocol

from langgraph.errors import GraphBubbleUp
from langgraph.runtime import Runtime
from opentelemetry import trace

from app.config import Settings
from app.db.base import Database
from app.llm.base import LLMProvider
from app.memory.store import MemoryStore
from app.observability import metrics
from app.observability.logging import get_logger
from app.orchestration.decision import PenaltyConfig
from app.orchestration.state import AgentEvent, AgentState
from app.rag.retriever import Retriever
from app.services.web_search import WebSearchProvider
from app.tools.base import ToolRegistry
from app.tools.context import AuthContext

log = get_logger(__name__)
tracer = trace.get_tracer("getnet.agents")


class EventPublisher(Protocol):
    async def publish(self, conversation_id: str, event: dict[str, Any]) -> None: ...


class NullPublisher:
    async def publish(self, conversation_id: str, event: dict[str, Any]) -> None:
        return None


@dataclass
class GraphContext:
    settings: Settings
    llm: LLMProvider
    tools: ToolRegistry
    retriever: Retriever | None
    web: WebSearchProvider
    memory: MemoryStore
    db: Database
    auth: AuthContext
    penalties: PenaltyConfig
    publisher: EventPublisher = field(default_factory=NullPublisher)


class NodeFn(Protocol):
    def __call__(self, state: AgentState, *, runtime: Runtime[GraphContext]) -> Awaitable[dict[str, Any]]: ...


def event(agent: str, name: str, detail: dict[str, Any], started: float | None = None) -> AgentEvent:
    """``started`` is a wall-clock epoch (``time.time()``) so start/end timestamps of parallel branches are
    directly comparable (monotonic-vs-wall drift would distort concurrency evidence)."""
    now = time.time()
    ev = AgentEvent(agent=agent, event=name, detail=detail, ended_at=datetime.fromtimestamp(now, UTC).isoformat())
    begin = started if started is not None else now
    ev["started_at"] = datetime.fromtimestamp(begin, UTC).isoformat()
    if started is not None:
        ev["latency_ms"] = round((now - started) * 1000, 1)
    return ev


def node(name: str) -> Callable[[NodeFn], NodeFn]:
    """Wrap a graph node: OTel span, latency metric, entered/exited events, safe failure handling.

    Interrupts (``GraphBubbleUp``) propagate. Any other exception is converted into an ``errors`` entry and
    a ``node_failed`` event so the graph degrades gracefully instead of failing the request.
    """

    def deco(fn: NodeFn) -> NodeFn:
        @wraps(fn)
        async def wrapper(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
            started = time.time()
            ctx = runtime.context
            if state.get("customer_id") and state["customer_id"] != ctx.auth.customer_id:
                # Defense in depth: checkpointed state and the server-built AuthContext must agree.
                raise PermissionError("state/auth customer mismatch")
            with tracer.start_as_current_span(f"agent.{name}") as span:
                span.set_attribute("agent.name", name)
                span.set_attribute("request.id", state.get("request_id", ""))
                try:
                    update = await fn(state, runtime=runtime)
                except GraphBubbleUp:
                    raise
                except PermissionError:
                    raise
                except Exception as exc:
                    log.exception("node_failed", node=name, error_type=type(exc).__name__)
                    span.record_exception(exc)
                    update = {
                        "errors": [f"{name}:{type(exc).__name__}"],
                        "events": [event(name, "node_failed", {"error": type(exc).__name__}, started)],
                        # A failed specialist step is still *done*: the supervisor must not reschedule it forever.
                        "completed_steps": [s["id"] for s in state.get("active_steps") or [] if s.get("agent") == name],
                    }
                elapsed = time.time() - started
                metrics.AGENT_DURATION.labels(node=name).observe(elapsed)
                update.setdefault("events", [])
                update["events"] = [*update["events"], event(name, "exited", {}, started)]
                return update

        return wrapper

    return deco
