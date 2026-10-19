# Mission

You are acting as a **Senior/Staff AI Engineer, Backend Engineer, SRE, Security Engineer, QA Engineer and Technical Architect**.

Your job is to **plan, design, implement, test, debug and complete — from an empty repository — a production-quality technical challenge submission** for an AI engineering hiring process.

This is NOT a documentation-only exercise. This is NOT a prototype. This is NOT a UI mockup.

You MUST deliver a **fully functional, executable multi-agent AI support system** that can be cloned onto a freshly installed **Ubuntu 26.04** server, started under Docker, and verified end-to-end by an evaluator with **one command**.

Do not stop after:

* creating architecture documents;
* generating scaffolding;
* creating interfaces without implementations;
* creating mocked agents or fake RAG functions;
* writing tests without running them;
* creating Docker files without building them;
* implementing components without integrating them;
* describing what should be implemented;
* leaving TODO placeholders for core functionality.

You are responsible for actually completing the implementation and validating that it works.

---

# Primary Objective

Build a complete implementation of the challenge **AI Hardcore Engineer — Multi-Agent Support System** (Getnet). If the official statement is available in the repository (e.g., `docs/AI_Hardcore_Engineer-Multi-Agent_Support_System-Tech-Challenge.md`), read it first; this prompt expands it and never contradicts it.

Mandatory agents:

1. **Router Agent**
2. **Knowledge Agent** (RAG + web search)
3. **Customer Support Agent** (at least 2 tools — implement more)

Also implement:

4. **Human Escalation Agent** with a complete human-in-the-loop cycle
5. **Supervisor** that observes specialist results and decides the next step (bounded)
6. **Response Synthesizer**

The application must:

* expose a working HTTP API (`POST` with `{"message": ..., "user_id": ...}`);
* run entirely through Docker;
* deploy on a freshly installed Ubuntu 26.04 server;
* use **MiniMax-M3 from minimax.io as the reasoning LLM**;
* implement functional, hybrid RAG over real Getnet public content;
* implement real agent orchestration with a supervisor and bounded replanning;
* implement functional Customer Support tools over a synthetic customer backend;
* support external web search for current/general information;
* keep **durable, customer-scoped conversational memory** across requests;
* implement a **complete human-in-the-loop loop** (Agent → Human → human response → Agent continues);
* use **real token-based authentication** while staying compatible with the challenge payload;
* include guardrails, observability, cost tracking, an AI evaluation framework with a human-labeled factuality benchmark, comprehensive automated tests and CI;
* provide clear documentation and scripts to install/run from a fresh Ubuntu host.

The result will be submitted as a GitHub repository to evaluators. Treat quality accordingly.

## Challenge statement (self-contained summary — every item is mandatory)

So this prompt stands alone even if the statement file is absent:

* Architect and implement an Agent Orchestration of **at least three distinct agent types** with a clear communication mechanism between them (direct calls, internal queue or event-driven — here: a LangGraph state machine).
* **Router Agent** — primary entry point; analyzes each message and decides which specialized agent (or sequence of agents) handles it, managing workflow and data flow between agents.
* **Knowledge Agent** — retrieval (internal/external) + generation about Getnet products and services grounded in the company website (`https://www.getnet.net/`, suggested source `https://www.getnet.net/en`; "please look for other sources"), using **RAG plus a web search tool** for general-purpose questions.
* **Customer Support Agent** — retrieves relevant user data to resolve inquiries; **must create at least 2 tools**.
* **API** — HTTP endpoint (e.g., FastAPI) accepting `POST` with payload `{"message": "Your query or statement here", "user_id": "some_user_identifier"}` and returning meaningful JSON.
* **Dockerization** — Dockerfile (and docker-compose.yml) so it builds and runs with standard Docker commands.
* **Testing** — describe the overall testing strategy and how comprehensive integration testing of the orchestration is approached (and implement it).
* **Language & frameworks** — free choice (Python here), suitable libraries for API, agents and RAG.
* **What evaluators look for** — clean, modular, maintainable code; a well-explained multi-agent design; high-quality prompts; a functional RAG pipeline that ingests the specified URLs; solid problem solving; effective tests; easy Docker setup; a comprehensive README; thoughtful AI evaluation, observability, reliability, guardrails and production monitoring.
* **README must cover** — how to build, configure, run and test; orchestration architecture, design choices, message workflow, RAG pipeline (ingestion → storage → retrieval → generation), and how LLM tools were leveraged.
* **Submission** — a GitHub repository with a comprehensive README.md and a **video** walking evaluators through reasoning, architecture decisions and how the orchestration works.
* **Bonus (implement all)** — a fourth custom agent (Human Escalation / Teams agent requesting human assistance); guardrails for unsafe, irrelevant, sensitive or unsupported requests; redirect/human handoff when the system cannot confidently resolve; an evaluation and observability strategy (dashboards, traces, structured logs, automated evaluation datasets, regression tests, alerting).
* **Example test scenarios** — the 10 messages listed under "Required challenge scenarios" below, all with `user_id: "cliente1988"`.

---

# How the submission will be scored

Evaluators use this weighted rubric. Design so every criterion can be honestly scored **10/10**, with evidence an evaluator can open, run or measure.

| Criterion | Weight | What a 10 requires |
|---|---:|---|
| Business & Challenge Fit | 10% | All challenge scenarios work; multi-turn experience; polished end-to-end demo surface |
| Agentic Reasoning & Orchestration | 15% | Real graph; parallel fan-out for independent work; a supervisor that observes results and replans within a budget; true inter-agent data dependencies |
| Context, Knowledge & RAG | 10% | Hybrid dense+sparse retrieval, multilingual reranking chosen by measurement, calibrated thresholds, JS-page coverage, citations, provenance, untrusted-evidence handling |
| Tool & Action Engineering | 10% | Typed, scoped, least-privilege tools; read vs. write separation; human-approved write actions |
| Security & Identity | 13% | Identity from verified tokens, deterministic authorization, shared (multi-replica) rate limiting, layered guardrails, threat model |
| Memory, State & Resilience | 8% | Durable conversation memory and graph checkpoints; context survives restarts and replicas; idempotency; concurrency safety |
| Human Oversight & Risk Management | 7% | Interrupt/resume HITL where the human answer returns into the workflow; approvals; deterministic priority/SLA; audit |
| Evaluation & Quality Engineering | 14% | Reproducible one-command tests; large deterministic eval set; human-labeled factuality benchmark; multi-turn/HITL suites; regression gates in CI |
| Observability, Reliability & Cost | 7% | Correlated logs/traces/metrics, dashboards and alerts shipped, cost per request, timeouts/retries/breakers/degradation |
| Production Engineering | 6% | Working CI, migrations, non-root pinned images of reasonable size, no correctness-relevant per-process state, horizontal-scaling proof |
| Engineering Judgment | 8% | Every component justified (ADRs with measured evidence), deterministic code where it is better, honest limitations |

Common reasons strong submissions lose points — **avoid all of them**:

* no conversation memory (turn 2 knows nothing about turn 1);
* escalation that only creates a ticket, with no path for the human's answer to come back;
* the request-body `user_id` used as the real identity;
* in-memory rate limiting / trace stores that break with more than one replica;
* static fan-out + synthesis with no supervisor reacting to results;
* evaluators unable to run the tests because of missing local dependencies;
* documentation claiming CI, dashboards or benchmarks that do not exist in the repository;
* security checks that swallow failures (`|| true`);
* a multi-gigabyte image because ML models are baked in.

## The central principle

> **The LLM proposes, deterministic code disposes.**
> LLMs handle semantic interpretation, planning proposals and language generation. Deterministic software controls identity, authorization, memory scope, state, data access, tool boundaries, approvals, validation, reliability and security.

State this principle in the README and show, for routing, planning, memory, escalation, approvals and auth, where the LLM proposes and where code decides.

---

# CRITICAL EXECUTION REQUIREMENT

Do not merely tell me how to build this. **BUILD IT.**

You have permission to create/modify files, install development dependencies, execute shell commands, build Docker images, run Docker Compose, run tests, start services, call local endpoints, crawl public Getnet pages, inspect PostgreSQL/Qdrant/Redis, run evaluations, inspect logs, fix errors and repeat until it works.

Whenever you implement a component: **run it → test it → inspect failures → fix → rerun the relevant tests.** Do not assume generated code works. Verify it.

---

# Phase 0 — Planning

1. Inspect the working directory. If it contains the challenge statement or other material, read it. Otherwise start from an empty repository.
2. Create `docs/IMPLEMENTATION_PLAN.md` containing: requirements interpretation, architecture, components, development phases, dependencies, testing strategy, security strategy, memory strategy, HITL strategy, observability strategy, RAG strategy, anticipated risks, and acceptance criteria mapped to every rubric row above.
3. **Continue immediately into implementation.** Do not stop and ask for approval.

Recommended build order (each phase ends green before the next starts): skeleton + config + CI → DB schema/migrations/seed → tools → LLM provider + `make llm-test` → ingestion + retrieval → agents → graph + supervisor → memory/checkpointer → auth → HITL + worker → guardrails → observability → evaluation → Docker hardening → docs → final validation → adversarial QA.

---

# Technology Direction

Prefer this stack unless you find a compelling, documented reason to change something.

* **Language**: Python 3.12, managed with **uv** and a committed lockfile.
* **API**: FastAPI — async endpoints, Pydantic v2 models, dependency injection, structured exception handling, OpenAPI.
* **Orchestration**: **LangGraph**, as an actual graph/state machine. Do not create Python classes that simply call each other and call it multi-agent. Every agent has explicit responsibilities, allowed tools, inputs, outputs and state transitions.
* **Datastores**: PostgreSQL (synthetic support backend, conversations, checkpoints, cases, outbox, audit), Qdrant (vectors), Redis (shared rate limiting, trace store, idempotency keys, SSE fan-out across replicas). Introduce each store for real needs only and document each use.
* **Migrations**: Alembic. Never `create_all()` as the schema strategy. Seeding separate from migrations. `make migrate` applies them.

