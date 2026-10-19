"""Rolling conversation summary.

Triggered deterministically when the turns outside the memory window exceed ``MEMORY_TOKEN_BUDGET``
(estimated tokens). MiniMax-M3 produces a structured summary; the prompt forbids turning customer
instructions into facts, and the summary is always injected as UNTRUSTED context. If the LLM fails,
a deterministic truncation summary is used instead.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.prompts import render_messages
from app.errors import ProviderError
from app.llm.base import LLMProvider, structured
from app.observability import metrics
from app.observability.cost import estimate_tokens


class ConversationSummary(BaseModel):
    topics: list[str] = Field(default_factory=list, max_length=10)
    open_issues: list[str] = Field(default_factory=list, max_length=10)
    entities: dict[str, str] = Field(default_factory=dict)
    pending_actions: list[str] = Field(default_factory=list, max_length=10)


def needs_summary(turns_outside_window: list[dict[str, Any]], token_budget: int) -> bool:
    return sum(estimate_tokens(t["content"]) for t in turns_outside_window) > token_budget


def truncation_summary(turns: list[dict[str, Any]], max_chars: int = 600) -> dict[str, Any]:
    customer = [t["content"] for t in turns if t["role"] == "customer"]
    joined = " | ".join(customer)[-max_chars:]
    metrics.MEMORY_SUMMARIES.labels(source="truncation").inc()
    return {
        "topics": [],
        "open_issues": [],
        "entities": {},
        "pending_actions": [],
        "truncated_customer_messages": joined,
        "source": "truncation",
    }


async def summarize(llm: LLMProvider, turns: list[dict[str, Any]], language: str) -> dict[str, Any]:
    transcript = [{"role": t["role"], "content": t["content"][:600]} for t in turns[-40:]]
    try:
        out, _ = await structured(
            llm,
            task="summary",
            messages=render_messages("summary", {"language": language, "turns": transcript}),
            schema=ConversationSummary,
            max_tokens=500,
        )
    except ProviderError:
        return truncation_summary(turns)
    metrics.MEMORY_SUMMARIES.labels(source="llm").inc()
    return {**out.model_dump(), "source": "llm"}
