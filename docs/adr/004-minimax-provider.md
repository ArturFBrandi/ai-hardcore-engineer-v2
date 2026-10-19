# ADR 004 — MiniMax-M3 through its OpenAI-compatible API behind `LLMProvider`

**Status:** accepted · 2026-10-19

## Context
MiniMax-M3 (minimax.io) is the mandatory reasoning LLM. Its docs list `thinking`, `reasoning_split`, `tools`,
`max_completion_tokens`; `response_format`/`tool_choice` are not documented.

## Decision
`app/llm/minimax.py` uses the official `openai` SDK against `https://api.minimax.io/v1` with SDK retries disabled
(ours: jittered backoff on 408/409/429/5xx/timeouts only, circuit breaker, token budget). Every request sends
`thinking: {type: disabled}`; `<think>` blocks are stripped defensively. Structured output = `response_format:
json_object` + Pydantic validation + one repair round (`app/llm/base.py::structured`) + deterministic fallback.
`FakeLLMProvider` (refused in production) implements the same protocol for tests.

## Live probe (2026-10-19, `scripts/llm_test.py` and an exploratory spike)
- Plain completion OK; with default thinking the content was `<think>…</think>\n\nOlá!` (6.9 s); with thinking disabled `Olá!` (1.9–2.4 s).
- `response_format=json_object` → valid JSON; `json_schema` → **ignored** (prose returned) → schema enforced in code.
- `tools` → `tool_calls` with JSON arguments; full round-trip (assistant message incl. `tool_calls` + `tool` message) answered correctly.
- `usage.prompt_tokens/completion_tokens` present → cost metrics. A bad key returns 401 (not retried).

## Alternatives considered
MiniMax native API (no tool-calling advantage found); switching models when an assumption failed (forbidden by the brief).

## Consequences
The rest of the code depends only on `LLMProvider`. Price constants from MiniMax pay-as-you-go: $0.30/M input,
$1.20/M output (≤512k context).

## Measured evidence
`make llm-test`: 3/3 PASS (4 calls, 1,291 tokens, $0.00047). `make test-live`: 5 passed (router structured output, tool
round-trip, no-retry on 401, grounded generation, Tavily).