## Recommended dependencies (verify current versions; pin via the lockfile)

```text
runtime: fastapi, uvicorn[standard], pydantic, pydantic-settings, sqlalchemy[asyncio], asyncpg, alembic,
         psycopg[binary,pool] (LangGraph checkpointer), langgraph, langgraph-checkpoint-postgres,
         openai (OpenAI-compatible client for MiniMax), httpx, tenacity, qdrant-client, fastembed,
         beautifulsoup4, lxml, pypdf, pyyaml, redis, pyjwt[crypto], cryptography, sse-starlette,
         structlog, prometheus-client, opentelemetry-api, opentelemetry-sdk,
         opentelemetry-exporter-otlp, opentelemetry-instrumentation-fastapi
optional: playwright (render profile only)
dev:     pytest, pytest-asyncio, pytest-timeout, pytest-cov, respx, aiosqlite (fast unit tests only),
         ruff, mypy, types-pyyaml, bandit, pip-audit
```

Do not add LangChain/LlamaIndex layers unless they remove real code; if you use them, justify it in an ADR.

## LLM — MiniMax-M3 (mandatory)

```text
MINIMAX_API_KEY=
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M3
```

* MiniMax provides an OpenAI-compatible API. Implement a provider abstraction:
  ```text
  LLMProvider
      +-- MiniMaxProvider      (production)
      +-- FakeLLMProvider      (deterministic test double; refused when APP_ENV=production)
  ```
* The rest of the application must not depend on MiniMax-specific details.
* Use the OpenAI-compatible API unless validation shows another officially supported MiniMax interface gives materially better tool-calling compatibility. If MiniMax behaves differently than assumed, consult current official MiniMax documentation and adapt the provider — never change the model.
* Do NOT silently switch to OpenAI GPT, Claude, Gemini, Ollama or any other reasoning LLM. Local open models for embeddings and reranking are acceptable (they are not the reasoning LLM) — document them.
* `make llm-test`: a minimal real request confirming authentication, model access, API format and response parsing (plain completion, structured output, tool call). An absent key yields an explicit, helpful error. Never commit credentials.

---

# Configuration

* Environment variables only, loaded by a typed `Settings` class; fail fast on invalid critical configuration; secrets as `SecretStr`; a `safe_dict()` that redacts secrets and DSN passwords is the only sanctioned way to log settings.
* `.env.example` documents **every** variable, grouped and commented. At minimum:

```text
APP_ENV=                          # development | test | production
LOG_LEVEL=
AUTH_MODE=                        # jwt | demo (demo refused in production)
JWT_ISSUER=
JWT_AUDIENCE=
JWT_JWKS_URL=

MINIMAX_API_KEY=
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M3
LLM_PROVIDER=minimax              # minimax | fake (fake refused in production)
LLM_TIMEOUT_SECONDS=
LLM_MAX_RETRIES=
LLM_PRICE_INPUT_PER_MTOK=
LLM_PRICE_OUTPUT_PER_MTOK=
MAX_TOKENS_PER_REQUEST=

DATABASE_URL=
QDRANT_URL=
REDIS_URL=

WEB_SEARCH_PROVIDER=
WEB_SEARCH_API_KEY=

EMBEDDING_MODEL=
SPARSE_MODEL=
RERANKER_MODEL=
RAG_TOP_K=
RAG_RERANK_TOP_K=
RAG_CONTEXT_TOP_K=
RAG_MIN_SCORE=
RAG_RERANK_MIN_SCORE=

ESCALATION_CONFIDENCE_THRESHOLD=
ESCALATION_PENALTIES_FILE=
TROUBLESHOOTING_MAX_ATTEMPTS=
SUPERVISOR_MAX_STEPS=

MEMORY_WINDOW_TURNS=
MEMORY_TOKEN_BUDGET=
CONVERSATION_TTL_DAYS=

HITL_SLA_MINUTES=
OPERATOR_WEBHOOK_URL=

RATE_LIMIT_PER_MINUTE=
ENABLE_DEBUG_ENDPOINTS=
CORS_ALLOW_ORIGINS=

OTEL_ENABLED=
OTEL_EXPORTER_OTLP_ENDPOINT=
```

---

# Architecture

Implement approximately this architecture as a **modular monolith** (single deployable API + a worker using the same image), with clean module boundaries so agents could be separated later. Document that this avoids unnecessary network complexity, distributed transactions, operational overhead and failure modes.

```text
                     Client (customer UI / curl)        Operator (console / CLI)
                               |                                  |
                               v                                  v
                 Auth (JWT) + rate limit (Redis)        Auth (JWT, operator scopes)
                               |                                  |
                               v                                  |
                       Input Guardrails                           |
                               |                                  |
                               v                                  |
                 Load conversation memory (Postgres)              |
                               |                                  |
                               v                                  |
                        Router Agent (rules + LLM)                |
                               |                                  |
                         Plan (typed, validated)                  |
                 +-------------+--------------+                   |
                 v                            v                   |
         Customer Support  ∥             Knowledge                |
          (scoped tools)          (hybrid RAG | web search)       |
                 +-------------+--------------+                   |
                               v                                  |
                           Supervisor ── next step? ──► specialists (bounded)
                               |                                  |
                               v                                  |
                          Synthesizer                             |
                               |                                  |
                    Deterministic decision model                  |
                       +-------+--------+                         |
                       v                v                         |
                    Answer      Escalation Agent ── interrupt() ──┘
                       |                ^      (checkpointed; resume with
                       |                └────── Command(resume=operator decision))
                       v
               Output Guardrails → Save memory → Response / SSE event
```

