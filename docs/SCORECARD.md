# Scorecard — evidence per rubric criterion

Every claim points to a file, a test, a command or a measured result. Numbers come from runs on 2026-10-19 unless stated.

## Business & Challenge Fit (10%)
- All 10 challenge scenarios × PT/EN are evaluation cases (`evaluation/dataset.json`, ids `challenge-*`) and run in
  `make demo-scenarios` (table printed by `scripts/demo.py`). Offline: 20/20 routed correctly; live: see `evaluation/results/live-latest.md`.
- Multi-turn experience: `tests/multiturn/test_memory.py` (11 tests), 17 scripted conversations in `evaluation/multiturn.json` (offline pass rate 1.0).
- Demo surfaces: customer chat `/ui` (agents, route source, confidence, case status, citations, live SSE human replies),
  operator console `/operator`, `make operator`, `make demo-conversation`.

## Agentic Reasoning & Orchestration (15%)
- Real graph: `app/orchestration/graph.py` (14 nodes, conditional fan-out, bounded loop, interrupt/resume).
- Parallel fan-out: `test_workflow1_settlement_specialists_run_in_parallel` (overlapping wall-clock intervals);
  `docs/observability/jaeger-trace-workflow1-parallel.png`.
- Supervisor reacting to results with a data dependency: `test_workflow2_supervisor_dependency_with_product_filter`;
  `docs/observability/jaeger-trace-workflow2-dependency.png`; triggers (decline code, broadened retry, transient retry,
  contradiction) in `app/orchestration/supervisor.py`; eval supervisor suite 6/6.
- Bounded replanning: step budget, visit cap, fingerprints, recursion limit; property test (150 random sequences);
  `test_supervisor_step_budget_enforced`; regression `test_failed_specialist_node_cannot_loop_the_supervisor`.
- ADR 010 (supervisor vs static fan-out vs swarm).

## Context, Knowledge & RAG (10%)
- Hybrid dense + BM25 with RRF (ADR 003); reranker chosen by measurement (ADR 006, `evaluation/results/retrieval_benchmark.md`,
  `rerank_budget.md`); calibrated threshold (`calibration.md`, F1 0.906).
- JS-page coverage: render profile + 74 discovered Get Ajuda articles (ADR 013); PDF manuals (4).
- Citations with provenance (`title, url, category, retrieved_at`), faithfulness = 1.0 (offline eval; cited URLs ⊆ evidence given).
- Untrusted evidence: `test_injection_planted_in_retrieved_evidence_is_data`; numeric grounding `test_hallucinated_numbers_are_not_delivered`.
- Retrieval metrics on the deployed config: Recall@5 0.909, MRR@10 0.851 (`evaluation/results/latest.md`).

## Tool & Action Engineering (10%)
- 7 typed read tools, idempotent `create_support_case`, approval-gated `request_terminal_replacement`
  (`app/tools/`), policy table + least-privilege matrix (`app/orchestration/policy.py`).
- `tests/integration/test_tools.py` (21: isolation, invalid ranges, DB failure, malformed JSON, approval gate, idempotency + outbox + audit).
- Human-approved writes: `tests/hitl/test_hitl.py::test_approval_*` (approve executes with owner context; reject explained; edit to a foreign terminal fails).

## Security & Identity (13%)
- JWT/JWKS, scopes, body/sub mismatch 403, demo mode refused in production (ADR 009, `app/api/auth.py`, `app/config.py`).
- Redis sliding-window rate limits shared across replicas (`app/api/protection.py`; `test_rate_limit_per_subject`; scale test).
- `tests/security/test_security.py` (34), route-auth enumeration, `docs/THREAT_MODEL.md` (STRIDE + OWASP LLM 2025 mapping).
- `make security` clean (pip-audit 0, Bandit Medium+ 0, gitleaks clean, Trivy 0 fixable HIGH/CRITICAL); CI `security` job.

## Memory, State & Resilience (8%)
- Durable memory + checkpoints in PostgreSQL (ADR 007); restart between turns (`test_context_survives_api_restart`);
  2 replicas (`tests/e2e/test_scale.py`); idempotency (`test_idempotency_key_replay_from_another_subject_is_not_a_replay`);
  concurrency (`test_concurrent_turns_on_same_conversation_are_serialized`); erasure + TTL purge tests.

## Human Oversight & Risk Management (7%)
- Interrupt/resume where the human answer returns into the workflow (ADR 008); `tests/hitl` (10) incl. SSE delivery,
  restart before resume, duplicate submit; deterministic priority/SLA (`app/orchestration/decision.py`), SLA worker test
  with injected clock; append-only audit (DB trigger, `test_audit_log_is_append_only`); eval HITL 7/7.

## Evaluation & Quality Engineering (14%)
- One-command tests: `make test` (only uv + docker), `make verify`, `make test-docker`.
- 126 single-turn cases, 17 multi-turn, 7 HITL, 6 supervisor, 68 golden items with atomic facts + traps and a labeling protocol.
- Deterministic metrics (`evaluation/metrics.py`), thresholds + baseline deltas + `--strict` in CI (`suites` job).
- Judge calibration (`evaluation/results/judge_calibration.md`), live eval (`make eval-live`, scheduled `live.yml`).

## Observability, Reliability & Cost (7%)
- Correlated logs/traces/metrics; dashboards, alert rules and screenshots in `docs/observability/`; `docs/SLO.md`.
- Cost per request in responses, traces and `agent_request_cost_usd`; budgets enforced (`test_token_budget_enforced_before_call`).
- Timeouts/retries/breakers/degradation: provider unit tests, `test_llm_outage_degrades_to_deterministic_paths`, E2E outages (Qdrant, Redis, PostgreSQL).

## Production Engineering (6%)
- CI: `.github/workflows/ci.yml` (lint+mypy strict, unit, suites+coverage 89.7%+eval, image+Trivy+SBOM+E2E+scale,
  security, bootstrap on ubuntu:26.04, snapshot artifact) — green; `live.yml` skips cleanly without secrets.
- Alembic migrations (`alembic check` = no drift), seeding separate; non-root image pinned by digest, 833 MB, models in a
  checksum-verified volume (ADR 012); no correctness-relevant per-process state (ADR 002); horizontal-scaling test.

## Engineering Judgment (8%)
- 13 ADRs with measured evidence (`docs/adr/`), deterministic code where better (dates, slots, priority, decision model,
  coverage guard, settlement policy query), honest limitations (README §22), measured trade-offs (e5-large vs MiniLM, rerank budget).
