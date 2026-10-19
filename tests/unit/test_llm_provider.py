"""MiniMax provider behaviour with a mocked HTTP transport (no credits needed)."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from app.config import AppEnv, Settings
from app.errors import ConfigurationError, ProviderError
from app.llm.base import StructuredOutputError, extract_json_object, strip_reasoning, structured
from app.llm.factory import build_llm
from app.llm.fake import FakeLLMProvider
from app.llm.minimax import MiniMaxProvider
from app.llm.reliability import BreakerState, CircuitBreaker
from app.observability.cost import RequestUsage, current_usage

URL = "https://api.minimax.io/v1/chat/completions"


class Route:
    """Scripted MockTransport: returns/raises queued items, records requests."""

    def __init__(self, *items: object, repeat: object | None = None) -> None:
        self.items = list(items)
        self.repeat = repeat
        self.calls: list[httpx.Request] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == URL
        self.calls.append(request)
        item = self.items.pop(0) if self.items else self.repeat
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, httpx.Response)
        return item

    def provider(self, settings: Settings) -> MiniMaxProvider:
        return MiniMaxProvider(settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {"minimax_api_key": "test-key", "llm_max_retries": 2, "app_env": "test"}
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def _completion(
    content: str | None = "ok", tool_calls: list[dict[str, object]] | None = None, finish: str = "stop"
) -> dict[str, object]:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "MiniMax-M3",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.llm.reliability.backoff_delay", lambda attempt: 0.0)


async def test_plain_completion_strips_think_and_counts_tokens() -> None:
    route = Route(httpx.Response(200, json=_completion("<think>secret plan</think>\n\nOlá!")))
    usage = RequestUsage(10_000, 0.3, 1.2)
    token = current_usage.set(usage)
    try:
        r = await route.provider(_settings()).chat(task="t", messages=[{"role": "user", "content": "hi"}])
    finally:
        current_usage.reset(token)
    assert r.content == "Olá!"
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    body = json.loads(route.calls[0].content)
    assert body["model"] == "MiniMax-M3"
    assert body["thinking"] == {"type": "disabled"}
    assert route.calls[0].headers["authorization"] == "Bearer test-key"


async def test_tool_calls_parsed_and_assistant_message_preserved() -> None:
    tc = [{"id": "call_1", "type": "function", "function": {"name": "get_customer_terminals", "arguments": "{}"}}]
    route = Route(httpx.Response(200, json=_completion("", tc, "tool_calls")))
    r = await route.provider(_settings()).chat(task="t", messages=[{"role": "user", "content": "x"}], tools=[{}])
    assert r.tool_calls[0].name == "get_customer_terminals"
    assert r.assistant_message()["tool_calls"][0]["id"] == "call_1"


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_retryable_statuses_are_retried(status: int) -> None:
    route = Route(httpx.Response(status, json={"error": "x"}), httpx.Response(200, json=_completion("fine")))
    r = await route.provider(_settings()).chat(task="t", messages=[{"role": "user", "content": "x"}])
    assert r.content == "fine"
    assert route.call_count == 2


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_auth_and_validation_errors_not_retried(status: int) -> None:
    route = Route(repeat=httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(ProviderError) as info:
        await route.provider(_settings()).chat(task="t", messages=[{"role": "user", "content": "x"}])
    assert route.call_count == 1
    assert info.value.retryable is False


async def test_retries_bounded() -> None:
    route = Route(repeat=httpx.Response(503, json={}))
    with pytest.raises(ProviderError):
        await route.provider(_settings(llm_max_retries=2)).chat(task="t", messages=[{"role": "user", "content": "x"}])
    assert route.call_count == 3


async def test_timeout_is_retryable_then_fails() -> None:
    route = Route(repeat=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderError):
        await route.provider(_settings(llm_max_retries=1)).chat(task="t", messages=[{"role": "user", "content": "x"}])
    assert route.call_count == 2


async def test_empty_response_raises() -> None:
    route = Route(repeat=httpx.Response(200, json=_completion("")))
    with pytest.raises(ProviderError, match="empty"):
        await route.provider(_settings(llm_max_retries=0)).chat(task="t", messages=[{"role": "user", "content": "x"}])


async def test_breaker_opens_and_short_circuits() -> None:
    route = Route(repeat=httpx.Response(503, json={}))
    p = route.provider(_settings(llm_max_retries=0, llm_breaker_failure_threshold=2))
    for _ in range(2):
        with pytest.raises(ProviderError):
            await p.chat(task="t", messages=[{"role": "user", "content": "x"}])
    assert p.breaker.state is BreakerState.OPEN
    with pytest.raises(ProviderError, match="circuit open"):
        await p.chat(task="t", messages=[{"role": "user", "content": "x"}])
    assert route.call_count == 2


def test_breaker_half_open_after_reset() -> None:
    now = [0.0]
    b = CircuitBreaker("x", failure_threshold=1, reset_seconds=10, clock=lambda: now[0])
    b.on_failure()
    assert b.state is BreakerState.OPEN
    now[0] = 11
    assert b.state is BreakerState.HALF_OPEN
    b.on_success()
    assert b.state is BreakerState.CLOSED


async def test_token_budget_enforced_before_call() -> None:
    usage = RequestUsage(budget_tokens=50, price_input_per_mtok=0.3, price_output_per_mtok=1.2)
    token = current_usage.set(usage)
    try:
        with pytest.raises(ProviderError, match="budget"):
            await Route().provider(_settings()).chat(task="t", messages=[{"role": "user", "content": "x" * 400}])
    finally:
        current_usage.reset(token)


def test_missing_key_is_explicit() -> None:
    with pytest.raises(ProviderError, match="MINIMAX_API_KEY"):
        MiniMaxProvider(_settings(minimax_api_key=""))


def test_fake_refused_in_production() -> None:
    s = _settings(app_env="test", llm_provider="fake").model_copy(update={"app_env": AppEnv.PRODUCTION})
    with pytest.raises(ConfigurationError):
        build_llm(s)


class _Out(BaseModel):
    intent: str


async def test_structured_repairs_malformed_json_once() -> None:
    fake = FakeLLMProvider(handlers={})
    fake.script("router", "not json at all", '```json\n{"intent": "weather"}\n```')
    out, _ = await structured(fake, task="router", messages=[{"role": "user", "content": "x"}], schema=_Out)
    assert out.intent == "weather"
    assert len(fake.calls_for("router")) == 2


async def test_structured_gives_up_after_repair_budget() -> None:
    fake = FakeLLMProvider(handlers={})
    fake.script("router", "{bad", '{"wrong": 1}')
    with pytest.raises(StructuredOutputError):
        await structured(fake, task="router", messages=[{"role": "user", "content": "x"}], schema=_Out)


def test_json_extraction_and_reasoning_strip() -> None:
    assert extract_json_object('<think>hmm</think> text {"a": 1} trailing') == {"a": 1}
    assert strip_reasoning("<THINK>x</THINK>answer") == "answer"
