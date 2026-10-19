# Getnet Multi-Agent Support System — developer interface. `make help` lists targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv
export UV_LINK_MODE ?= copy

.PHONY: help setup lint typecheck test-unit

help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Sync the locked Python environment (requires uv)
	@command -v $(UV) >/dev/null || { echo "uv not found: run 'curl -LsSf https://astral.sh/uv/install.sh | sh' or sudo ./scripts/bootstrap-ubuntu.sh"; exit 1; }
	$(UV) sync --frozen

lint: setup ## Ruff lint + format check
	$(UV) run ruff check app ingestion evaluation scripts tests
	$(UV) run ruff format --check app ingestion evaluation scripts tests

typecheck: setup ## mypy (strict) on app/
	$(UV) run mypy app

test-unit: setup ## Unit tests (no datastores, fake LLM)
	$(UV) run pytest tests/unit -q
