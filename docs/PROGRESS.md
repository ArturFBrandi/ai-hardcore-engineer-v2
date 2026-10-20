# Progress Ledger

A new session must be able to continue from this file alone. Read `docs/initial-prompt.md` (spec) and
`docs/IMPLEMENTATION_PLAN.md` first.

## Environment facts
- Host: Ubuntu 24.04 on WSL2, repo on `/mnt/e` (Windows drive: file modes do not persist → mark scripts
  executable with `git update-index --chmod=+x`; LF enforced via `.gitattributes`).
- Never run `scripts/bootstrap-ubuntu.sh` on the host; test it in an `ubuntu:26.04` container / CI.
- `.env` holds `MINIMAX_API_KEY` and `WEB_SEARCH_API_KEY` (Tavily) — never print/overwrite them.
- Tools on host: docker 29 + compose v5, uv 0.12, gh (authenticated), python 3.12. No jq/trivy/gitleaks/syft
  on host → run them via pinned Docker images.

## Verified provider facts (2026-10-19, live probe)
- MiniMax-M3 at `https://api.minimax.io/v1/chat/completions`: plain completion OK; `tools`/`tool_calls` OK;
  `response_format={"type":"json_object"}` OK; `json_schema` silently ignored; thinking on by default and
  emitted as `<think>…</think>` in `content`; `extra_body={"thinking":{"type":"disabled"}}` disables it.
  `usage` returns prompt/completion tokens.
- Tavily `POST https://api.tavily.com/search` with Bearer key: 200, returns `answer` + `results`.
- LangGraph 1.2: new input on an interrupted thread drops the pending interrupt; `Command(resume=…)` with the
  stored interrupt `checkpoint_id` resumes the same thread (design: store checkpoint id on the case).
- Getnet BR (`site.getnet.com.br`) is server-rendered WordPress; `page-sitemap.xml` is broken (WP error) →
  link discovery from seeds; FAQ hub `/duvidas/*` static; PDF manuals under `/boas-vindas/manual-maquinas/`.

