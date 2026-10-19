# Implementation Plan — Getnet Multi-Agent Support System

Source of truth: `docs/initial-prompt.md` (engineering specification) and
`docs/AI_Hardcore_Engineer-Multi-Agent_Support_System-Tech-Challenge.md` (official statement).
Progress ledger: `docs/PROGRESS.md`.

## 1. Requirements interpretation

| Requirement | Interpretation |
|---|---|
| ≥ 3 agent types + communication mechanism | LangGraph `StateGraph` with typed shared state; agents are graph nodes; communication = state channels with reducers; parallelism = multiple nodes in the same superstep. |
| Router Agent | Hybrid: deterministic rules for high-confidence intents (weather, FX, explicit human, case status, injection/unsupported) → MiniMax-M3 structured output otherwise → deterministic fallback. Code derives the agent list from intent + flags. Never answers. |
| Knowledge Agent | Two modes: Getnet (hybrid RAG over crawled official content, authoritative) and External (Tavily web search). Generates a grounded answer with evidence ids. |
| Customer Support Agent | Bounded MiniMax tool-calling loop over 7 read-only typed tools + 1 approval-gated proposal tool, scoped to an immutable `AuthContext`. |
| API | `POST /v1/chat` accepts the exact challenge payload `{message, user_id}` (+ optional `conversation_id`). |
| Docker | Multi-stage Dockerfile (builder/test/runtime), compose with api, worker, migrate, models-init, postgres, qdrant, redis + optional profiles. |
| Testing | 7 suites (unit, integration, orchestration, security, e2e, multiturn, hitl) + evaluation harness + CI. |
| Bonus | Escalation Agent with interrupt/resume HITL, guardrails, human handoff, evaluation & observability stack — all implemented. |
| LLM | MiniMax-M3 only (OpenAI-compatible API), behind `LLMProvider`; `FakeLLMProvider` for tests only (refused in production). |

Live probe (2026-10-19) of MiniMax-M3 established: `/v1/chat/completions` works; `tools` + `tool_calls`
round-trip works; `response_format={"type":"json_object"}` works; `json_schema` is silently ignored;
thinking is on by default and emitted as `<think>…</think>` inside `content` → we send
`thinking: {type: disabled}` for structured/tool calls and always strip think blocks. Recorded in ADR 004.

## 2. Architecture

Modular monolith: one image, two processes (`api`, `worker`) + one-shot jobs (`migrate`, `models-init`).

```
Client ─► FastAPI (auth JWT/demo, Redis rate limit, idempotency, body limit, headers)
        ─► ownership check + per-conversation lock (Postgres advisory lock)
        ─► LangGraph run (thread = "{customer_id}:{conversation_id}", AsyncPostgresSaver)
             input_guard → load_memory → router → [customer_support ∥ knowledge] → supervisor ⟲ (bounded)
             → synthesizer → decision → (escalation → output_guard → save_memory → await_human: interrupt())
                                       → output_guard → save_memory → END
             resume: Command(resume=OperatorDecision) → await_human → (execute_action) → human_reply → save_human → END
        ─► response JSON + Redis pub/sub event (SSE fan-out across replicas)
Worker: outbox delivery (webhook/log sink, retries, dead letter), SLA timer (injectable clock), TTL purge.
```

## 3. Components (module map)

`app/config.py` settings · `app/api/*` routes, auth, middleware · `app/orchestration/*` graph, state,
routing, planning, supervisor, decision, policy · `app/agents/*` agent nodes + `prompts/*.md` ·
`app/memory/*` store, slots, summary · `app/hitl/*` service, outbox, sla · `app/llm/*` provider,
minimax, fake, reliability · `app/tools/*` typed tools + registry · `app/rag/*` embeddings, sparse,
reranker, qdrant store, retriever · `app/guardrails/*` normalize, input, output, redaction ·
`app/services/web_search.py` · `app/observability/*` logging, metrics, tracing, trace store, cost ·
`app/db/*` models, repositories, seed · `app/worker/main.py` · `ingestion/*` crawler, extract, pdf,
chunker, pipeline, snapshot · `evaluation/*` harness, metrics, datasets, golden set, calibrate, judge.

