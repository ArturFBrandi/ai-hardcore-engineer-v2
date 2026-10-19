# ADR 007 — Conversation memory in PostgreSQL + LangGraph AsyncPostgresSaver (psycopg v3)

**Status:** accepted · 2026-10-19

## Context
Turn 2 must know turn 1 across separate HTTP requests, restarts and replicas; memory must be customer-scoped, redacted,
erasable (LGPD) and safe against injection; HITL interrupts must survive restarts.

## Decision
- Source of truth: Alembic tables `conversations` (slots, summary, status, TTL) and `conversation_turns` (redacted
  content, roles `customer|assistant|human_operator|system_note`), indexed `(customer_id, updated_at)`.
- Graph checkpoints: `langgraph-checkpoint-postgres` `AsyncPostgresSaver` on the same database. It requires **psycopg v3**
  (the app uses asyncpg for SQLAlchemy) → a dedicated `psycopg_pool.AsyncConnectionPool`; `setup()` runs idempotently in
  the `migrate` job (`app/db/migrate.py`).
- Thread id `"{customer_id}:{conversation_id}"` built only by server code; ownership checked before the graph runs; a
  foreign or unknown id → the same 404.
- Layers: window (`MEMORY_WINDOW_TURNS`), rolling MiniMax summary over `MEMORY_TOKEN_BUDGET` (fallback truncation),
  deterministic slots from tool results; topic-specific slots expire on intent-family change.
- Concurrency: PostgreSQL advisory lock per conversation (`pg_try_advisory_lock` with a deadline → 409).

## Alternatives considered
Checkpoint-only memory (rejected: erasure, listing and redaction are awkward; interrupts drop on new input); Redis memory (not durable enough).

## Measured evidence
`tests/multiturn` (11 passed: coreference, carry-over + escalation on the 3rd failure, language persistence, case
awareness, topic switch, injection isolation, split injection, summary trigger, erasure incl. checkpoints, TTL purge,
3 concurrent turns → turns 2,3,4); `tests/e2e/test_stack.py::test_context_survives_api_restart`; scale test.
