# ADR 011 — Redis for shared request protection, traces and SSE fan-out

**Status:** accepted · 2026-10-19

## Decision
Redis 8.2 (AOF) holds only ephemeral, cross-replica state: sliding-window rate limits (Lua, per IP pre-auth, per subject,
stricter operator writes, `Retry-After`), `Idempotency-Key` records bound to the subject (24 h), sanitized debug traces
(24 h TTL) and pub/sub for SSE. Conversation memory is **not** in Redis.

## Failure policy (tested in `tests/e2e/test_stack.py::test_redis_outage_policy`)
Customer rate limiting fails open (metric `rate_limit_backend_errors_total{policy="open"}`), operator writes fail
closed, requests with `Idempotency-Key` fail closed (503), readiness reports 503, SSE degrades, traces are skipped.
