"""Per-request token accounting, cost estimation and budget enforcement.

A ``RequestUsage`` accumulator is bound to a contextvar for the duration of a chat request (or HITL
resume). Every LLM call records into it; ``MAX_TOKENS_PER_REQUEST`` is enforced by code *before* a call
is made, so a runaway loop degrades gracefully instead of burning tokens.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.observability import metrics


class TokenBudgetExceeded(Exception):
    pass


@dataclass
class RequestUsage:
    budget_tokens: int
    price_input_per_mtok: float
    price_output_per_mtok: float
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    calls_by_task: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return round(
            self.input_tokens / 1e6 * self.price_input_per_mtok + self.output_tokens / 1e6 * self.price_output_per_mtok,
            6,
        )

    def check(self, estimated_tokens: int) -> None:
        if self.total_tokens + estimated_tokens > self.budget_tokens:
            raise TokenBudgetExceeded(
                f"token budget exhausted ({self.total_tokens}+{estimated_tokens} > {self.budget_tokens})"
            )

    def record(self, task: str, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.llm_calls += 1
        self.calls_by_task[task] = self.calls_by_task.get(task, 0) + 1
        metrics.LLM_TOKENS.labels(direction="input").inc(input_tokens)
        metrics.LLM_TOKENS.labels(direction="output").inc(output_tokens)
        metrics.LLM_COST.inc(
            input_tokens / 1e6 * self.price_input_per_mtok + output_tokens / 1e6 * self.price_output_per_mtok
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "input": self.input_tokens,
            "output": self.output_tokens,
            "total": self.total_tokens,
            "llm_calls": self.llm_calls,
            "calls_by_task": dict(self.calls_by_task),
            "usd": self.cost_usd,
            "budget": self.budget_tokens,
        }


current_usage: ContextVar[RequestUsage | None] = ContextVar("current_usage", default=None)


def estimate_tokens(text: str) -> int:
    """Deterministic, provider-independent estimate (~4 chars/token, PT/EN)."""
    return max(1, len(text) // 4)