## 4. Development phases (each ends green: tests run + pass, docs updated, commit, CI green)

| # | Phase | Exit criteria |
|---|---|---|
| 0 | Plan + setup | this file, PROGRESS.md, docs copied, .gitattributes |
| 1 | Skeleton + config + CI | uv project, Settings, FastAPI app with health, Makefile, ruff/mypy/pytest, CI lint+unit green |
| 2 | DB schema / migrations / seed | Alembic migrations, idempotent relative-date seed, repositories, integration tests vs. Postgres |
| 3 | Tools | AuthContext, 7 read tools, create_support_case, approval-gated action, policy table, tests |
| 4 | LLM provider | MiniMax provider, Fake provider, retry/breaker, `make llm-test` live green |
| 5 | Ingestion + retrieval | sources.yaml, crawler, extraction, chunking, PDF, Qdrant hybrid, rerank, snapshot, benchmark + calibration |
| 6 | Agents | router, knowledge, customer support, synthesizer, escalation, human_reply + prompts |
| 7 | Graph + supervisor | LangGraph build, parallel superstep, bounded replanning, decision model |
| 8 | Memory + checkpointer | conversations/turns, slots, summary, AsyncPostgresSaver, restart test |
| 9 | Auth | JWT/JWKS, demo mode, scopes, mismatch 403, rate limit, idempotency |
| 10 | HITL + worker | interrupt/resume, operator API/console/CLI, approvals, outbox, SLA, audit |
| 11 | Guardrails | normalization, injection, canary, redaction, numeric-claim check |
| 12 | Observability | structlog+OTel correlation, metrics, trace store, compose profile, dashboards, alerts |
| 13 | Evaluation | datasets ≥100, multiturn ≥15, HITL, supervisor, golden ≥60, metrics, thresholds, baseline, judge |
| 14 | Docker hardening | digests, non-root, sizes, models-init, proxy scale test, test-docker, verify |
| 15 | Docs | README, ARCHITECTURE, THREAT_MODEL, SLO, TESTING_GUIDE, VIDEO_SCRIPT, SCORECARD, ADRs |
| 16 | Final validation + adversarial QA + repo review | spec procedure from `docker compose down -v` |

(Several phases are implemented in the order above but committed together when they only make sense together; PROGRESS.md records exactly what was verified.)

## 5. Dependencies

Runtime and dev dependencies as listed in the spec, resolved by uv into `uv.lock`. No LangChain/LlamaIndex
layers (LangGraph only). Local models via fastembed (ONNX, CPU) stored in a `models` volume.
External services: MiniMax (LLM), Tavily (web search). Tools via Docker images in CI/Make: Trivy, gitleaks, syft.

## 6. Testing strategy

* Unit tests use the Fake LLM and in-process fakes; datastore-touching tests run against real Postgres/Qdrant/Redis
  (Compose locally, service containers in CI) — no SQLite pretending to be Postgres for integration tests.
* Orchestration tests compile the real graph with the Fake LLM; assert routes, step sequences, span timestamps
  (parallelism), supervisor dependency, budgets; property-style random-decision termination test.
* E2E against the containerized stack over HTTP with `LLM_PROVIDER=fake`; live suite (`make test-live`) with MiniMax.
* Evaluation harness separate from tests: deterministic metrics, thresholds, baseline deltas, `--strict` gate.

## 7. Security strategy

Identity only from verified JWT `sub` (RS256 via JWKS); `AUTH_MODE=demo` for the exact challenge payload,
refused in production; scopes per route enforced by dependencies + a route-enumeration test; `AuthContext`
immutable and passed by code into tools/repositories/memory/HITL; Redis rate limiting; layered guardrails;
canary token; redaction before persistence/logging; threat model with STRIDE + OWASP LLM Top 10 (2025).

## 8. Memory strategy

Postgres `conversations` + `conversation_turns` (redacted) are the conversational source of truth, scoped by
customer; LangGraph `AsyncPostgresSaver` checkpoints graph runs (thread id built by server code only).
Layers: window (`MEMORY_WINDOW_TURNS`), rolling LLM summary over `MEMORY_TOKEN_BUDGET` (fallback truncation),
deterministic slots from tool results. TTL purge in the worker; erasure endpoint deletes turns, summary, checkpoints.

