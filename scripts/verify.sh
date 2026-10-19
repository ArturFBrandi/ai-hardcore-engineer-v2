#!/usr/bin/env bash
# `make verify` — the single evaluator command. From a clean checkout: build, start, migrate, seed, load the
# snapshot corpus, smoke-test, run every offline suite, the containerized E2E suite and `eval --strict`,
# then print a summary table. Exits non-zero if anything fails. Needs: docker (compose plugin) and uv.
set -uo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || { echo "docker not found → sudo ./scripts/bootstrap-ubuntu.sh"; exit 1; }
command -v uv >/dev/null || { echo "uv not found → curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example (add MINIMAX_API_KEY for the real assistant)"; }

export GIT_SHA="${GIT_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}"
export MODELS_DIR="${MODELS_DIR:-$HOME/.cache/getnet-models}"
declare -a NAMES STATUS DURATION
FAILED=0

step() {  # step <name> <command...>
  local name="$1"; shift
  local t0=$SECONDS
  echo; echo "════ ${name} ════"
  if "$@"; then STATUS+=("PASS"); else STATUS+=("FAIL"); FAILED=1; fi
  NAMES+=("$name"); DURATION+=("$((SECONDS - t0))s")
}

step "sync locked env"          uv sync --frozen
step "dev JWT keys"             uv run python -m scripts.mint_token keys
step "build image"              docker compose build api
step "start stack (offline LLM)" env LLM_PROVIDER=fake WEB_SEARCH_PROVIDER=disabled docker compose up -d --wait
step "migrate"                  docker compose run --rm migrate
step "seed"                     docker compose run --rm --no-deps migrate seed
step "ingest snapshot"          docker compose run --rm --no-deps -e MODELS_OFFLINE=true migrate snapshot import
step "smoke test"               uv run python -m scripts.smoke_test --api-url http://localhost:8000
step "unit"                     uv run pytest tests/unit -q
step "integration"              uv run pytest tests/integration -q
step "orchestration"            uv run pytest tests/orchestration -q
step "security"                 uv run pytest tests/security -q
step "multi-turn"               uv run pytest tests/multiturn -q
step "hitl"                     uv run pytest tests/hitl -q
step "e2e (containerized stack)" env API_URL=http://localhost:8000 uv run pytest tests/e2e -q -m "stack and not scale"
step "eval --strict"            uv run python -m evaluation.run_eval --strict
if grep -qE '^MINIMAX_API_KEY=.+' .env 2>/dev/null; then
  step "restart stack with MiniMax-M3" docker compose up -d --wait
fi

echo; echo "════════════════════ make verify summary ════════════════════"
printf "%-34s %-6s %s\n" "STEP" "RESULT" "TIME"
for i in "${!NAMES[@]}"; do printf "%-34s %-6s %s\n" "${NAMES[$i]}" "${STATUS[$i]}" "${DURATION[$i]}"; done
echo "evaluation report: evaluation/results/latest.md"
if [ "$FAILED" -ne 0 ]; then echo "VERIFY FAILED"; exit 1; fi
echo "VERIFY PASSED — open http://localhost:8000/ui and http://localhost:8000/operator"
