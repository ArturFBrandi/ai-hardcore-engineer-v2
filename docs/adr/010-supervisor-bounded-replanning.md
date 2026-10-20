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

## Alternatives considered
- *Static fan-out + synthesis*: measured below — faster and slightly cheaper, but troubleshooting retrieval never uses
  the terminal-model filter and one of four answers escalated for lack of evidence.
- *Free-form swarm*: unbounded calls; rejected without implementation (no way to guarantee termination or audit).

## Measured evidence
- Offline eval supervisor suite 6/6 (first superstep, reasons, product filter, budget, termination).
- Property test: 150 random outcome sequences, always terminates within the budget.
- Regression found by CI (2026-10-19): a crashing node left its step pending → rescheduled until the recursion limit;
  fixed and covered by `test_failed_specialist_node_cannot_loop_the_supervisor`.

## Measured comparison with MiniMax-M3 (2026-10-20, `python -m evaluation.orchestration_compare --live`)
Six questions (3 troubleshooting, 2 settlement, 1 decline), same stack, run alone (`evaluation/results/orchestration_compare.md`):

| mode | mean latency | mean LLM calls | mean tokens | mean USD | troubleshooting with model filter |
|---|---:|---:|---:|---:|---:|
| supervisor (LLM steps ≤ 3) | 31.3 s | 7.5 | 17,978 | $0.0067 | 3/3 |
| static fan-out | 14.5 s | 5.7 | 16,445 | $0.0061 | 0/4 |

Each LLM-proposed Knowledge round added ~10–15 s, and three of them pushed a decline question to 59 s; the live
evaluation's troubleshooting p95 was 116 s against a 120 s request timeout. **Decision:** keep the supervisor
(dependency + model filter is the point of Workflow 2) but cap LLM-proposed steps at
`SUPERVISOR_MAX_LLM_STEPS=1` per turn; deterministic triggers keep `SUPERVISOR_MAX_STEPS=3`. Covered by
`test_llm_supervisor_proposals_are_capped_per_turn` (fails with the cap raised). Post-cap latency: see the README
performance table (final live evaluation).
