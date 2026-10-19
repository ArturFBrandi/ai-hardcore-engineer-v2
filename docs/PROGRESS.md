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
| 12 Observability | partial | metrics/traces/logs implemented; compose profile + dashboard + alerts written; screenshots pending |
| 13 Evaluation | done | `python -m evaluation.run_eval` → 126 single-turn, 17 multi-turn, 7 HITL, 6 supervisor; pass 0.976, all strict gates pass; baseline written. Live eval pending |
| 14 Docker | done | image 833 MB (non-root uid 10001, no models baked); `docker compose up -d --wait` healthy; E2E `pytest tests/e2e -m "stack and not scale"` 9 passed; scale test (2 replicas + nginx) passed; bootstrap tested in ubuntu:26.04 twice |
| Live provider | done | `make test-live` 5 passed (router structured output, tool round-trip, auth no-retry, grounded generation, Tavily) |
| Security tooling | done | `./scripts/security_scan.sh` passed (pip-audit 0 vulns, Bandit Medium+ 0, gitleaks history clean, Trivy 0 fixable HIGH/CRITICAL) |
| 15 Docs | pending | README, ARCHITECTURE, THREAT_MODEL, SLO, TESTING_GUIDE, VIDEO_SCRIPT, SCORECARD, ADRs |

## Open issues
- CPU reranking costs ~3 s p50 for 15 candidates (benchmark); evaluate reranking top-8 only.
- Get Ajuda hubs are JS-rendered: article URLs discovered via Playwright render and listed in `sources.yaml` `discovered_seeds`.
- Host port 5432 is used by another local project → compose publishes datastores on 127.0.0.1:55432/56333/56379.

## Next step
- Confirm CI green for the full workflow; eval-live; judge calibration; observability screenshots; docs; final validation.
