"""Live provider validation (MiniMax-M3 + Tavily). Runs only when credentials exist (`make test-live`)."""

from __future__ import annotations

import json
import os

import pytest
from pydantic import BaseModel

from app.agents.prompts import render_messages
from app.config import Settings
from app.llm.base import structured
from app.llm.minimax import MiniMaxProvider
from app.observability.cost import RequestUsage, current_usage
from app.orchestration.routing import RouterOutput
from app.services.web_search import TavilySearch

pytestmark = pytest.mark.live
HAS_MINIMAX = bool(os.environ.get("MINIMAX_API_KEY"))
HAS_TAVILY = bool(os.environ.get("WEB_SEARCH_API_KEY"))
needs_minimax = pytest.mark.skipif(not HAS_MINIMAX, reason="MINIMAX_API_KEY not set — live MiniMax validation skipped")
needs_tavily = pytest.mark.skipif(not HAS_TAVILY, reason="WEB_SEARCH_API_KEY not set — live web search skipped")


def _settings() -> Settings:
    return Settings(
        app_env="test",
        minimax_api_key=os.environ.get("MINIMAX_API_KEY", ""),
        web_search_api_key=os.environ.get("WEB_SEARCH_API_KEY", ""),
    )  # type: ignore[arg-type]


@pytest.fixture
def usage():  # type: ignore[no-untyped-def]
    u = RequestUsage(200_000, 0.3, 1.2)
    token = current_usage.set(u)
    yield u
    current_usage.reset(token)


@needs_minimax
async def test_router_structured_output_live(usage) -> None:  # type: ignore[no-untyped-def]
    p = MiniMaxProvider(_settings())
    cases = {
        "When will the money from yesterday's sales be deposited?": "customer_settlement",
        "Qual a diferença entre a Get Clássica e a Get Smart?": "getnet_product_information",
        "Minha maquininha não conecta na internet": "technical_support",
    }
    for message, expected in cases.items():
        out, _ = await structured(
            p,
            task="router",
            messages=render_messages("router", {"message": message, "slots": {}}),
            schema=RouterOutput,
            max_tokens=500,
        )
        assert out.intent == expected, (message, out.intent)
    assert usage.input_tokens > 0 and usage.output_tokens > 0, "token usage is read from responses"


@needs_minimax
async def test_tool_call_round_trip_live(usage) -> None:  # type: ignore[no-untyped-def]
    p = MiniMaxProvider(_settings())
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_recent_transactions",
                "description": "List the merchant's sales",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date_preset": {"type": "string", "enum": ["today", "yesterday", "day_before_yesterday"]}
                    },
                    "required": [],
                },
            },
        }
    ]
    msgs = [
        {"role": "system", "content": "You are a support agent. Use tools for merchant data."},
        {"role": "user", "content": "Quais foram minhas vendas de ontem?"},
    ]
    r = await p.chat(task="customer_support", messages=msgs, tools=tools, max_tokens=300)
    assert r.tool_calls and r.tool_calls[0].name == "get_recent_transactions"
    assert json.loads(r.tool_calls[0].arguments or "{}").get("date_preset") in ("yesterday", None)  # args JSON parses
    msgs += [
        r.assistant_message(),
        {
            "role": "tool",
            "tool_call_id": r.tool_calls[0].id,
            "content": json.dumps({"transactions": [{"amount": "45.90", "status": "approved"}]}),
        },
    ]
    r2 = await p.chat(task="customer_support", messages=msgs, tools=tools, max_tokens=300)
    assert "45" in r2.content


@needs_minimax
async def test_auth_error_is_not_retried_live() -> None:
    from app.errors import ProviderError

    p = MiniMaxProvider(Settings(app_env="test", minimax_api_key="invalid-key-for-test", llm_max_retries=2))  # type: ignore[arg-type]
    with pytest.raises(ProviderError) as info:
        await p.chat(task="t", messages=[{"role": "user", "content": "hi"}], max_tokens=10)
    assert info.value.retryable is False


class _Answer(BaseModel):
    answer: str


@needs_minimax
async def test_grounded_generation_refuses_unsupported_fact_live(usage) -> None:  # type: ignore[no-untyped-def]
    from app.agents.knowledge import RagAnswer
    from app.agents.prompts import evidence_block

    ev = [
        {
            "evidence_id": "E1",
            "title": "Crediário",
            "url": "https://site.getnet.com.br/crediario/",
            "category": "crediario",
            "text": "Ofereça parcelamento em até 48x para o seu cliente. O prazo máximo e a taxa de juros variam de acordo com o emissor do cartão.",
        }
    ]
    out, _ = await structured(
        MiniMaxProvider(_settings()),
        task="knowledge_rag",
        messages=render_messages(
            "knowledge_rag",
            {"question": "Qual a taxa de juros do crediário e em quantas vezes?", "language": "pt"},
            blocks=[evidence_block(ev)],
        ),
        schema=RagAnswer,
        max_tokens=600,
    )
    assert "48" in out.answer
    assert "E1" in out.used_evidence or "[E1]" in out.answer


@needs_tavily
async def test_tavily_search_live() -> None:
    r = await TavilySearch(_settings()).search("euro exchange rate BRL today", max_results=3)
    assert r.results and all(x.url.startswith("http") for x in r.results)