## 9. HITL strategy

Escalation node persists case + outbox row (one transaction), the customer gets the handoff answer, the turn is
saved, then `await_human` calls `interrupt(payload)`. The checkpoint id of the interrupt is stored on the case.
Operator reply/decision resumes the same thread (`Command(resume=...)`), flowing into `execute_action`
(approvals, with the case owner's AuthContext) and `human_reply` (MiniMax rewrite or verbatim) → output guard →
appended as `human_operator` turn → SSE event. Idempotent resume via case status transition + Redis/DB guard.

## 10. Observability strategy

structlog JSON logs with OTel trace/span ids; OTel spans per node/tool/retrieval; Prometheus metrics (spec list);
Redis-backed debug trace store (sanitized, dev only); per-request token cost; `observability` profile with
OTel Collector, Jaeger, Prometheus (alert rules), Grafana (provisioned dashboard).

## 11. RAG strategy

`ingestion/sources.yaml` seeds (site.getnet.com.br product/FAQ/help pages + official PDF manuals, getnet.net/en,
getnet.net/pt support pages), allow/deny patterns, categories, render flag. Extraction removes header/nav/footer/
modals/scripts, keeps headings, FAQ Q/A, procedures, tables. Structure-aware chunking. Dense multilingual +
BM25 sparse named vectors in Qdrant, RRF fusion via Query API, cross-encoder rerank, thresholds calibrated on the
golden set. Model choice by benchmark (Recall@5, MRR, nDCG@5, latency). Snapshot export/import for offline demo.

## 12. Anticipated risks

| Risk | Mitigation |
|---|---|
| MiniMax structured output not schema-enforced | json_object + Pydantic validation + one repair retry + deterministic fallback |
| MiniMax latency/outage | timeouts, retries w/ jitter, circuit breaker, deterministic fallbacks |
| Getnet site changes / JS pages | sources.yaml, optional Playwright profile, committed snapshot fixture |
| Model downloads make images huge | models-init + named volume, never baked in |
| Interrupted thread receiving new input | interrupt checkpoint id stored on the case; resume targets that checkpoint (verified by test) |
| Repo on Windows drive (modes/CRLF) | .gitattributes LF, `git update-index --chmod=+x` |
| Live eval cost | small live subsets, token budget, cost metrics |

## 13. Acceptance criteria mapped to the rubric

| Rubric row | Acceptance evidence |
|---|---|
| Business & Challenge Fit (10%) | 10 challenge scenarios PT+EN as eval cases passing offline and live; `/ui`; multi-turn demo |
| Agentic Reasoning & Orchestration (15%) | LangGraph graph; Workflow 1 parallel proven by span timestamps; Workflow 2 supervisor dependency test; budget/termination tests; ADR with measured cost |
| Context, Knowledge & RAG (10%) | hybrid dense+sparse RRF, reranker chosen by benchmark, calibrate report, PDF + render report, citations faithfulness test, injection-in-evidence test |
| Tool & Action Engineering (10%) | typed tools, policy table, read/write separation, approval-gated execution tests |
| Security & Identity (13%) | JWT/JWKS tests, mismatch 403, scope enumeration test, Redis rate limit, guardrail suite, THREAT_MODEL |
| Memory, State & Resilience (8%) | multiturn suite, restart E2E, 2-replica test, idempotency, concurrency test |
| Human Oversight & Risk (7%) | HITL suite: interrupt/resume, approve/reject/edit, SLA bump with injected clock, audit rows |
| Evaluation & Quality (14%) | `make verify`, ≥100 cases, golden ≥60, multiturn/HITL/supervisor suites, `--strict` in CI |
| Observability, Reliability & Cost (7%) | dashboards/alerts in repo, cost per request in trace + metrics, breaker metrics, outage tests |
| Production Engineering (6%) | CI green, Alembic, non-root digest-pinned image size reported, no per-process correctness state, scale test |
| Engineering Judgment (8%) | ADRs with measured evidence, honest limitations |
