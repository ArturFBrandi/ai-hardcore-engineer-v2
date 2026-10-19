# ADR 002 — Modular monolith (one image, api + worker processes)

**Status:** accepted · 2026-10-19

## Context
Six logical agents, a worker (outbox, SLA, retention) and several datastores. Splitting agents into services would
add network hops, distributed transactions (case + outbox + audit must commit atomically) and operational overhead.

## Decision
One deployable image (`Dockerfile`, target `runtime`) with roles selected by `docker/entrypoint.sh`: `api`, `worker`,
`migrate`, `seed`, `models`, `snapshot`, `ingest`. Module boundaries (`app/agents`, `app/tools`, `app/rag`, `app/hitl`,
`app/memory`, …) keep a later split possible.

## Alternatives considered
Microservice per agent (rejected: latency + failure modes for no isolation benefit at this scale); a single process
running background jobs in threads (rejected: SLA/outbox must survive API restarts and scale independently).

## Consequences
- No correctness-relevant per-process state: conversations/checkpoints/cases in PostgreSQL, rate limits/idempotency/
  traces/SSE fan-out in Redis. Circuit-breaker state is per process by design (load shedding, not correctness).
- Horizontal scaling = more `api` replicas.

## Measured evidence
- `make scale-test` (`tests/e2e/test_scale.py`): 2 replicas behind nginx served one conversation alternately with
  sequential turn numbers, cross-replica debug traces and a shared rate limit (passed locally and in CI).
- Runtime image 833 MB on disk (≈190 MB compressed content), uid 10001, no models inside.
