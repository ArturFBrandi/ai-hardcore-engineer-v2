# Testing guide

## Strategy
Tests target **behaviour and structure, never exact LLM prose**: intents, routes, plan steps, supervisor reasons,
tool calls, statuses, citations, slots, audit rows, HTTP codes. The deterministic system (rules, plans, supervisor,
tools, retrieval, guardrails, decision model, memory, HITL) is exercised with `FakeLLMProvider` — a test double that
reads the same `<task_input>` / evidence blocks the real prompts carry — against **real** PostgreSQL, Qdrant and Redis
(no SQLite stand-ins). MiniMax-M3 quality is measured separately (live suite + live evaluation). No test sleeps for
timers: the worker takes an injectable clock.

| Suite | Path | Needs | What it proves |
|---|---|---|---|
| unit | `tests/unit` | nothing | config fail-fast, provider retry/no-retry/breaker/budget/repair, extraction & chunking, redaction, normalization, routing rules, plan validation, decision model, slots, summary trigger, Tavily client, metrics, **property test: supervisor always terminates within budget** |
| integration | `tests/integration` | datastores | 21 tool tests (isolation, invalid ranges, DB failure, approval gate, idempotent case + outbox + audit, append-only audit) + HTTP contract |
| orchestration | `tests/orchestration` | datastores + models | real LangGraph: Workflow 1 parallelism (overlapping timestamps), Workflow 2 dependency + product filter, web path & outage, insufficient evidence → escalation, knowledge gap → honest answer, invalid router output → fallback, LLM outage → deterministic paths, budget, no-loop regression |
| security | `tests/security` | datastores + models | route auth enumeration, invalid tokens, body/sub mismatch, scopes, idempotency replay across subjects, 8 encoded/obfuscated injections, cross-customer/bulk/SQL/credential requests, LLM tool manipulation, RAG-planted injection, canary leak, hallucinated numbers, redaction in memory+logs, rate limit, body limit, headers |
| multi-turn | `tests/multiturn` | datastores + models | coreference, carry-over + escalation on the Nth failure, language persistence, case awareness, topic switch, injection isolation (turn 1 → turn 3), split injection, summary trigger, erasure incl. checkpoints, TTL purge, concurrent turns serialized |
| HITL | `tests/hitl` | datastores + models | interrupt → reply → resume → SSE → next turn, duplicate submit, approve/reject/edit, restart before resume, customer turn while waiting, SLA bump (injected clock), outbox retry/dead letter, audit, role separation |
| E2E | `tests/e2e/test_stack.py` | composed stack (`LLM_PROVIDER=fake`) | real images over HTTP: health, challenge payload, **API restart between turns**, HITL + SSE, identity rules, **Qdrant / Redis / PostgreSQL outages**, `AUTH_MODE=demo` |
| scale | `tests/e2e/test_scale.py` | 2 replicas + nginx | context, turn numbering, traces and rate limits shared across replicas |
| live | `tests/live` | MiniMax + Tavily keys | structured routing, tool round-trip, no retry on 401, grounded generation, Tavily |

## How integration testing of the orchestration works
1. `tests/conftest.py` creates `getnet_test`, applies Alembic + checkpointer setup, seeds, downloads the configured
   models (cached in `MODELS_DIR`) and indexes the committed snapshot into collection `getnet_test` (skipped when complete).
2. A session-scoped `Container` (same factory as production) is built with the fake LLM and a fake web search; the
   `api` fixture runs the real FastAPI app with its lifespan over an ASGI transport; tokens are minted with throwaway keys.
3. Tests script the fake per task when they need specific behaviour (`fake_llm.script("router", "not json", ...)`,
   `fail_all = ProviderError(...)`) or monkeypatch the retriever to simulate empty/poisoned/failed retrieval.
4. Assertions read the run's state/events (timestamps, supervisor decisions, filters) or the HTTP/API surfaces.

## Commands
```bash
make test               # all offline suites (starts datastores via docker compose; needs only uv + docker)
make test-unit | test-integration | test-orchestration | test-security | test-multiturn | test-hitl
make coverage           # gate: >= 85% of app/ (measured 90.9% on 2026-10-20)
make test-docker        # whole offline suite inside the Dockerfile `test` stage
make up && make test-e2e        # E2E against the containerized stack (use LLM_PROVIDER=fake for offline)
make scale-test         # 2 API replicas behind nginx
make test-live          # real MiniMax-M3 / Tavily (skipped with a message without keys)
make eval               # evaluation harness with --strict regression gates
make verify             # everything above from a clean state, with a summary table
```
Missing dependencies fail with the exact command to run (e.g. `uv not found → curl … | sh`, datastores not reachable → `make datastores`).
