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

## Open issues
- Host port 5432 is used by another local project → compose publishes datastores on 127.0.0.1:55432/56333/56379.

## Next step
- Phase 4: LLM provider (MiniMax + Fake + reliability), `scripts/llm_test.py`, `make llm-test`.
