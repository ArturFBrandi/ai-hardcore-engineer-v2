# ADR 005 — Tavily as the external web-search provider

**Status:** accepted · 2026-10-19

## Context
Weather, exchange rates and other current facts must never come from model memory.

## Decision
`app/services/web_search.py`: `WebSearchProvider` protocol, `TavilySearch` implementation (`POST /search`, Bearer key,
`include_answer`, 15 s timeout, one retry on 429/5xx, circuit breaker), `DisabledWebSearch` when unconfigured. Results
are untrusted evidence (`<web_results untrusted="true">`). Failure → honest "cannot retrieve current external
information right now"; the decision model has `web_search_failure` penalty 0 so web-only questions never open cases.

## Alternatives considered
Brave/Serper (similar; Tavily returns cleaned content + a synthesized `answer`, fewer tokens to ground on).

## Measured evidence
Live probe: HTTP 200 in 2.47 s with EUR/BRL results. `tests/orchestration/test_workflows.py::test_web_path_and_honest_outage`
(fake provider, including the outage path) and `tests/live/test_live_provider.py::test_tavily_search_live` (real API).
