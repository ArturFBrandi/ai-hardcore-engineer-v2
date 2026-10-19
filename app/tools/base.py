"""Typed tool framework: Tool, ToolResult, ToolRegistry.

Architecture: ``LLM agent → typed tool → repository → database``. The registry is the single choke point:

* checks the least-privilege matrix (agent may hold the tool) and the action policy table;
* validates LLM-produced JSON arguments against the tool's Pydantic input model;
* runs approval-gated tools **only** through ``execute_approved`` (called by the HITL executor after an
  operator approved the persisted pending action) — an LLM call yields an ``approval_required`` result;
* enforces a timeout, records metrics/trace events and converts failures into explicit ``ToolResult``s.
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.db.base import Database
from app.db.models import ActionPolicy
from app.errors import NotFoundError, ToolError
from app.observability import metrics
from app.observability.logging import get_logger
from app.orchestration.policy import agent_may_use, policy_for
from app.tools.context import AuthContext

log = get_logger(__name__)

ToolStatus = Literal["ok", "not_found", "invalid_arguments", "error", "approval_required", "forbidden"]


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    status: ToolStatus
    data: dict[str, Any] | None = None
    error: str | None = None
    retryable: bool = False
    latency_ms: float = 0.0
    arguments: dict[str, Any] = {}

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Tool[InputT: ToolInput](ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[ToolInput]]
    timeout_seconds: ClassVar[float] = 5.0

    def __init__(self, db: Database) -> None:
        self.db = db

    @property
    def policy(self) -> ActionPolicy:
        return policy_for(self.name)

    def openai_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }

    @abstractmethod
    async def run(self, ctx: AuthContext, args: InputT) -> dict[str, Any]:
        """Execute against the customer-scoped repository. Raise NotFoundError/ToolError on failure."""


class ToolRegistry:
    def __init__(self, tools: list[Tool[Any]]) -> None:
        self._tools = {t.name: t for t in tools}

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas_for(self, agent: str) -> list[dict[str, Any]]:
        return [t.openai_schema() for n, t in sorted(self._tools.items()) if agent_may_use(agent, n)]

    async def invoke(
        self, agent: str, name: str, raw_args: str | dict[str, Any] | None, ctx: AuthContext
    ) -> ToolResult:
        """Invocation path for agents (LLM-proposed calls)."""
        args_dict = _parse_args(raw_args)
        tool = self._tools.get(name)
        if tool is None or not agent_may_use(agent, name):
            metrics.TOOL_FAILURES.labels(tool=name if tool else "unknown", reason="forbidden").inc()
            log.warning("tool_forbidden", agent=agent, tool=name)
            return ToolResult(tool=name, status="forbidden", error=f"tool {name!r} is not available to {agent}")
        policy = tool.policy
        if policy is ActionPolicy.FORBIDDEN:
            return ToolResult(tool=name, status="forbidden", error="action forbidden by policy")
        try:
            parsed = tool.input_model.model_validate(args_dict or {})
        except ValidationError as exc:
            metrics.TOOL_FAILURES.labels(tool=name, reason="invalid_arguments").inc()
            return ToolResult(
                tool=name, status="invalid_arguments", error=_short_validation(exc), arguments=args_dict or {}
            )
        if policy is ActionPolicy.REQUIRES_APPROVAL:
            # Never executed here: the proposal is returned for the HITL approval gate.
            metrics.TOOL_CALLS.labels(tool=name, status="approval_required").inc()
            return ToolResult(
                tool=name,
                status="approval_required",
                data={"proposed": parsed.model_dump(mode="json")},
                arguments=parsed.model_dump(mode="json"),
            )
        return await self._execute(tool, parsed, ctx)

    async def execute_approved(self, name: str, args: dict[str, Any], ctx: AuthContext) -> ToolResult:
        """Execution path for an approval-gated action that a human operator approved.

        ``ctx`` must be the case owner's context (``AuthContext.for_case_owner``)."""
        tool = self._tools.get(name)
        if tool is None or tool.policy is not ActionPolicy.REQUIRES_APPROVAL:
            return ToolResult(tool=name, status="forbidden", error="not an approval-gated action")
        if not ctx.has_scope("approved_action"):
            return ToolResult(tool=name, status="forbidden", error="missing approval")
        try:
            parsed = tool.input_model.model_validate(args)
        except ValidationError as exc:
            return ToolResult(tool=name, status="invalid_arguments", error=_short_validation(exc), arguments=args)
        return await self._execute(tool, parsed, ctx)

    async def execute_system(self, name: str, args: dict[str, Any], ctx: AuthContext) -> ToolResult:
        """Execution path for deterministic system code (escalation's ``create_support_case``)."""
        tool = self._tools.get(name)
        if tool is None or tool.policy is not ActionPolicy.AUTO_ALLOWED:
            return ToolResult(tool=name, status="forbidden", error="not allowed")
        try:
            parsed = tool.input_model.model_validate(args)
        except ValidationError as exc:
            return ToolResult(tool=name, status="invalid_arguments", error=_short_validation(exc), arguments=args)
        return await self._execute(tool, parsed, ctx)

    async def _execute(self, tool: Tool[Any], args: ToolInput, ctx: AuthContext) -> ToolResult:
        started = time.perf_counter()
        arguments = args.model_dump(mode="json")
        try:
            async with asyncio.timeout(tool.timeout_seconds):
                data = await tool.run(ctx, args)
            status: ToolStatus = "ok"
            error, retryable = None, False
        except NotFoundError as exc:
            data, status, error, retryable = None, "not_found", exc.detail, False
        except ToolError as exc:
            data, status, error, retryable = None, "error", exc.detail, exc.retryable
        except TimeoutError:
            data, status, error, retryable = None, "error", "tool timed out", True
        except (OSError, ConnectionError) as exc:  # datastore connectivity
            data, status, error, retryable = None, "error", f"backend unavailable: {type(exc).__name__}", True
        except Exception as exc:
            log.exception("tool_unexpected_error", tool=tool.name)
            data, status, error, retryable = None, "error", f"unexpected {type(exc).__name__}", True
        latency = (time.perf_counter() - started) * 1000
        metrics.TOOL_CALLS.labels(tool=tool.name, status=status).inc()
        if status != "ok":
            metrics.TOOL_FAILURES.labels(tool=tool.name, reason=status).inc()
        log.info("tool_call", tool=tool.name, status=status, latency_ms=round(latency, 1))
        return ToolResult(
            tool=tool.name,
            status=status,
            data=data,
            error=error,
            retryable=retryable,
            latency_ms=round(latency, 1),
            arguments=arguments,
        )


def _parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"__invalid_json__": raw[:200]}
    return value if isinstance(value, dict) else {"__invalid_json__": str(value)[:200]}


def _short_validation(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())[:300]
