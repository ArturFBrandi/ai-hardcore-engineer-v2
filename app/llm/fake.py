"""Deterministic test double for ``LLMProvider``.

Refused when ``APP_ENV=production`` (settings validation + provider factory). Used by unit, orchestration,
E2E and offline evaluation runs so `make test` needs no MiniMax credits.

Behaviour per call, in order:
1. a scripted response queued for the task (``script(task, ...)``) — used by tests to force exact outputs,
   malformed JSON, provider errors, etc.;
2. otherwise a deterministic heuristic handler for the task (``app.llm.fake_handlers``) which reads the
   ``<task_input>`` JSON block every agent prompt carries.

It never pretends to be MiniMax in reports: responses are tagged ``provider=fake`` in traces.
"""

from __future__ import annotations

import itertools
import json
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from app.errors import ProviderError
from app.llm.base import LLMResponse, Message
from app.observability.cost import current_usage, estimate_tokens

ScriptItem = LLMResponse | Exception | str | dict[str, Any] | Callable[[list[Message]], LLMResponse]
Handler = Callable[[list[Message], list[dict[str, Any]] | None], LLMResponse]

_ids = itertools.count(1)


def tool_call_response(*calls: tuple[str, dict[str, Any]], content: str = "") -> LLMResponse:
    from app.llm.base import ToolCall  # noqa: PLC0415 - keeps the public helper import-light

    return LLMResponse(
        content=content,
        tool_calls=[ToolCall(id=f"call_{next(_ids)}", name=n, arguments=json.dumps(a)) for n, a in calls],
        finish_reason="tool_calls",
    )


def task_input(messages: list[Message]) -> dict[str, Any]:
    """Parse the last ``<task_input>{...}</task_input>`` block from the conversation."""
    for msg in reversed(messages):
        content = msg.get("content") or ""
        if isinstance(content, str) and "<task_input>" in content:
            raw = content.split("<task_input>", 1)[1].split("</task_input>", 1)[0]
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


class FakeLLMProvider:
    name = "fake"
    model = "fake-deterministic"

    def __init__(self, handlers: dict[str, Handler] | None = None) -> None:
        if handlers is None:
            from app.llm.fake_handlers import DEFAULT_HANDLERS  # noqa: PLC0415 - circular (handlers use fake helpers)

            handlers = dict(DEFAULT_HANDLERS)
        self._handlers = handlers
        self._scripts: dict[str, deque[ScriptItem]] = defaultdict(deque)
        self.calls: list[dict[str, Any]] = []
        self.fail_all: Exception | None = None

    def script(self, task: str, *items: ScriptItem) -> None:
        self._scripts[task].extend(items)

    def reset(self) -> None:
        self._scripts.clear()
        self.calls.clear()
        self.fail_all = None

    def calls_for(self, task: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["task"] == task]

    async def chat(
        self,
        *,
        task: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> LLMResponse:
        self.calls.append({"task": task, "messages": messages, "tools": tools, "json_mode": json_mode})
        usage = current_usage.get()
        if usage is not None:
            try:
                usage.check(estimate_tokens(str(messages)) + max_tokens)
            except Exception as exc:
                raise ProviderError(str(exc)) from exc
        if self.fail_all is not None:
            raise self.fail_all
        resp = self._next(task, messages, tools)
        in_tok = estimate_tokens(str(messages))
        out_tok = estimate_tokens(resp.content) + 8 * len(resp.tool_calls)
        if usage is not None:
            usage.record(task, in_tok, out_tok)
        return LLMResponse(
            content=resp.content,
            tool_calls=resp.tool_calls,
            input_tokens=in_tok,
            output_tokens=out_tok,
            finish_reason=resp.finish_reason or "stop",
        )

    def _next(self, task: str, messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
        queue = self._scripts.get(task)
        if queue:
            item = queue.popleft()
            if isinstance(item, Exception):
                raise item
            if isinstance(item, LLMResponse):
                return item
            if isinstance(item, str):
                return LLMResponse(content=item)
            if isinstance(item, dict):
                return LLMResponse(content=json.dumps(item))
            return item(messages)
        handler = self._handlers.get(task)
        if handler is None:
            raise ProviderError(f"fake provider has no handler for task {task!r}")
        return handler(messages, tools)