## Phases
| Phase | Status | Verified by |
|---|---|---|
| 0 Plan | done | `docs/IMPLEMENTATION_PLAN.md` written |
| 1 Skeleton + config + CI | done | `make lint`, `make typecheck`, `make test-unit` → 10 passed; CI run 39300059432 green |
| 2 DB schema / migrations / seed | done | `alembic upgrade head` → `downgrade base` → `upgrade head`; `alembic check` = no drift; `python -m app.db.seed` twice (idempotent: 2 customers, 3 terminals, 122 txns, 68 settlements) |
| 3 Tools + policy | done | `uv run pytest tests -q` → 31 passed (21 tool integration tests on real Postgres incl. isolation, approval gate, idempotent case + outbox + audit, append-only audit) |
| 4 LLM provider | done | `uv run --env-file .env python -m scripts.llm_test` → PASS plain/structured/tool round-trip (MiniMax-M3, 4 calls, 1291 tokens, $0.00047); 19 provider unit tests (retry/no-retry, breaker, budget, malformed JSON repair) |
| 5 Ingestion + retrieval | done | crawl: 150 pages (146 HTML + 4 PDF), 925 chunks; `python -m evaluation.benchmark_retrieval` (4 dense × 3 rerankers, results/retrieval_benchmark.md); `python -m evaluation.rerank_budget` (top-8 + 700 chars = same Recall@5, ~2.5x faster); `python -m evaluation.calibrate` → RAG_RERANK_MIN_SCORE=-0.8 (F1 0.906) |
| 6/7 Agents + graph + supervisor | done | `pytest tests/orchestration` 9 passed (parallel superstep by timestamps, supervisor dependency w/ product filter, web outage, insufficient evidence, fallback, budget) |
| 8 Memory | done | `pytest tests/multiturn` 11 passed; E2E restart-between-turns passed |
| 9 Auth/protection | done | `pytest tests/security` 34 passed |
| 10 HITL + worker | done | `pytest tests/hitl` 10 passed |
| 11 Guardrails | done | unit + security suites |
| 12 Observability | done | OTEL_ENABLED=true stack + `--profile observability`: Prometheus targets up, Jaeger traces with agent/tool/rag/llm spans; screenshots in `docs/observability/` (Grafana dashboard, Workflow 1 + 2 traces, alerts) |
| 13 Evaluation | done | `python -m evaluation.run_eval` → 126 single-turn, 17 multi-turn, 7 HITL, 6 supervisor; pass 0.976, all strict gates pass; baseline written. Live eval pending |
| 14 Docker | done | image 833 MB (non-root uid 10001, no models baked); `docker compose up -d --wait` healthy; E2E `pytest tests/e2e -m "stack and not scale"` 9 passed; scale test (2 replicas + nginx) passed; bootstrap tested in ubuntu:26.04 twice |
| Live provider | done | `make test-live` 5 passed (router structured output, tool round-trip, auth no-retry, grounded generation, Tavily) |
| Security tooling | done | `./scripts/security_scan.sh` passed (pip-audit 0 vulns, Bandit Medium+ 0, gitleaks history clean, Trivy 0 fixable HIGH/CRITICAL) |
| 15 Docs | done | README (curl examples executed 2026-10-19: jwt 200/200/403/404, demo 200), ARCHITECTURE, THREAT_MODEL, SLO, TESTING_GUIDE, VIDEO_SCRIPT, SCORECARD, ADRs 001–013; `pytest tests/unit/test_dev_interface.py` (Makefile/README targets) passed |
| 16 Live-eval fixes | done | live eval 1 (commit 7cd9132): HITL 0.71 (replacement not proposed by MiniMax), EN follow-up query in English → no evidence, "ajuda" → human. Fixed: deterministic replacement proposal on explicit request, vague-message clarify rule, PT query safeguard, supervisor rationale clipping. Offline 159 passed; eval strict PASS; CI 39359865008 green |
| 17 Adversarial QA + final validation | done | Fixed and covered by regression tests: NUL byte → 500 (`test_control_characters_are_stripped_not_a_500`), unpaginated operator queue (`test_operator_queue_is_paginated_and_priority_ordered`), English follow-up query + broadened retry (`test_broadened_troubleshooting_retry_uses_the_topic_query`), supervisor LLM steps capped (`test_llm_supervisor_proposals_are_capped_per_turn`; troubleshooting p95 116 s → 38 s live), web citations for "insufficient" answers, operator-reply numbers in grounding (`test_next_turn_can_repeat_numbers_from_the_operator_reply`), flaky parallel test (models warmed in fixture), Makefile missing in test image, demo-conversation on approval cases, proxy healthcheck; ADR 014 (scale command). |
| Final validation (2026-10-20) | done | From `docker compose down -v`: build --no-cache, up, migrate, seed, ingest-snapshot, smoke, `make test` (pass), `make test-docker` 166 passed, `make eval` strict PASS, `make verify` PASSED, `make ingest` (150 pages, 147 docs, 925 chunks), demo-scenarios 20/20 answered, demo-conversation OK, `make scale-test` 1 passed, `API_PORT=8000-8001 docker compose --profile proxy up -d --scale api=2` healthy, `make llm-test` PASS, `make test-live` 5 passed, `make eval-live` strict PASS (commit e28b2b2). Manual HTTP: jwt 33/33, demo 32/32; outages MiniMax/web/Qdrant/PostgreSQL/Redis graceful. `make coverage` 90.89%, 191 passed. |
| Post-release fix (2026-10-20) | done | Manual UI test: operator reply "Bom dia, em que posso ajudar?" reached the customer as commentary ("não foi possível identificar um pedido específico nas notas do operador"). Fixed `human_reply` prompt (the operator text IS the message) + deterministic guard: meta-commentary → operator's text verbatim. Regression test `test_polished_reply_that_comments_on_the_operator_message_is_not_sent` (fails with guard disabled). `make test` 192 passed; live MiniMax: greeting, numbers and a question to the merchant all delivered correctly. |

## Open issues
- Live intent confusion customer_settlement vs customer_transaction on "my sales yesterday" phrasings (2–3/126 per run); routing unaffected.
- Golden/judge labels are drafts pending human confirmation (documented).
- Video link placeholder in README (to be recorded by the author).
- Get Ajuda hubs are JS-rendered: article URLs discovered via Playwright render and listed in `sources.yaml` `discovered_seeds`.
- Host port 5432 is used by another local project → compose publishes datastores on 127.0.0.1:55432/56333/56379.
- `make eval-live` must run alone at concurrency 2: MiniMax returns HTTP 429 at 4 and the breaker opens.

## Next step
- Done. Maintenance only: keep CI green; re-run `make eval-live` after prompt/model changes and update README §17/§21.
