"""Provider-agnostic LLM interface and the structured-output helper.

The rest of the application depends only on ``LLMProvider`` (OpenAI-style messages + tools). MiniMax
specifics live in ``app.llm.minimax``; the deterministic test double lives in ``app.llm.fake``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from app.errors import ProviderError
from app.observability import metrics
from app.observability.logging import get_logger

log = get_logger(__name__)

Message = dict[str, Any]

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as produced by the model


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    latency_ms: float = 0.0

    def assistant_message(self) -> Message:
        """The full assistant message to append to history before tool results (MiniMax requirement)."""
        msg: Message = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in self.tool_calls
            ]
        return msg


class LLMProvider(Protocol):
    name: str
    model: str

    async def chat(
        self,
        *,
        task: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> LLMResponse: ...


class StructuredOutputError(ProviderError):
    code = "llm_structured_output_error"


def strip_reasoning(text: str) -> str:
    """Remove ``<think>`` blocks (MiniMax interleaved thinking) — reasoning is never exposed or stored."""
    return _THINK.sub("", text or "").strip()


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _FENCE.sub("", strip_reasoning(text)).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise json.JSONDecodeError("not an object", cleaned, 0)
    return value


async def structured[T: BaseModel](
    provider: LLMProvider,
    *,
    task: str,
    messages: list[Message],
    schema: type[T],
    max_tokens: int = 800,
    repair_attempts: int = 1,
) -> tuple[T, LLMResponse]:
    """JSON-mode call validated by Pydantic, with a bounded repair loop.

    MiniMax honours ``response_format=json_object`` but ignores ``json_schema`` (verified live), so the
    schema is enforced here by code. Raises ``StructuredOutputError`` after the repair budget — callers
    then use their deterministic fallback.
    """
    convo = list(messages)
    last_error = ""
    for attempt in range(repair_attempts + 1):
        resp = await provider.chat(task=task, messages=convo, json_mode=True, max_tokens=max_tokens, temperature=0.1)
        try:
            return schema.model_validate(extract_json_object(resp.content)), resp
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:400]
            metrics.LLM_FAILURES.labels(task=task, reason="malformed_output").inc()
            log.warning("llm_malformed_output", task=task, attempt=attempt, error=last_error[:200])
            convo = [
                *messages,
                {"role": "assistant", "content": strip_reasoning(resp.content)[:2000]},
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid JSON for the required schema. Error: "
                        f"{last_error}\nReply again with ONLY the corrected JSON object."
                    ),
                },
            ]
    raise StructuredOutputError(f"{task}: invalid structured output after repair: {last_error[:200]}")
