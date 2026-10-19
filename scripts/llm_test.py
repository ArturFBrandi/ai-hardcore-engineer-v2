"""`make llm-test`: minimal REAL MiniMax-M3 requests.

Confirms authentication, model access, API format and response parsing for: a plain completion,
a structured (JSON) output validated by Pydantic, and a tool call round-trip. Never prints the key.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from pydantic import BaseModel

from app.config import LLMProviderName, get_settings
from app.errors import ProviderError
from app.llm.base import structured
from app.llm.minimax import MiniMaxProvider
from app.observability.cost import RequestUsage, current_usage


class _Route(BaseModel):
    intent: str
    language: str


async def main() -> int:
    settings = get_settings()
    if settings.llm_provider is not LLMProviderName.MINIMAX:
        print("LLM_PROVIDER is not 'minimax'; llm-test validates the real provider only.")
        return 2
    if not settings.minimax_api_key.get_secret_value():
        print(
            "MINIMAX_API_KEY is not set.\n"
            "  1. cp .env.example .env\n  2. put your key from https://platform.minimax.io in MINIMAX_API_KEY\n"
            "  3. re-run: make llm-test"
        )
        return 2
    provider = MiniMaxProvider(settings)
    usage = RequestUsage(100_000, settings.llm_price_input_per_mtok, settings.llm_price_output_per_mtok)
    current_usage.set(usage)
    results: list[tuple[str, bool, str]] = []

    t = time.perf_counter()
    try:
        r = await provider.chat(
            task="llm_test_plain",
            messages=[{"role": "user", "content": "Reply with exactly: pong"}],
            max_tokens=20,
        )
        results.append(
            ("plain completion", "pong" in r.content.lower(), f"{r.content!r} in {time.perf_counter() - t:.2f}s")
        )
    except ProviderError as exc:
        results.append(("plain completion", False, str(exc)))

    t = time.perf_counter()
    try:
        parsed, _ = await structured(
            provider,
            task="llm_test_structured",
            messages=[
                {
                    "role": "system",
                    "content": 'Classify the message. Reply with JSON {"intent": str, "language": "pt"|"en"}.',
                },
                {"role": "user", "content": "Quando cai o dinheiro das vendas de ontem?"},
            ],
            schema=_Route,
        )
        results.append(
            (
                "structured output",
                parsed.language in {"pt", "en"},
                f"{parsed.model_dump()} in {time.perf_counter() - t:.2f}s",
            )
        )
    except ProviderError as exc:
        results.append(("structured output", False, str(exc)))

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_customer_terminals",
                "description": "List the merchant's card machines",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    ]
    t = time.perf_counter()
    try:
        msgs = [
            {"role": "system", "content": "You are a support agent. Use tools to look up customer data."},
            {"role": "user", "content": "Which card machines do I have?"},
        ]
        r = await provider.chat(task="llm_test_tools", messages=msgs, tools=tools, max_tokens=300)
        ok = bool(r.tool_calls) and r.tool_calls[0].name == "get_customer_terminals"
        detail = f"tool_calls={[tc.name for tc in r.tool_calls]}"
        if ok:
            msgs.append(r.assistant_message())
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": r.tool_calls[0].id,
                    "content": json.dumps({"terminals": [{"model": "Get Smart", "status": "active"}]}),
                }
            )
            r2 = await provider.chat(task="llm_test_tools", messages=msgs, tools=tools, max_tokens=300)
            ok = "smart" in r2.content.lower()
            detail += f"; final={r2.content[:80]!r}"
        results.append(("tool call round-trip", ok, f"{detail} in {time.perf_counter() - t:.2f}s"))
    except ProviderError as exc:
        results.append(("tool call round-trip", False, str(exc)))

    print(f"MiniMax model={settings.minimax_model} base_url={settings.minimax_base_url}")
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"  usage: {usage.as_dict()}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
