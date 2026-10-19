# Getnet Multi-Agent Support System — developer interface. `make help` lists every target.
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv
export UV_LINK_MODE ?= copy
export GIT_SHA ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
export APP_VERSION ?= 1.0.0
COMPOSE ?= docker compose
API_URL ?= http://localhost:8000
# Host-side tests/eval reuse the downloaded models cache (the stack uses the `models` volume).
export MODELS_DIR ?= $(HOME)/.cache/getnet-models
DATASTORES ?= compose
PYTEST ?= $(UV) run pytest
ENVFILE := $(if $(wildcard .env),--env-file .env,)

.PHONY: help setup up down logs docker-build migrate seed ingest ingest-snapshot snapshot-export reindex calibrate \
        benchmark-retrieval demo demo-scenarios demo-conversation operator keys token llm-test smoke-test \
        test test-docker test-unit test-integration test-orchestration test-security test-e2e test-multiturn test-hitl \
        test-live eval eval-live coverage lint typecheck security sbom verify purge-expired export-feedback dev-reset \
        datastores render-ingest observability-up scale-test check-deps

help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

check-deps:
	@command -v $(UV) >/dev/null || { echo "uv not found → run: curl -LsSf https://astral.sh/uv/install.sh | sh   (or sudo ./scripts/bootstrap-ubuntu.sh)"; exit 1; }

setup: check-deps ## Sync the locked Python environment (uv sync --frozen)
	$(UV) sync --frozen

# ------------------------------------------------------------------------------------- stack
keys: setup ## Generate the git-ignored dev JWT keypair (.dev-keys/) — idempotent
	$(UV) run python -m scripts.mint_token keys

token: ## Mint a dev JWT: make -s token SUB=cliente1988 SCOPE="chat"
	@$(UV) run --quiet python -m scripts.mint_token token --sub "$(or $(SUB),cliente1988)" --scope "$(or $(SCOPE),chat conversations:read conversations:delete)"

docker-build: ## Build the runtime image (tagged version + git SHA)
	$(COMPOSE) build api
	@docker image ls "getnet-support-agents:$(APP_VERSION)-$(GIT_SHA)" --format 'image {{.Repository}}:{{.Tag}} size {{.Size}}'

up: keys ## Start the stack (waits for health), seed and load the snapshot corpus
	@command -v docker >/dev/null || { echo "docker not found → run: sudo ./scripts/bootstrap-ubuntu.sh"; exit 1; }
	$(COMPOSE) up -d --wait
	$(MAKE) --no-print-directory seed ingest-snapshot

down: ## Stop the stack (keeps volumes)
	$(COMPOSE) --profile proxy --profile observability down

logs: ## Follow api + worker logs
	$(COMPOSE) logs -f api worker

migrate: ## Apply Alembic migrations + LangGraph checkpointer setup (one-shot container)
	$(COMPOSE) run --rm migrate

seed: ## Load the idempotent synthetic seed (dates relative to now)
	$(COMPOSE) run --rm --no-deps migrate seed

ingest-snapshot: ## Index the committed corpus snapshot into Qdrant (no crawl needed)
	$(COMPOSE) run --rm --no-deps -e MODELS_OFFLINE=true migrate snapshot import

snapshot-export: ## Export the current Qdrant corpus to data/snapshots (versioned, checksummed)
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/data:/app/data" migrate snapshot export

ingest: ## Live crawl of the official Getnet sources (incremental; respects robots.txt)
	$(COMPOSE) run --rm --no-deps -e MODELS_OFFLINE=true -v "$(CURDIR)/data:/app/data" migrate ingest crawl

reindex: ## Safe rebuild: drop and rebuild the collection from a fresh crawl
	$(COMPOSE) run --rm --no-deps -e MODELS_OFFLINE=true -v "$(CURDIR)/data:/app/data" migrate ingest crawl --recreate

render-ingest: ## Crawl with JS rendering (Playwright, separate image; render profile)
	$(COMPOSE) --profile render run --rm render

observability-up: ## Start OTel Collector + Jaeger + Prometheus + Grafana and enable tracing
	OTEL_ENABLED=true $(COMPOSE) --profile observability up -d --wait

scale-test: ## Two API replicas behind nginx (:8080); verifies context, rate limits and traces across replicas
	$(COMPOSE) -f docker-compose.yml -f docker/compose.scale.yml --profile proxy up -d --wait --scale api=2
	API_URL=http://localhost:8080 $(PYTEST) tests/e2e/test_scale.py -q -m scale

purge-expired: ## Retention: delete expired conversations and their checkpoints now
	$(COMPOSE) run --rm --no-deps migrate python -m scripts.purge_expired

export-feedback: ## Export operator resolutions/replies as candidate eval cases (needs review)
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/evaluation:/app/evaluation" migrate python -m scripts.export_feedback

dev-reset: ## DESTRUCTIVE: remove containers AND volumes (database, vectors, models)
	@echo "WARNING: this deletes all local data (PostgreSQL, Qdrant, Redis, models). Ctrl-C to abort."; sleep 5
	$(COMPOSE) --profile proxy --profile observability --profile render down -v