The architecture must support workflows where **more than one specialist cooperates on a single request**, run independent branches **concurrently**, and support **sequential data dependencies** decided by the supervisor (e.g., the customer's terminal model found by Customer Support drives a model-specific Knowledge retrieval).

---

# Shared Agent State

Define a typed LangGraph state (TypedDict with reducers for parallel-written lists). Include at least:

```python
class AgentState(TypedDict, total=False):
    request_id: str
    trace_id: str
    customer_id: str                 # from the verified principal only
    conversation_id: str
    turn: int
    message: str
    language: str

    history: list[ConversationTurn]  # windowed, redacted, untrusted
    summary: ConversationSummary | None
    slots: ConversationSlots         # structured, deterministic

    intent: str
    route: list[str]
    route_source: str                # rule | llm | fallback
    plan: Plan
    supervisor_steps: int

    retrieved_documents: Annotated[list[RetrievedDocument], operator.add]
    web_results: Annotated[list[WebSearchResult], operator.add]
    tool_results: Annotated[list[ToolResult], operator.add]
    proposed_actions: list[ProposedAction]

    candidate_answer: str
    final_answer: str
    citations: Annotated[list[Citation], operator.add]

    confidence: float
    escalation_reasons: Annotated[list[str], operator.add]
    needs_escalation: bool
    case_id: str | None
    status: str                      # answered | escalated | awaiting_human | human_replied

    token_usage: TokenUsage
    errors: Annotated[list[str], operator.add]
    events: Annotated[list[AgentEvent], operator.add]
```

Improve as needed. Do not store private chain-of-thought. Events contain decisions, inputs/outputs summaries, scores and latencies (with start/end timestamps) suitable for operational debugging.

---

# Agent 1 — Router Agent

Responsibilities: classify intent; decide which specialists are needed; identify whether customer data, Getnet knowledge or current external information is required; detect explicit human requests; resolve follow-ups using conversation slots; produce a structured route and an initial plan. The router must NOT answer the user.

* Structured output validated with Pydantic; invalid output → deterministic fallback (route source `fallback`).
* **Hybrid routing**: deterministic high-confidence rules first (weather, exchange rate, explicit human request…), LLM only when semantic interpretation is needed. Do not build a huge brittle keyword engine. Track `rule | llm | fallback`.
* **The final agent list is derived by code** from the classified intent and flags — the LLM classifies, code disposes.
* Include few-shot examples in the router prompt, mixed PT/EN guidance, and the windowed conversation history (delimited as untrusted).

Example structured output:

```json
{
  "intent": "customer_settlement",
  "agents": ["customer_support", "knowledge"],
  "requires_customer_data": true,
  "requires_getnet_knowledge": true,
  "requires_external_web": false,
  "wants_human": false,
  "language": "en",
  "source": "llm"
}
```

Intent taxonomy at least:

```text
getnet_product_information  getnet_policy        getnet_how_to
technical_support           customer_settlement  customer_transaction
customer_terminal           customer_account     case_status
weather                     exchange_rate        general_information
human_support               unsupported
```

---

# Supervisor and Planning (bounded replanning)

* **Plan**: typed `Plan` = list of `PlanStep{agent, objective, inputs, depends_on}`, proposed by LLM structured output and **validated by code**: allowed agents only, allowed transitions only, `SUPERVISOR_MAX_STEPS` (default 3), no repeated identical step, per-request token budget. Invalid plan → deterministic route.
* **Supervisor node** runs after each superstep and chooses `continue(step) | synthesize | escalate`, combining deterministic triggers with an LLM proposal:
  * Customer Support found the customer's terminal and its model → follow-up Knowledge step with `product_filter=<model>`, `category=terminal_troubleshooting`;
  * declined transaction with a reason code → Knowledge step for that code;
  * RAG empty under a strict filter → one broadened retrieval (query rewrite) before declaring no evidence;
  * transient tool failure → one retry step;
  * contradiction between customer data and policy evidence → escalate with reason `contradictory_information`.
* Loop protection: LangGraph recursion limit, step budget, per-agent visit counter, deterministic termination. Every supervisor decision is a traced event (`decision`, `reason`, `step`).
* Independent steps still run in parallel in the same superstep.
* Write an ADR comparing supervisor-with-bounded-replanning vs. static fan-out vs. free-form agent swarm, with measured latency/token cost per workflow.

---

# Agent 2 — Knowledge Agent

Two explicit modes:

1. **Getnet mode** — RAG is **authoritative**. Never answer Getnet facts (prices, fees, settlement times, contractual rules, product capabilities, troubleshooting procedures, financial conditions) from pretrained knowledge. If evidence is insufficient: say verified information could not be located, or escalate.
2. **External mode** — web search for current/general information (weather, exchange rates). If search is unavailable, say so; never fabricate.

The Knowledge Agent accepts filters/objectives from the plan (product, category, rewritten query) and uses conversation context only for query rewriting.

## Getnet RAG sources

Do not limit ingestion to one page. Start from official domains, including `https://www.getnet.net/`, `https://www.getnet.net/en` and `https://site.getnet.com.br/`, and crawl relevant internal links. Include official PDFs (manuals). Focus on: Get Clássica, Get Smart, Get Mini, Get Renova, Get Tap, card machines and comparisons, Pix, Payment Link, Crediário, receivables advance (antecipação), settlement, public rates, sales management, connectivity, terminal error codes, troubleshooting, FAQs, Get Ajuda, manuals, contact information.

Respect robots.txt, reasonable crawl rates, timeouts, content types and duplicates. Do not crawl the internet indiscriminately.

`ingestion/sources.yaml` holds seed URLs, allowed domains, allow/deny patterns, categories, language metadata, and a per-URL `render: js` flag. Do not hardcode the catalogue in Python.

## Ingestion pipeline

```text
Official URLs → Crawler (static | optional JS render) → HTML/PDF extraction → Noise removal
→ Normalization → Structure-aware chunking → Metadata enrichment → Dense + sparse embeddings → Qdrant
```

* **Extraction**: remove navigation, repeated footers, cookie banners, scripts, styles, unrelated menus; preserve headings, paragraphs, useful tables, FAQ Q/A pairs, numbered procedures, product names and relevant links. Never flatten procedures into fragments.
* **Chunking**: boundaries at H1/H2/H3, paragraph groups, FAQ entries, numbered procedures; keep semantic units together.
* **Metadata**: `url, canonical_url, title, heading, category, product, language, source, retrieved_at, content_hash, etag, last_modified`.
* **Dedup** by content hash; **incremental re-crawl** using ETag/Last-Modified + hash; stale-document metric.
* **JS-heavy pages**: optional headless rendering (Playwright) in a separate optional image/profile so the API image does not grow. Report pages captured with vs. without rendering.
* Commands: `make ingest` (live crawl, really ingests), `make reindex` (safe rebuild), `make snapshot-export` / `make ingest-snapshot` (versioned chunks + metadata + hashes so evaluators can run the demo without a multi-minute crawl; publish as a CI/release artifact or a small committed fixture subset; respect site terms — do not commit large copies of third-party content without need).

## Embeddings, sparse vectors, reranking

* Qdrant via Docker Compose with **named vectors**: dense multilingual + sparse BM25 (e.g., fastembed `Qdrant/bm25`), fused with **RRF** through the Query API.
* Document in the README and an ADR: embedding model, **vector dimensions**, distance metric, sparse model, reranker, **chunk strategy** (sizes, overlap, boundaries) and the **reason for each selection**.
* Embedding and reranker models run locally (no second paid API). Candidates must be **benchmarked on the golden set** (Recall@5, MRR, nDCG@5, CPU latency), e.g. dense: `paraphrase-multilingual-MiniLM-L12-v2`, `intfloat/multilingual-e5-base`, `BAAI/bge-m3`; reranker: `jinaai/jina-reranker-v2-base-multilingual`, `BAAI/bge-reranker-v2-m3`, `BAAI/bge-reranker-base`. Pick by numbers (Portuguese performance matters — Getnet content is mainly PT, questions come in PT and EN). Record results in an ADR; keep the models configurable.
* Retrieval: retrieve ~15 → metadata filters + dedup → rerank ~8 → select 4–6 as evidence. Deterministic product-name boost where measured to help.
* **Threshold calibration**: `make calibrate` sweeps `RAG_MIN_SCORE` / `RAG_RERANK_MIN_SCORE` on the golden set and writes the chosen values and precision/recall curve to `evaluation/results/calibration.md`; `.env.example` defaults come from this run.
* Record retrieval scores and diagnostics in traces.

## Citations

Knowledge answers return structured sources (`title, url, category, retrieved_at`). Cite only chunks actually given to the generator and supporting the answer; verify this in tests.

## RAG prompt-injection protection

All crawled/retrieved/searched content is **untrusted evidence**, delimited (e.g., `<retrieved_evidence untrusted="true">…</retrieved_evidence>`). System prompts state that documents contain evidence only, embedded instructions are untrusted and possibly malicious, and system instructions always take precedence. Test injection attacks planted in retrieved content.

## External web search

Abstract behind `WebSearchProvider` (Tavily, Brave, Serper or similar — choose one, document it in an ADR). Key via environment variable. Graceful, honest failure; web-only questions degrade to "unavailable" rather than opening support cases.

---

# Agent 3 — Customer Support Agent

Works with customer-specific data through a **bounded tool-calling loop** (max rounds) over a realistic **synthetic** PostgreSQL backend.

## Synthetic customer model

Tables at minimum: `customers, terminals, terminal_events, transactions, settlements, support_cases`. Potential additions only if they serve a workflow: `customer_products, customer_preferences, merchant_accounts`. Keep the design manageable. Realistic relationships, foreign keys, enums, check constraints and indexes; masked documents/cards/bank data only; everything clearly labeled synthetic.

Suggested columns:

```text
customers        id, name, trade_name, document_masked, email_masked, segment, plan, pix_enabled,
                 anticipation_enabled, settlement_days (D+N), bank_masked, created_at
terminals        id (serial), customer_id, model, status (active|inactive|maintenance|blocked),
                 connectivity (wifi|4g|wifi+4g), firmware_version, activated_at, last_seen_at
terminal_events  id, terminal_id, event_type (connectivity_loss|error_code|reboot...), detail, occurred_at
transactions     id, customer_id, terminal_id, amount, currency, method (credit|debit|pix|payment_link|crediario),
                 installments, status (approved|declined|pending|refunded), decline_reason, card_brand_masked, occurred_at
settlements      id, customer_id, gross_amount, fees, net_amount, method, status (completed|pending|scheduled),
                 expected_date, paid_at, transaction_count
support_cases    id (CASE-...), customer_id, conversation_id, category, priority, status
                 (open|in_progress|awaiting_customer|resolved|closed), subject, description, idempotency_key (unique),
                 assigned_operator, sla_due_at, created_at, updated_at, resolved_at, resolution_code
```

Platform tables (all via Alembic):

```text
conversations, conversation_turns           (see "Conversation Memory")
langgraph checkpoint tables                 (created by AsyncPostgresSaver.setup())
pending_actions   id, case_id, customer_id, tool, arguments_json, policy, status (proposed|approved|rejected|executed|failed),
                  decided_by, decided_at, result_json
outbox            id, topic, payload_json, status, attempts, next_attempt_at, created_at, delivered_at
audit_log         id, actor_type (customer|agent|operator|system), actor_id, action, entity, entity_id,
                  request_id, before_json, after_json, created_at   (append-only; no UPDATE/DELETE grants)
```

Seed `cliente1988` (challenge examples) and a second customer for isolation tests: at least one Get Smart or Get Clássica terminal (one with recent connectivity-loss events), approved/declined/pending transactions (recent and historical, decline reason codes), completed/pending/scheduled settlements, historical and open support cases. Generate dates **relative to the seeding moment** so "yesterday's sales" always works; seeding is idempotent and repeatable for test fixtures.

## Tools

Read-only (Customer Support): `get_customer_profile, get_customer_terminals, get_terminal_status, get_recent_transactions, get_transaction_details, get_settlement_schedule, get_open_support_cases`.

Write (Escalation only): `create_support_case` — idempotent per `(conversation_id, escalation_reason_hash)` via a unique constraint.

Approval-gated write (proposed by Customer Support, executed only after human approval): at least one meaningful action, e.g. `request_terminal_replacement(terminal_id, reason)` or `request_settlement_review(settlement_id, reason)`.

Do not add tools merely to inflate the count. Every tool is functional.

## Tool contracts

Every tool has typed inputs/outputs, validation, explicit errors, timeouts, logging/tracing and authorization boundaries. Architecture: `LLM agent → typed tool → service/repository → database`. The LLM never issues SQL. Parameterized queries only.

## Authorization boundary (critical)

* Tools receive an immutable `AuthContext` built **only** from the verified principal; the LLM never sees, chooses or changes the identity.
* Repositories are automatically scoped to `AuthContext.customer_id`.
* An explicit **action policy table** (code/config, not prompt) classifies each action as `auto_allowed | requires_approval | forbidden`.
* Tests prove cross-customer data cannot be read via tools, repositories, API, memory or HITL resume.

---

# Agent 4 — Human Escalation Agent and Human-in-the-Loop

## Escalation

Receives unresolved requests; categorizes the issue (LLM suggests, code validates against an enum such as `POS_CONNECTIVITY, POS_HARDWARE, TRANSACTION_ISSUE, SETTLEMENT_ISSUE, ACCOUNT_ISSUE, PRODUCT_QUESTION, GENERAL_REQUEST, SECURITY_CONCERN`); summarizes useful execution context and diagnostics (intent, route, plan steps, signals, tools used/failed, evidence found, errors — never secrets or chain-of-thought); creates the support case via `create_support_case`; returns a customer-friendly handoff message with the case id. A deterministic fallback plan guarantees escalation never fails because the LLM did. If the case cannot be persisted, tell the customer honestly and give official contact channels.

Example result fields:

```json
{
  "escalated": true,
  "case_id": "CASE-20261019-0042",
  "category": "POS_CONNECTIVITY",
  "priority": "high",
  "status": "awaiting_human",
  "escalation_reasons": ["repeated troubleshooting failure (3 attempts)"]
}
```

## Deterministic decision model

Confidence starts at 1.0 and is reduced by concrete system signals — never by the LLM's self-assessment. Signals: no RAG evidence; low retrieval/rerank score; **entity mismatch** (the question names a product/entity absent from all selected evidence); contradictory information; required customer data unavailable; tool failures; web-search failure; LLM failure; guardrail flags; unsupported request; explicit human request; **repeated troubleshooting failure** (from memory slots, at `TROUBLESHOOTING_MAX_ATTEMPTS`); potentially high-risk issue (fraud/scam reports). Penalty weights and threshold are configurable (validated YAML/env). Every decision returns explainable `escalation_reasons`. Suspected attack traffic does not auto-open human cases from low confidence alone (prevents burning operator time), except genuine security concerns reported by the customer.

**Priority and SLA are deterministic** from signals: fraud/security → `urgent`; terminal down with no sales today → `high`; settlement discrepancy → `high`; otherwise `normal`.

## Complete HITL loop (Agent → Human → Agent)

* For cases requiring a human answer or decision, the Escalation Agent calls LangGraph **`interrupt()`** with a typed payload (case id, sanitized context summary, proposed customer message, proposed action). The run is persisted by the checkpointer; the conversation status becomes `awaiting_human`; the customer immediately receives the handoff message.
* The operator's response resumes **the same thread** via `Command(resume=OperatorDecision(...))`, continuing into a `human_reply` node that validates the payload, turns the operator's notes into a customer-facing message with MiniMax-M3 (grounded strictly on operator text + case context) or sends it verbatim when marked `send_as_is`, applies output guardrails, appends it to the conversation, and updates case status and slots.
* The customer receives it via `GET /v1/conversations/{id}` and the SSE stream; on the next turn, agents know the human's answer through memory.
* **Approval gates**: proposed write actions requiring approval interrupt for an operator decision (`approve | reject | edit`); the tool then executes with the **original customer's `AuthContext`**; rejections are explained to the customer. The operator can never act on a different customer than the case owner.
* Resume is idempotent (double-submit safe) and works after any number of restarts.

## Operator surfaces

```text
GET  /v1/operator/cases?status=open&priority=high
GET  /v1/operator/cases/{case_id}            sanitized context, conversation excerpt, signals, proposed action
POST /v1/operator/cases/{case_id}/claim
POST /v1/operator/cases/{case_id}/reply      {message, send_as_is}
POST /v1/operator/cases/{case_id}/decision   {approve|reject|edit, edited_arguments, note}
POST /v1/operator/cases/{case_id}/resolve    {resolution_code, note}
```

* Minimal operator console (static HTML + vanilla JS at `/operator`, no build step) and a CLI (`make operator`), both thin clients over the API.
* **Transactional outbox**: case creation writes an outbox row in the same transaction; a `worker` service delivers notifications to `OPERATOR_WEBHOOK_URL` (Teams/Slack-compatible) with retries; default sink = structured log.
* **SLA timer** in the worker: no operator action within `HITL_SLA_MINUTES` → bump priority, notify again, post a status update to the customer. Use an injectable clock so tests never `sleep`.
* **Append-only audit log**: every escalation, interrupt, operator action, approval, resume and executed write action (actor, timestamp, request id, before/after).
* **Feedback loop**: `make export-feedback` exports operator resolutions and corrected answers as candidate evaluation cases for human review.

---

# Conversation Memory, State and Resilience

## API contract

`POST /v1/chat` accepts the exact challenge payload and an optional conversation id:

```json
{
  "message": "E a venda de anteontem?",
  "user_id": "cliente1988",
  "conversation_id": "conv_8f2c..."
}
```

* Absent `conversation_id` → new conversation. The response includes `conversation_id`, `turn` and `status`.
* Conversations are owned by the authenticated customer; a foreign or nonexistent id returns the **same generic 404** (no existence oracle). Ownership is checked in code before the graph runs.

```text
GET    /v1/conversations                      list my conversations (paginated)
GET    /v1/conversations/{id}                 turns, statuses, case ids, sources
GET    /v1/conversations/{id}/events          SSE stream (agent/human replies, case status)
DELETE /v1/conversations/{id}                 erase: turns, summaries, checkpoints (LGPD)
```

## Persistence

* **LangGraph checkpointer**: `langgraph-checkpoint-postgres` (`AsyncPostgresSaver`) on the same PostgreSQL. It uses `psycopg` v3 (not asyncpg) — add it deliberately, explain it in an ADR, run its `setup()` idempotently as part of migrations/startup. Thread id = `"{customer_id}:{conversation_id}"`, constructed only by server code. Never trust a client-supplied thread id or client-supplied history.
* Alembic tables `conversations` (id, customer_id, status, language, created_at, updated_at, expires_at) and `conversation_turns` (conversation_id, turn, role `customer|assistant|human_operator|system_note`, **redacted** content, intent, agents, case_id, request_id, created_at), indexed by `(customer_id, updated_at)`.
* Content is redacted (cards, CPF/CNPJ, tokens, secrets) before persistence.
* Retention `CONVERSATION_TTL_DAYS` with a scheduled purge in the worker and `make purge-expired`.

## Memory layers

1. **Working memory**: last `MEMORY_WINDOW_TURNS` turns, delimited as `<conversation_history untrusted="true">`, given to Router, Customer Support, Knowledge query rewriting and Synthesizer.
2. **Rolling summary**: when history exceeds `MEMORY_TOKEN_BUDGET` (deterministic token count), MiniMax-M3 produces a structured summary (topics, open issues, entities, pending actions); fallback = truncation.
3. **Structured slots**, extracted deterministically from tool results (not LLM prose): `active_terminal_id, active_terminal_model, last_transaction_ids, last_date_range, open_case_id, preferred_language, troubleshooting_attempts[category]`. Slots expire or are replaced deterministically on intent change.
4. Customer-level preferences only if they add measurable value. Never cross-customer.

## Behaviours that must work across separate HTTP requests

* Coreference: "Quais foram minhas vendas de ontem?" → "E a de anteontem?".
* Entity carry-over: "Minha Get Smart não conecta" → "Já reiniciei e continua igual" → no repeated steps, attempts incremented, escalation on the Nth failure.
* Language persistence for short follow-ups ("ok, and Pix?").
* Case awareness: "Qual o status do meu chamado?" resolves `open_case_id`.
* Topic switch does not pollute answers with stale slots.

## Memory security

History and summaries are untrusted: an injection in turn 1 must not change behaviour in turn 3; summaries must not convert user instructions into "facts"; threads of other customers are unreachable; erasure removes everything. Test all of it.

## Resilience

* **Idempotency-Key** header on `POST /v1/chat` (Redis, 24h TTL, bound to the subject): retries return the stored response and never run the graph twice or duplicate a case.
* Restart the `api` container between turns → context preserved (automated E2E).
* `docker compose up --scale api=2` behind a reverse proxy → alternating replicas keep context, rate limits and traces (automated test).
* Concurrent requests on the same conversation are serialized (advisory/row lock) — tested.
* Graceful shutdown drains in-flight runs; interrupted runs resume from checkpoints.

---

# Response Synthesizer

Combines customer tool results, Getnet evidence, web results, memory context and human replies into one coherent answer in the customer's language; attaches citations; avoids exposing internal payloads; preserves factual grounding; never reveals chain-of-thought.

---

# Required Example Workflows

**Workflow 1 — settlement (parallel cooperation)**: "When will the money from yesterday's sales be deposited?" Uses actual seeded data; no generic mock answers.

```text
Router
  ├──> Customer Support ──> get_recent_transactions(yesterday), get_settlement_schedule
  └──> Knowledge ─────────> Getnet settlement policy (RAG)
            │ (same superstep, parallel)
            v
        Supervisor ──> Synthesizer ──> answer with amounts, dates and citations
```

**Workflow 2 — troubleshooting (supervisor-driven dependency)**: "My card machine won't connect to the internet, what should I do?" The dependency must be visible in traces and the answer cites the model-specific source.

```text
Router
  └──> Customer Support ──> get_customer_terminals ──> get_terminal_status (connectivity_loss events)
            v
        Supervisor: terminal model = Get Smart
            v
        Knowledge (product_filter="Get Smart", category=terminal_troubleshooting)
            v
        Synthesizer ──> diagnosis + official steps + citations
```

**Workflow 3 — external information**: "What's the euro exchange rate today?" Never answered from LLM memory.

```text
Router (rule: exchange_rate) ──> Knowledge (web mode) ──> Web Search ──> Synthesizer
                                                   └─ search down ──> honest "unavailable" (no escalation)
```

**Workflow 4 — multi-turn**: troubleshooting follow-up after a failed attempt → memory slots → repeated-failure escalation → case + interrupt.

**Workflow 5 — HITL**: escalation → operator reply in the console → resume → customer sees the reply via SSE → next turn uses it.

**Workflow 6 — approval**: Customer Support proposes `request_terminal_replacement` → policy requires approval → operator approves → tool executes → customer informed (and a rejected variant).

## Required challenge scenarios

All must work (PT and EN variants) and be automated evaluation cases:

```text
What's the difference between the Get Clássica and the Get Smart?
What's the weather forecast in Porto Alegre tomorrow?
When will the money from yesterday's sales be deposited?
Do I need a bank account to receive my sales via Pix?
My card machine won't connect to the internet, what should I do?
How does receivables advance (antecipação) work with Getnet?
What's the euro exchange rate today?
My card machine is showing a transaction decline error.
How many installments can I split a sale into with the crediário?
Can I sell through WhatsApp using the Payment Link?
```

For each: expected intent, route/plan steps, required tools, knowledge source type, escalation behaviour. Never assert exact natural-language wording.

---

# API

`POST /v1/chat` returns e.g.:

```json
{
  "request_id": "req_...",
  "trace_id": "4bf92f35...",
  "conversation_id": "conv_...",
  "turn": 2,
  "status": "answered",
  "answer": "...",
  "intent": "customer_settlement",
  "agents": ["router", "customer_support", "knowledge", "supervisor", "synthesizer"],
  "route_source": "llm",
  "confidence": 0.92,
  "escalated": false,
  "escalation_reasons": [],
  "case_id": null,
  "sources": [{"title": "...", "url": "...", "category": "...", "retrieved_at": "..."}]
}
```

Also: `GET /health/live` (process alive), `GET /health/ready` (PostgreSQL, Qdrant, Redis — not MiniMax), `GET /metrics` (Prometheus), dev-only `GET /v1/debug/traces/{request_id}` (sanitized steps; force-disabled in production), conversation and operator endpoints above, `/ui` and `/operator` static pages (dev/demo).

* **Uniform error envelope** (`ErrorResponse{error, detail, request_id}`) for every error, including 401/403/404/422/429/503. No stack traces to clients.
* Status codes: 200 success (escalation is still 200), 401/403 auth, 404 not found/foreign, 409 conflicting concurrent turn if not serialized, 422 invalid input, 429 rate limited, 503 dependency unavailable.
* Responses carry `X-Request-ID` and `traceparent`.
* Do not expose hidden reasoning in any response.

Debug trace example (`GET /v1/debug/traces/{request_id}`, development only; sanitized — no secrets, no unnecessary PII, no chain-of-thought):

```json
{
  "request_id": "req_...",
  "trace_id": "...",
  "steps": [
    {"agent": "router", "event": "routed", "detail": {"intent": "customer_terminal", "route": ["customer_support"], "source": "llm"}, "started_at": "...", "latency_ms": 820},
    {"agent": "customer_support", "event": "tool_call", "detail": {"tool": "get_customer_terminals", "ok": true}, "latency_ms": 14},
    {"agent": "supervisor", "event": "decision", "detail": {"decision": "continue", "step": "knowledge", "reason": "terminal_model_known", "product_filter": "Get Smart"}},
    {"agent": "knowledge", "event": "retrieved", "detail": {"candidates": 15, "reranked": 8, "selected": 5, "top_rerank_score": 4.1}, "latency_ms": 310},
    {"agent": "decision_model", "event": "evaluated", "detail": {"confidence": 0.9, "needs_escalation": false}},
    {"agent": "cost", "event": "tokens", "detail": {"input": 3120, "output": 410, "usd": 0.0021}}
  ]
}
```

---

# Identity, Authentication and Authorization

* **JWT bearer authentication** (RS256/ES256) with JWKS verification of `iss, aud, exp, nbf, sub, scope`, clock-skew tolerance and key rotation support, using a maintained library (e.g., PyJWT + cryptography).
* **Local development identity provider**: `make keys` generates a git-ignored dev keypair; `make token SUB=cliente1988 SCOPE="chat"` mints tokens; the dev JWKS is served/loaded only when `APP_ENV != production`. In production the JWKS URL comes from config (OIDC provider).
* `AUTH_MODE`:
  * `jwt` (default in compose and production): identity = token `sub`. Body `user_id` is optional; if present and different from `sub` → **403**, logged as a security event and counted in metrics. This keeps the challenge payload valid while making it non-authoritative.
  * `demo` (development/evaluation convenience, refused when `APP_ENV=production`): body `user_id` is the identity, exactly as the challenge statement shows. README shows curl examples for both modes.
* Scopes: customer `chat conversations:read conversations:delete`; operator `cases:read cases:write approvals:write`; admin `ingest:run`. Enforced with FastAPI dependencies; a test enumerates every route and asserts it declares an auth dependency (except health/metrics).
* **The LLM never decides authorization.** `AuthContext` flows unchanged from principal → graph → tools → memory → HITL resume.
* **Rate limiting in Redis** (sliding window or GCRA): per subject on `/v1/*`, per IP pre-auth, stricter for operator writes; `Retry-After`; documented fail-open/fail-closed policy with a metric.
* Request body size limit, request timeouts, security headers, CORS deny-by-default.

---

# Guardrails

Implement real, layered guardrails (input, retrieval, tool, output, memory):

* **Prompt injection / system prompt extraction** — detection after normalization (Unicode NFKC, confusables, zero-width characters, simple base64/leet decoding), plus prompt hardening; multi-turn injection detection (history considered).
* **Cross-customer data access** — blocked deterministically regardless of wording.
* **RAG and web instruction injection** — retrieved content is data, never instructions.
* **Sensitive information** — redact credentials, secrets, card data and unnecessary PII in output, logs, traces and memory.
* **Canary token** embedded in system prompts; any output containing it is blocked (robust leak detection).
* **Unsupported financial claims** — no invented fees, settlement times or contractual conditions; numbers in Getnet answers must appear in evidence or tool data.
* **Excessive agency** — least-privilege tool sets per agent, approval gates, bounded loops.

Least-privilege matrix (document the rationale):

```text
Router            routing/planning only, no tools
Supervisor        next-step decisions only, no tools
Knowledge         retrieval, web search
Customer Support  read-only customer tools; may PROPOSE approval-gated actions
Escalation        create_support_case, interrupt
Operator (human)  reply, approve/reject/edit, resolve — only for the case owner's data
```

---

# Reliability Engineering

* **Timeouts** for MiniMax, web search, PostgreSQL, Qdrant, Redis, crawler HTTP, tools and webhooks.
* **Retries** only for retryable errors (429, transient connection failures, selected 5xx) with exponential backoff + jitter; never blindly retry validation/auth failures.
* **Circuit breakers** for MiniMax and web search, with state exported as a metric.
* **Bounded loops**: tool rounds, supervisor steps, recursion limit, token budget per request (`MAX_TOKENS_PER_REQUEST`) enforced by code with graceful degradation.
* **Graceful degradation**: web search down → "I cannot retrieve current external information right now"; RAG unavailable → no answers from model memory, fail safely or escalate; LLM down → deterministic fallbacks (routing, escalation plan); Redis down → documented behaviour, tested.
* Worker deliveries retried with backoff; dead-letter after N attempts.

---

# Observability

* **Structured JSON logs** with `request_id`, OTel `trace_id`/`span_id` (one correlated id space — no separate random trace id), customer id hashed where needed. Never log API keys, credentials, card data or unnecessary customer information.
* Log/trace events to capture: route selected (and source), plan created/validated, agent entered/exited, supervisor decisions, tool calls, tool failures, retrieval duration, retrieval/rerank scores, LLM latency, LLM token usage, web-search latency, database latency, memory load/save/summarize, guardrail triggers, escalation, interrupt/resume, approvals, auth failures, rate limiting, overall latency.
* **OpenTelemetry** spans: `chat_request → auth → input_guardrails → load_memory → router → plan → customer_support (tool.*) ∥ knowledge (vector_search, sparse_search, rerank, web_search) → supervisor → synthesis → decision → escalation/interrupt → output_guardrails → save_memory`, with start/end timestamps so branch concurrency is directly visible.
* **Prometheus metrics** (low-cardinality labels; never request ids): `agent_requests_total, agent_request_duration_seconds, agent_routes_total{source}, supervisor_steps_total, agent_tool_calls_total, agent_tool_failures_total, rag_queries_total, rag_query_duration_seconds, rag_no_results_total, agent_escalations_total{reason}, hitl_queue_size, hitl_time_to_human_reply_seconds, hitl_sla_breaches_total, approvals_total{decision}, llm_requests_total, llm_failures_total, llm_request_duration_seconds, llm_tokens_total{direction}, llm_cost_usd_total, web_search_requests_total, web_search_failures_total, circuit_breaker_state, guardrail_triggers_total, auth_failures_total{reason}, rate_limited_total, memory_summaries_total`.
* **Cost**: per-request tokens and estimated cost from `LLM_PRICE_*`; returned in the debug trace; aggregated in metrics.
* `observability` Compose profile ships **OTel Collector + Jaeger (or Tempo) + Prometheus + Grafana** with a **provisioned dashboard** and **Prometheus alert rules** (error rate, p95 latency, escalation-rate spike, breaker open, HITL SLA breach, RAG no-results ratio, cost spike). Commit dashboard JSON, alert rules and screenshots in `docs/observability/`. No commercial SaaS required.
* `docs/SLO.md`: availability, latency and quality SLOs and the metric/alert measuring each.

---

# Docker and Deployment

* `Dockerfile` (multi-stage: `builder`, `test`, `runtime`), `docker-compose.yml`, `.dockerignore`.
* Non-root runtime; health checks; base images **pinned by digest**; image tagged with version + git SHA (no `:latest` for releases); align Qdrant client and server versions.
* **Reasonable image size**: do not bake ML models into the API image. Download them once into a named `models` volume through a one-shot `models-init` service (pinned revisions + checksum verification), or use a separate pinned model image layer. Report the final image size.
* Compose services: `api`, `worker`, `migrate` (one-shot; api waits on it), `postgres`, `qdrant`, `redis`, `models-init`; optional profiles: `observability`, `proxy` (Caddy/Traefik/nginx for the scale test), `render` (JS crawler). Default stack stays laptop-friendly. Named volumes for PostgreSQL, Qdrant, Redis (if persisted) and models. Only the API (and proxy) is published; datastores bind to `127.0.0.1`.
* `scripts/bootstrap-ubuntu.sh`: verify OS; update metadata; install prerequisites; install **Docker Engine and the Compose plugin from Docker's official repository** (no deprecated packages, no legacy `docker-compose` Python tool); configure; verify Docker; install `uv` for host-side tests; print next steps. Idempotent, readable, safe, fail-fast, documented; exposes nothing beyond what is needed.
* `scripts/deploy.sh` or Make targets. Fresh-server experience:

```bash
git clone <repository> && cd <repository>
sudo ./scripts/bootstrap-ubuntu.sh
cp .env.example .env      # add MINIMAX_API_KEY (and WEB_SEARCH_API_KEY)
make verify               # build, up, migrate, seed, ingest-snapshot, all suites, eval
make ingest               # optional: live crawl
```

* Backup/reset documented; `make dev-reset` explicitly destructive with a warning.

---

# Makefile / Developer Interface

At minimum (every target must exist and work; a test parses the Makefile and README to ensure every documented target exists):

```text
help setup up down logs docker-build migrate seed ingest ingest-snapshot snapshot-export reindex calibrate
demo demo-scenarios demo-conversation operator keys token llm-test smoke-test
test test-docker test-unit test-integration test-orchestration test-security test-e2e test-multiturn test-hitl test-live
eval eval-live coverage lint typecheck security sbom verify purge-expired export-feedback dev-reset
```

* `make test` works on a fresh clone with only `uv` installed (it syncs the locked environment itself — never assume a pre-built `.venv`).
* `make test-docker` runs the full offline suite inside the Dockerfile `test` stage with datastores from Compose — no host Python needed.
* `make verify` is the single evaluator command: from a clean state it builds, starts, migrates, seeds, loads the snapshot corpus, runs smoke, unit, integration, orchestration, security, E2E, multi-turn, HITL and eval (`--strict`) and prints a summary table.
* If a dependency is missing, failures say exactly which command to run.

---

# Testing Strategy

Testing is a first-class deliverable. Implement and **run** tests, separated into:

```text
tests/unit  tests/integration  tests/orchestration  tests/security  tests/e2e  tests/multiturn  tests/hitl
```

* **Unit**: extraction, chunking, metadata, dedup, repositories, tools, schemas, guardrails (incl. normalization/encodings), decision model, routing rules, plan validation, slot extraction, summary trigger, redaction, auth (token validation edge cases), rate limiter, idempotency, error handling.
* **Router/Supervisor**: mocked/deterministic LLM; assert structured routes and step sequences (never prose); invalid output → fallback; step budget enforced; property-style test with random fake decisions proving termination.
* **Tools**: valid/unknown customer, no transactions, absent terminal, declined transaction, scheduled settlement, invalid date ranges, DB failure, cross-user authorization, approval-gated action refused without approval.
* **RAG**: deterministic test corpus; retrieval measured separately from generation (Recall@K, MRR, nDCG); expected source/category in top K; hybrid vs. dense comparison; citation faithfulness.
* **Orchestration**: real LangGraph with controlled LLM — Workflow 1 (parallel, asserted via span timestamps), Workflow 2 (supervisor dependency with product filter), web path, insufficient evidence → escalation, interrupt/resume.
* **Security**: system prompt extraction, encoded/multi-turn injection, cross-user access, JWT/body mismatch, expired/forged tokens, wrong scopes, foreign conversation id, replayed idempotency key from another subject, tool manipulation ("dump all customer records"), RAG injection, hallucination bait (nonexistent products/fees → acknowledge no evidence or escalate), log/memory redaction, canary leak.
* **Multi-turn**: coreference, entity carry-over, language persistence, case status, topic switch, memory injection, summary trigger, erasure, TTL purge, restart between turns, two replicas, concurrent turns.
* **HITL**: interrupt → reply → resume → customer sees it → next turn uses it; approve/reject/edit; restart between interrupt and resume; duplicate resume; SLA bump with injected clock; outbox delivery and retry; audit entries; role separation (operator vs. customer tokens).
* **E2E** against the complete containerized stack: start services, migrate, seed, load test corpus, call HTTP, verify output, route metadata, citations, statuses. Offline deterministic E2E (`LLM_PROVIDER=fake`) separated from **live provider validation** (real MiniMax, runs only when credentials exist).
* **Smoke test** (`make smoke-test`): API, readiness, DB, Qdrant, Redis, seeded customer, one retrieval, token minting; plus a real chat request when MiniMax credentials are configured.
* Coverage with `pytest-cov`; gate ≥ 85% for `app/`.
* `make test` must not require MiniMax or search credits (fake provider, DI, fixtures). Production execution always uses MiniMax. **Mocks belong only in tests.**

---

# AI Evaluation Framework

Create `evaluation/` with a harness (in-process, HTTP and live modes), pure deterministic metric functions, and versioned thresholds.

## Datasets

* **Single-turn**: ≥ 100 cases, including the 10 challenge scenarios (PT+EN), product knowledge, troubleshooting, customer data, web questions, missing data, unknown customers, ambiguous requests, adversarial prompts, injections (plain, encoded, multi-turn), cross-customer attempts, JWT/body mismatch, requests that should escalate. Structured expectations, e.g.:
  ```json
  {
    "id": "settlement-001",
    "user_id": "cliente1988",
    "question": "When will yesterday's sales be deposited?",
    "expected_intent": "customer_settlement",
    "expected_agents": ["customer_support", "knowledge"],
    "must_use_tools": ["get_settlement_schedule"],
    "expected_source_category": "settlement",
    "expected_escalation": false,
    "tags": ["challenge", "en"]
  }
  ```
* **Multi-turn**: ≥ 15 scripted conversations with per-turn expectations (intent, slots, carry-over, no repeated troubleshooting steps, escalation on Nth failure).
* **HITL**: interrupt/resume/approval scenarios with expected statuses, priority and audit entries.
* **Supervisor**: expected step sequences and budget compliance.
* **Golden factuality benchmark** (`evaluation/golden/`): ≥ 60 PT/EN questions, each with supporting URL/chunk ids, **atomic facts** the answer must contain, facts it must not contain (hallucination traps), `labeled_by` and `labeled_at`. Draft labels from the official sources, mark them as requiring human confirmation, and document the labeling protocol in `evaluation/golden/README.md`. This set also drives model selection and threshold calibration.

## Metrics

Deterministic wherever possible: case pass rate, intent accuracy, routing accuracy, plan/step accuracy, tool-selection accuracy, escalation accuracy, priority accuracy, guardrail block accuracy, false-block rate, citation presence and faithfulness, Recall@K, MRR, nDCG, context-carryover accuracy, HITL flow success, claim precision/recall, hallucination rate, answer-language correctness, latency p50/p95, LLM/tool failure rate, tokens and cost per case, per-tag breakdown.

## LLM judge (optional, probabilistic)

Only for semantic claims (relevance, groundedness, completeness) that cannot be checked deterministically; separate prompt and call from generation; temperature 0; clearly labeled probabilistic. **Calibrate** on a ≥ 30-item human-labeled subset and report judge-vs-human accuracy and Cohen's κ; if agreement is weak, say so and do not use the judge as a gate.

## Reports and gating

* `make eval` prints a human-readable report and writes `evaluation/results/latest.json` and `evaluation/results/latest.md`.
* `evaluation/thresholds.yaml` + `--strict` → non-zero exit on regression; committed `evaluation/results/baseline.json`; reports show deltas per metric and per tag.
* `make eval-live` runs against real MiniMax-M3; record the latest live summary (date, commit SHA, metrics) in the README — numbers must come from a real run.
* Do not commit credentials or volatile results unnecessarily.

---

# Static Quality and Supply-Chain Security

* `ruff` (lint + format) and `mypy` configured thoughtfully — do not globally disable useful checks to go green; avoid type evasions like `object` for compiled graphs.
* `make security`: `pip-audit`, Bandit, Trivy image scan, gitleaks — **failing on real findings** (no `|| true`); an explicit reviewed ignore list with justification and expiry dates.
* `make sbom` (e.g., syft). Locked dependencies.

---

# CI (GitHub Actions)

Create `.github/workflows/ci.yml` **and verify it actually exists and runs**. Jobs: lint, typecheck, unit, integration/orchestration with service containers (Postgres, Qdrant, Redis), security tests, offline E2E against the composed stack, multi-turn and HITL suites, `eval --strict`, coverage gate, Docker build with cache, Trivy, gitleaks, SBOM artifact, snapshot-corpus artifact. A scheduled/manual workflow runs live MiniMax tests and `eval-live` only when secrets exist; forked PRs must not fail for lack of secrets. Cache dependencies responsibly. Add the CI badge to the README. The final commit must be green.

---

# Documentation

## README.md

Must let an evaluator unfamiliar with the project run and understand it. At the top: a **5-minute evaluator path** (`make verify` → open `/ui` and `/operator` → follow the demo script) and the video link placeholder. Then:

Overview · Architecture (Mermaid) · The central principle and where LLM proposes vs. code decides · Agent responsibilities (Router, Supervisor, Knowledge, Customer Support, Escalation, Synthesizer) · Message workflows (sequence diagrams for Workflows 1–6) · RAG pipeline (ingestion, cleaning, chunking, dense+sparse embedding, storage, retrieval, fusion, reranking, generation, citations, calibration) · MiniMax-M3 integration (API, configuration, provider abstraction, tool-calling and structured-output approach) · Customer tools and action policy · Synthetic data · Conversation memory · Human-in-the-loop · Identity & auth (both modes with curl) · Quick start · Fresh Ubuntu 26.04 installation · Configuration (every variable) · Testing (all commands) · Evaluation (how to run and interpret; latest offline and live results) · Observability (logs, traces, metrics, dashboards, alerts, cost) · Security (threat model summary) · Guardrails · Reliability · Performance and cost per workflow (LLM calls, tokens, latency) · Limitations (honest) · Production evolution (real Getnet APIs behind the tool seam, managed OIDC, secrets manager, streaming, canary evaluations).

Every command and curl example in the README must be tested before documenting it.

## Other documents

* `docs/ARCHITECTURE.md` — component diagram, sequence diagrams (incl. interrupt/resume and approval), data flows, trust boundaries, agent permissions, failure behaviour.
* `docs/THREAT_MODEL.md` — assets, trust boundaries (customer, operator, LLM provider, web content, crawler, memory), STRIDE table, mapping to the **OWASP Top 10 for LLM Applications (2025)** with the control and the test file for each item.
* `docs/SLO.md`, `docs/observability/` (dashboard JSON, alert rules, screenshots).
* `docs/TESTING_GUIDE.md` — strategy, how to run each suite, how integration testing of the orchestration works.
* `docs/VIDEO_SCRIPT.md` — architecture, multi-agent cooperation, supervisor dependency, multi-turn memory surviving a restart, HITL loop, approval, JWT/body mismatch rejection, cross-customer attempt, evaluation results, dashboards.
* `docs/SCORECARD.md` — for each rubric criterion, the concrete evidence (file paths, test names, commands, metrics) so nothing is taken on faith.
* `docs/adr/` — concise ADRs (Context, Decision, Alternatives considered, Consequences, **Measured evidence**), numbered `001-langgraph-orchestration.md`, `002-modular-monolith.md`, `003-vector-database.md`, `004-minimax-provider.md`, `005-web-search-provider.md`, and so on, at least for: LangGraph orchestration; modular monolith; vector database & hybrid retrieval; MiniMax provider; web search provider; embedding/reranker selection by benchmark; conversation memory & checkpointer; HITL interrupt/resume & approval policy; authentication model & `AUTH_MODE`; supervisor with bounded replanning; Redis; model distribution & image size.

---

# Demonstration Experience

* **Customer chat UI** (static HTML + vanilla JS at `/ui`, dev/demo only, no build toolchain): conversation list, messages, sources, agents used, route source, confidence, case id/status, live human replies via SSE; uses JWTs (a demo login mints dev tokens only in non-production).
* **Operator console** at `/operator` and `make operator` CLI.
* `make demo-scenarios`: runs the 10 challenge scenarios (PT and EN) and prints a table (intent, agents, plan steps, tools, sources, escalated, latency, tokens).
* `make demo-conversation`: scripted multi-turn conversation demonstrating memory, Workflow 2 dependency, escalation, operator reply via CLI and the customer receiving it.
* README includes tested curl examples, e.g.:

```bash
TOKEN=$(make -s token SUB=cliente1988 SCOPE=chat)
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": "When will the money from yesterday'\''s sales be deposited?", "user_id": "cliente1988"}'

# AUTH_MODE=demo (development only) — the exact challenge payload, no token
curl -s -X POST http://localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "When will the money from yesterday'\''s sales be deposited?", "user_id": "cliente1988"}'

# follow-up turn in the same conversation
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": "And the sales from the day before?", "conversation_id": "<id from previous response>"}'
```

Verify every example before documenting it.

---

# Real Provider Validation

When `MINIMAX_API_KEY` is available, run actual MiniMax-M3 validation (`make llm-test`, `make test-live`, `make eval-live`) and confirm:

* `/v1/chat/completions` compatibility;
* structured (JSON/schema) responses parse and validate, with a repair/retry path for malformed output;
* **tool calling** works for the Customer Support loop (tool_calls round-trip, arguments JSON);
* streaming is not required, but if used, it works;
* timeout handling, retry policy and circuit breaker behave as configured;
* error handling for 401/403 (no retry), 429/5xx (retry with backoff), empty or truncated responses;
* token usage is read from responses and reaches metrics.

If the latest official MiniMax API behaves differently from assumptions, consult current official MiniMax documentation and adapt the provider. Do not change the model away from MiniMax-M3 because an integration assumption was wrong.

---

# Suggested Repository Structure

Use or improve this structure (adapt when technically justified):

```text
.
├── app/
│   ├── main.py                    # FastAPI app factory, lifespan, middleware, static /ui and /operator
│   ├── config.py                  # typed Settings, fail-fast validation, safe_dict()
│   ├── errors.py                  # error hierarchy
│   ├── api/
│   │   ├── routes_chat.py         # /v1/chat
│   │   ├── routes_conversations.py
│   │   ├── routes_operator.py
│   │   ├── routes_health.py       # /health/*, /metrics, /v1/debug/traces
│   │   ├── schemas.py             # request/response models, ErrorResponse
│   │   ├── deps.py                # DI container
│   │   ├── auth.py                # JWT/JWKS, AUTH_MODE, scopes, Principal → AuthContext
│   │   └── middleware.py          # request context, Redis rate limit, idempotency, security headers
│   ├── orchestration/
│   │   ├── graph.py               # LangGraph build, checkpointer wiring, interrupt/resume
│   │   ├── state.py               # AgentState and domain models
│   │   ├── routing.py             # deterministic rules + intent→agent mapping
│   │   ├── planning.py            # Plan model + validation
│   │   ├── supervisor.py
│   │   ├── decision.py            # deterministic confidence/escalation model
│   │   └── policy.py              # action policy table (auto|approval|forbidden)
│   ├── agents/
│   │   ├── router.py  knowledge.py  customer_support.py  escalation.py  synthesizer.py  human_reply.py
│   │   └── prompts/               # *.md prompt templates (router, supervisor, knowledge_rag, knowledge_web,
│   │                              #   customer_support, escalation, synthesizer, human_reply, summary)
│   ├── memory/
│   │   ├── store.py               # conversations/turns repositories (customer-scoped)
│   │   ├── slots.py               # deterministic slot extraction
│   │   └── summary.py             # rolling summary
│   ├── hitl/
│   │   ├── service.py             # operator actions, resume, approvals
│   │   ├── outbox.py
│   │   └── sla.py
│   ├── llm/
│   │   ├── base.py                # LLMProvider protocol, structured-output helper
│   │   ├── minimax.py
│   │   ├── fake.py                # test double (refused in production)
│   │   └── reliability.py         # retry, backoff+jitter, circuit breaker
│   ├── tools/
│   │   ├── base.py                # Tool, ToolRegistry, AuthContext
│   │   ├── customer.py            # read-only tools
│   │   └── actions.py             # create_support_case, approval-gated actions
│   ├── rag/
│   │   ├── embeddings.py  sparse.py  reranker.py  qdrant_store.py  retriever.py  schemas.py
│   ├── guardrails/
│   │   ├── input.py  output.py  normalize.py  redaction.py
│   ├── services/
│   │   └── web_search.py          # WebSearchProvider + Tavily implementation
│   ├── observability/
│   │   ├── logging.py  metrics.py  tracing.py  trace_store.py  cost.py
│   ├── db/
│   │   ├── base.py  models.py  repositories.py  seed.py
│   ├── worker/
│   │   └── main.py                # outbox delivery, SLA timers, TTL purge
│   └── static/
│       ├── ui/                    # customer chat page
│       └── operator/              # operator console
├── ingestion/
│   ├── sources.yaml  crawler.py  render.py  extract.py  pdf.py  chunker.py  pipeline.py  snapshot.py
├── evaluation/
│   ├── dataset.json  multiturn.json  hitl.json  supervisor.json  thresholds.yaml
│   ├── golden/                    # factuality benchmark + labeling protocol
│   ├── run_eval.py  metrics.py  judge.py  calibrate.py
│   └── results/                   # latest.json, latest.md, baseline.json, calibration.md
├── tests/
│   ├── unit/  integration/  orchestration/  security/  e2e/  multiturn/  hitl/
│   ├── conftest.py
│   └── fakes.py
├── scripts/
│   ├── bootstrap-ubuntu.sh  deploy.sh  llm_test.py  smoke_test.py  mint_token.py  operator.py  demo.py
├── docker/
│   ├── entrypoint.sh  prometheus.yml  alerts.yml  otel-collector.yml  grafana/  proxy/
├── docs/
│   ├── IMPLEMENTATION_PLAN.md  ARCHITECTURE.md  THREAT_MODEL.md  SLO.md  TESTING_GUIDE.md
│   ├── VIDEO_SCRIPT.md  SCORECARD.md  observability/  adr/
├── alembic/
├── .github/workflows/ci.yml  .github/workflows/live.yml
├── Dockerfile  docker-compose.yml  Makefile  pyproject.toml  uv.lock
├── .env.example  .gitignore  .dockerignore  .gitleaksignore
└── README.md
```

---

# Language, Performance, Cost, Privacy, Errors

* **Language**: Portuguese and English; answer in the customer's language; retrieval must work for EN questions over mainly-PT content; language persists within a conversation.
* **Performance**: do not optimize prematurely, but pay attention to latency; run independent branches concurrently; avoid unnecessary sequential LLM calls; measure everything; report LLM calls per request for each workflow.
* **Cost**: do not add agents or LLM calls to look sophisticated; deterministic code where deterministic is better; enforce the token budget.
* **Privacy**: synthetic data clearly labeled; no real PANs, personal records, credentials or secrets in the repository; redaction everywhere; retention and erasure implemented.
* **Error model**: `ConfigurationError, AuthenticationError, AuthorizationError, ProviderError, RetrievalError, ToolError, ExternalSearchError, ApprovalRequired, EscalationRequired, ConflictError` — translated to safe API responses.

---

# Code Quality

Avoid giant agent classes, global mutable state, circular imports, untyped dicts everywhere, duplicated prompts or business logic, hardcoded credentials, magic constants, broad `except Exception` without handling, hidden network calls, tightly coupled infrastructure and needless abstraction layers. Prefer small modules, dependency inversion, protocols, typed domain models, testable services, explicit errors, reusable configuration, and a shared helper for agent event/timing boilerplate.

## Agent prompt quality

Prompts live under source control separately from code (e.g., `app/agents/prompts/*.md`). Each defines role, responsibilities, available tools, forbidden behaviour, grounding rules, escalation rules, untrusted-data rules, language rules, and the expected structured output, with few-shot examples where classification is involved. Keep prompts and validators consistent (e.g., length limits). No vague "You are a helpful assistant."

---

# Important Anti-Patterns

Do NOT:

* create fake RAG responses, fake web search results or pretend tools ran;
* hardcode challenge answers or route by exact question match;
* use an in-memory list instead of the requested storage architecture;
* return made-up customer settlements;
* let another LLM do MiniMax-M3's job;
* commit secrets or expose hidden reasoning;
* let the LLM perform authorization or decide conversation/case ownership;
* allow the LLM to run SQL;
* fake memory by echoing client-sent history or trusting a client-supplied thread id;
* implement HITL as a flag with no resume path;
* execute approval-gated tools before approval, or let an operator act on another customer;
* store raw PII in memory or logs;
* silently fall back to model memory when verified Getnet evidence is required;
* keep correctness-relevant state in process memory;
* claim CI, dashboards, benchmarks or metrics that are not in the repository or were not measured;
* swallow security-check failures;
* skip tests because an API key is unavailable;
* break the challenge payload;
* leave core TODOs.

---

# Final Validation Procedure

From as clean a state as possible:

```bash
docker compose down -v
docker compose build --no-cache
docker compose up -d
make migrate
make seed
make ingest-snapshot
make smoke-test
make test
make test-docker
make eval
make verify            # must reproduce all of the above in one command
make ingest
make demo-scenarios
make demo-conversation
docker compose --profile proxy up -d --scale api=2
make llm-test && make test-live && make eval-live    # when MINIMAX_API_KEY is configured
```

Then manually, through HTTP, in both `AUTH_MODE=jwt` and `AUTH_MODE=demo`:

* all challenge scenarios (PT and EN);
* a 4-turn conversation with coreference, restarting the `api` container between turns;
* escalation → operator reply via console/CLI → customer receives it → next turn uses it;
* an approval-gated action approved and another rejected;
* body `user_id` ≠ token `sub` → 403; foreign `conversation_id` → 404;
* injection in turn 1 with no effect in turn 3;
* MiniMax, web search, Qdrant, PostgreSQL and Redis outages → graceful behaviour.

Inspect HTTP status, answer quality, routing, supervisor steps, tools, citations, memory slots, case/outbox/audit rows, logs, traces and dashboards. `docker compose ps` must show all mandatory services healthy; logs must be free of uncaught exceptions, retry storms, DB/Qdrant errors and malformed LLM responses. The GitHub Actions run must be green. Fix what you find. **Do not declare completion while known core tests are failing.**

---

# Final Adversarial QA

Switch roles: act as an adversarial senior engineer scoring a competing candidate with the rubric above. Try to break the system — hallucinated Getnet facts, broken routing, tool-selection errors, cross-customer leaks (tools, memory, conversations, cases, idempotency replays, HITL resume), prompt/RAG/encoded/multi-turn injection, summary poisoning, missing citations, stale current-information mistakes, supervisor budget exhaustion, graph loops, tool-call loops, runaway retries, excessive tokens, missing timeouts, Docker startup races, migration problems, crawler failures, malformed HTML, duplicate chunks, datastore and provider outages, malformed/empty LLM responses, race conditions on the same conversation, secrets in logs, stack traces to clients, broken README commands, claims not backed by files, dependency vulnerabilities, container security mistakes. Fix every issue and add regression tests for meaningful bugs.

---

# Final Repository Review

1. `git status`; review all files;
2. remove temporary/debug artifacts;
3. `.env`, dev keys and caches are ignored and untracked;
4. no credentials anywhere (gitleaks clean);
5. README commands, diagrams and SCORECARD match reality;
6. tests describe actual behaviour;
7. Docker Compose represents the actual runtime;
8. `.github/workflows/ci.yml` exists and is green.

---

# Definition of Done

* [ ] FastAPI application starts successfully; Docker image builds; Docker Compose stack starts.
* [ ] PostgreSQL, Qdrant and Redis are persistent (named volumes) and healthy.
* [ ] Worker and all Compose services are healthy; image size reported and reasonable.
* [ ] `/health/live`, `/health/ready` and `/metrics` work; debug traces work in development and are disabled in production.
* [ ] Structured logging works and contains no secrets.
* [ ] Migrations (incl. checkpointer setup) and idempotent synthetic seeding work; `cliente1988` and a second customer have rich data.
* [ ] MiniMax-M3 provider implemented; live connectivity tested with `make llm-test`.
* [ ] Router (hybrid), Supervisor (bounded replanning), Knowledge (RAG + web), Customer Support (tools), Escalation (HITL) and Synthesizer work inside a LangGraph graph.
* [ ] Workflow 1 runs specialists in parallel; Workflow 2 is a supervisor-driven dependency visible in traces.
* [ ] ≥ 7 read-only tools, idempotent `create_support_case`, ≥ 1 approval-gated write action; action policy table.
* [ ] Crawler (static + optional JS render), PDF ingestion, hybrid dense+sparse retrieval, reranking, calibrated thresholds, snapshot corpus, citations with provenance.
* [ ] Web search works when configured and degrades honestly.
* [ ] Durable customer-scoped memory: window, summary, slots; survives restarts and replicas; erasure and TTL purge; idempotency keys; concurrency safety.
* [ ] Complete HITL: interrupt → operator reply/decision → resume → customer receives it → agents use it; operator API, console, CLI; outbox notifications; SLA bumps; audit log.
* [ ] JWT auth with JWKS; `AUTH_MODE=demo` refused in production; body/sub mismatch → 403; scopes on every route; Redis rate limiting.
* [ ] Guardrails (input, retrieval, tool, output, memory, canary) with tests.
* [ ] Structured logs with correlated OTel ids; metrics; dashboards and alert rules shipped; cost per request; debug traces in development only.
* [ ] Unit, integration, orchestration, security, E2E, multi-turn and HITL suites pass; coverage gate met.
* [ ] Evaluation: ≥ 100 single-turn cases, multi-turn, HITL, supervisor suites, golden factuality benchmark with judge calibration, baseline deltas, `--strict` gating.
* [ ] `make test` works on a fresh clone with only uv; `make test-docker` and `make verify` work from `docker compose down -v`.
* [ ] Supplied challenge scenarios manually verified through HTTP.
* [ ] Ubuntu 26.04 bootstrap script works.
* [ ] GitHub Actions CI exists and is green; security checks fail on real findings.
* [ ] README, ARCHITECTURE, THREAT_MODEL, SLO, TESTING_GUIDE, VIDEO_SCRIPT, SCORECARD and ADRs complete and accurate.
* [ ] No secrets committed; no core TODOs remain.

---

# Final Completion Report

Only after completing and validating the implementation, report concisely:

* **Architecture implemented** — graph nodes/edges incl. supervisor, interrupt/resume, memory load/save.
* **Agents** — responsibilities and tool permissions.
* **Tools** — each tool, read/write/approval classification.
* **RAG** — pages and chunks ingested (with/without JS rendering), embedding/sparse/reranker models with benchmark numbers, calibrated thresholds, vector DB.
* **MiniMax** — integration method, model, whether live validation succeeded (never display the key).
* **Memory** — storage, scoping, window/summary/slots, retention, restart/replica test results.
* **HITL** — flow, operator surfaces, SLA, audit, test evidence.
* **Security** — auth modes, scopes, rate limiting, guardrails, threat model highlights, security tests.
* **Docker** — services, health, image size, tested startup commands, replica test.
* **Tests** — exact `X passed / Y failed / Z skipped` per suite; coverage %. Explain any failure.
* **Evaluation** — actual offline and (if a key was available) live metrics.
* **Observability** — dashboards, alerts, cost per workflow.
* **CI** — workflow status.
* **Known limitations** — transparent.
* **How to run** — exact minimal verified commands.
* **Demonstration** — 8–10 queries and one scripted conversation for the video: product comparison; personalized settlement; terminal troubleshooting with supervisor dependency; live external information; multi-turn follow-up surviving a restart; prompt-injection attack; cross-customer attempt / JWT mismatch; human escalation with operator reply flowing back; approval-gated action.

Do not claim functionality you did not actually validate.

---

# Final Principle

**LLMs interpret, plan and phrase. Deterministic software owns identity, authorization, memory scope, state, approvals, tool boundaries, validation, reliability and security.**

The evaluators must be able to clone this repository onto a fresh Ubuntu 26.04 machine, configure the API credentials, run one command, and then hold a multi-turn conversation in which specialized agents cooperate, a supervisor reacts to what they found, memory survives restarts, a human operator's answer flows back into the conversation, and every claim in the README is backed by a test, a metric or a file they can open.

Build that system completely.
