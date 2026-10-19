# ADR 010 — Supervisor with bounded replanning

**Status:** accepted · 2026-10-19

## Context
Static fan-out cannot express "the terminal model found by Customer Support decides what Knowledge retrieves"; a free
agent swarm cannot be bounded, audited or tested deterministically.

## Decision
`app/orchestration/supervisor.py` runs after every superstep: schedule plan steps whose dependencies are satisfied
(enriched with upstream findings), then deterministic triggers (decline code → knowledge; empty RAG under a strict
filter → one broadened retrieval; transient tool failure → one retry; contradiction → escalate); only if nothing fired
and results are ambiguous, MiniMax proposes a step that code validates. Limits: `SUPERVISOR_MAX_STEPS` (3), 3 visits per
agent, no repeated fingerprint, token budget, recursion limit 40. A failed node marks its step completed.

## Alternatives considered (cost per workflow measured in the live evaluation, see `evaluation/results/live-latest.md`)
- *Static fan-out + synthesis*: one fewer superstep, but Workflow 2 would retrieve without the model filter.
- *Free-form swarm*: unbounded calls; rejected without implementation (no way to guarantee termination or audit).

## Measured evidence
- Offline eval supervisor suite 6/6 (first superstep, reasons, product filter, budget, termination).
- Property test: 150 random outcome sequences, always terminates within the budget.
- Regression found by CI (2026-10-19): a crashing node left its step pending → rescheduled until the recursion limit;
  fixed and covered by `test_failed_specialist_node_cannot_loop_the_supervisor`.
