"""MiniMax-M3 provider over MiniMax's OpenAI-compatible Chat Completions API.

Verified behaviour (live probe, see ADR 004):
* ``/v1/chat/completions`` with ``tools`` → ``tool_calls`` round-trips; ``response_format=json_object`` works;
  ``json_schema`` is ignored (schema enforced by ``app.llm.base.structured``).
* Thinking is on by default and returned as ``<think>…</think>`` inside ``content``. We send
  ``thinking: {type: disabled}`` (lower latency, no reasoning to strip) and still strip defensively.
* ``usage.prompt_tokens`` / ``usage.completion_tokens`` feed cost metrics.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI
from opentelemetry import trace

from app.config import Settings
from app.errors import ProviderError
from app.llm.base import LLMResponse, Message, ToolCall, strip_reasoning
from app.llm.reliability import CircuitBreaker, CircuitOpenError, with_retries
from app.observability import metrics
from app.observability.cost import TokenBudgetExceeded, current_usage, estimate_tokens
from app.observability.logging import get_logger

log = get_logger(__name__)
_tracer = trace.get_tracer("getnet.llm")

_RETRYABLE = (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, _RETRYABLE):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code in (408, 409, 429, 500, 502, 503, 504, 529)


class MiniMaxProvider:
    name = "minimax"

    def __init__(self, settings: Settings, http_client: Any = None) -> None:
        key = settings.minimax_api_key.get_secret_value()
        if not key:
            raise ProviderError(
                "MINIMAX_API_KEY is not set. Add it to .env (see .env.example) or use LLM_PROVIDER=fake for tests."
            )
        self.model = settings.minimax_model
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=settings.minimax_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,  # retries are ours (jitter + breaker + retryability rules)
            http_client=http_client,
        )
        self.breaker = CircuitBreaker(
            "minimax", settings.llm_breaker_failure_threshold, settings.llm_breaker_reset_seconds
        )

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
        usage = current_usage.get()
        if usage is not None:
            try:
                usage.check(estimate_tokens(str(messages)) + max_tokens)
            except TokenBudgetExceeded as exc:
                metrics.LLM_FAILURES.labels(task=task, reason="budget").inc()
                raise ProviderError(str(exc)) from exc
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature if temperature is not None else self._settings.llm_temperature,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        started = time.perf_counter()

        async def _call() -> Any:
            return await self._client.chat.completions.create(**kwargs)

        span = _tracer.start_span(f"llm.{task}", attributes={"llm.model": self.model, "llm.task": task})
        try:
            raw = await with_retries(
                _call,
                is_retryable=_is_retryable,
                max_retries=self._settings.llm_max_retries,
                breaker=self.breaker,
                name="minimax",
            )
        except CircuitOpenError as exc:
            metrics.LLM_REQUESTS.labels(task=task, status="circuit_open").inc()
            metrics.LLM_FAILURES.labels(task=task, reason="circuit_open").inc()
            raise ProviderError("MiniMax circuit open", retryable=True) from exc
        except openai.APIStatusError as exc:
            reason = "auth" if exc.status_code in (401, 403) else f"http_{exc.status_code}"
            metrics.LLM_REQUESTS.labels(task=task, status="error").inc()
            metrics.LLM_FAILURES.labels(task=task, reason=reason).inc()
            log.error("llm_http_error", task=task, status_code=exc.status_code)
            raise ProviderError(f"MiniMax HTTP {exc.status_code}", retryable=_is_retryable(exc)) from exc
        except (openai.APIError, httpx.HTTPError) as exc:
            metrics.LLM_REQUESTS.labels(task=task, status="error").inc()
            metrics.LLM_FAILURES.labels(task=task, reason=type(exc).__name__).inc()
            raise ProviderError(f"MiniMax request failed: {type(exc).__name__}", retryable=True) from exc
        finally:
            span.end()
        latency = time.perf_counter() - started
        metrics.LLM_DURATION.labels(task=task).observe(latency)

        if not raw.choices:
            metrics.LLM_REQUESTS.labels(task=task, status="empty").inc()
            metrics.LLM_FAILURES.labels(task=task, reason="empty_response").inc()
            raise ProviderError("MiniMax returned no choices", retryable=True)
        choice = raw.choices[0]
        msg = choice.message
        content = strip_reasoning(msg.content or "")
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        in_tok = int(getattr(raw.usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(raw.usage, "completion_tokens", 0) or 0)
        if usage is not None:
            usage.record(task, in_tok, out_tok)
        else:
            metrics.LLM_TOKENS.labels(direction="input").inc(in_tok)
            metrics.LLM_TOKENS.labels(direction="output").inc(out_tok)
        if not content and not tool_calls:
            metrics.LLM_REQUESTS.labels(task=task, status="empty").inc()
            metrics.LLM_FAILURES.labels(task=task, reason="empty_response").inc()
            raise ProviderError("MiniMax returned an empty message", retryable=True)
        if choice.finish_reason == "length":
            metrics.LLM_FAILURES.labels(task=task, reason="truncated").inc()
            log.warning("llm_truncated", task=task, max_tokens=max_tokens)
        metrics.LLM_REQUESTS.labels(task=task, status="ok").inc()
        log.info(
            "llm_call",
            task=task,
            model=self.model,
            latency_ms=round(latency * 1000),
            input_tokens=in_tok,
            output_tokens=out_tok,
            tool_calls=len(tool_calls),
            finish_reason=choice.finish_reason,
        )
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=in_tok,
            output_tokens=out_tok,
            finish_reason=choice.finish_reason,
            latency_ms=round(latency * 1000, 1),
        )