# ------------------------------------------------------------------------------------- demo
llm-test: setup ## Minimal REAL MiniMax-M3 requests (plain, structured, tool call)
	$(UV) run $(ENVFILE) python -m scripts.llm_test

smoke-test: setup ## API, readiness, DB, Qdrant, Redis, seed, retrieval, tokens (+ real chat if MiniMax is configured)
	$(UV) run $(ENVFILE) python -m scripts.smoke_test --api-url $(API_URL)

demo: demo-scenarios ## Alias for demo-scenarios

demo-scenarios: setup ## Run the 10 challenge scenarios (PT+EN) against the running stack, print a table
	$(UV) run python -m scripts.demo scenarios --api-url $(API_URL)

demo-conversation: setup ## Scripted multi-turn conversation: memory, dependency, escalation, operator reply
	$(UV) run python -m scripts.demo conversation --api-url $(API_URL)

operator: setup ## Operator CLI, e.g. make operator ARGS="list" | ARGS="reply CASE-... 'text'"
	$(UV) run python -m scripts.operator $(ARGS)

# ------------------------------------------------------------------------------------- tests
datastores: ## Start PostgreSQL, Qdrant and Redis for host-side tests (waits until healthy)
ifeq ($(DATASTORES),compose)
	@command -v docker >/dev/null || { echo "docker not found → run: sudo ./scripts/bootstrap-ubuntu.sh"; exit 1; }
	$(COMPOSE) up -d --wait postgres qdrant redis
endif

test: setup datastores ## Full offline suite (fake LLM, no credits needed): unit → hitl
	$(PYTEST) tests -m "not live and not stack" -q

test-unit: setup ## Unit tests (no datastores)
	$(PYTEST) tests/unit -q

test-integration: setup datastores ## Integration tests (real PostgreSQL/Qdrant/Redis)
	$(PYTEST) tests/integration -q

test-orchestration: setup datastores ## Real LangGraph graph with controlled LLM
	$(PYTEST) tests/orchestration -q

test-security: setup datastores ## Security suite
	$(PYTEST) tests/security -q

test-multiturn: setup datastores ## Multi-turn memory suite
	$(PYTEST) tests/multiturn -q

test-hitl: setup datastores ## Human-in-the-loop suite
	$(PYTEST) tests/hitl -q

test-e2e: setup ## E2E over HTTP against the composed stack (offline: LLM_PROVIDER=fake stack)
	API_URL=$(API_URL) $(PYTEST) tests/e2e -q -m "stack and not scale"

test-live: setup ## Live MiniMax-M3 / Tavily tests (skipped with a message when credentials are absent)
	$(UV) run $(ENVFILE) pytest tests/live -q -m live

test-docker: ## Full offline suite inside the Dockerfile `test` stage (no host Python needed)
	$(COMPOSE) up -d --wait postgres qdrant redis
	docker build --target test -t getnet-support-agents:test .
	docker run --rm --network host -e MODELS_DIR=/models -v getnet-support_models:/models getnet-support-agents:test \
	  pytest tests -m "not live and not stack" -q -p no:cacheprovider

coverage: setup datastores ## Offline suite with coverage gate (>= 85% of app/)
	$(PYTEST) tests -m "not live and not stack" -q --cov=app --cov-report=term --cov-report=xml --cov-fail-under=85

eval: setup datastores ## Offline evaluation (fake LLM) with --strict gates → evaluation/results/latest.{json,md}
	$(UV) run python -m evaluation.run_eval --strict

eval-live: setup datastores ## Live evaluation against MiniMax-M3 + Tavily → evaluation/results/live-latest.{json,md}
	$(UV) run $(ENVFILE) python -m evaluation.run_eval --mode live

calibrate: setup datastores ## Sweep RAG thresholds on the golden set → evaluation/results/calibration.md
	$(UV) run python -m evaluation.calibrate

benchmark-retrieval: setup datastores ## Benchmark embedding/reranker candidates → evaluation/results/retrieval_benchmark.md
	$(UV) run python -m evaluation.benchmark_retrieval

# ------------------------------------------------------------------------------------- quality
lint: setup ## Ruff lint + format check
	$(UV) run ruff check app ingestion evaluation scripts tests
	$(UV) run ruff format --check app ingestion evaluation scripts tests

typecheck: setup ## mypy --strict on app/
	$(UV) run mypy app

security: setup ## pip-audit + Bandit + gitleaks + Trivy (fail on findings; reviewed ignores only)
	./scripts/security_scan.sh

sbom: ## CycloneDX SBOM of the runtime image (syft) → sbom.cdx.json
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock anchore/syft:v1.33.0 \
	  "getnet-support-agents:$(APP_VERSION)-$(GIT_SHA)" -o cyclonedx-json > sbom.cdx.json
	@echo "wrote sbom.cdx.json"

verify: ## THE evaluator command: build, start, migrate, seed, snapshot, smoke, all suites, eval --strict
	./scripts/verify.sh
