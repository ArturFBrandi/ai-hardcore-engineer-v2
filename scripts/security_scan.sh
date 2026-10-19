#!/usr/bin/env bash
# `make security` — dependency, code, secret and image scanning. Fails on real findings (no `|| true`).
# Reviewed exceptions live in .security-ignore.yaml-style files with justification and expiry.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="getnet-support-agents:${APP_VERSION:-1.0.0}-${GIT_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}"
TRIVY_IMAGE="aquasec/trivy:0.66.0"
GITLEAKS_IMAGE="zricethezav/gitleaks:v8.28.0"

echo "── pip-audit (locked runtime + dev dependencies)"
REQ="$(mktemp)"
uv export --frozen --format requirements-txt --no-hashes --no-emit-project > "$REQ"
# shellcheck disable=SC2046
uv run pip-audit --strict --requirement "$REQ" --disable-pip --no-deps $(sed -n 's/^\s*-\s*id:\s*\(\S*\).*/--ignore-vuln \1/p' .pip-audit-ignore.yaml 2>/dev/null)

echo "── Bandit (app, ingestion, scripts): fails on Medium/High; Low findings reviewed (asserts, fixed-argv subprocess in dev scripts)"
uv run bandit -q -ll -r app ingestion scripts -c pyproject.toml

echo "── gitleaks (entire git history; git-ignored local secrets such as .env/.dev-keys are never committed)"
docker run --rm -v "$PWD:/repo" "$GITLEAKS_IMAGE" git /repo --redact --config /repo/.gitleaks.toml --exit-code 1

echo "── Trivy image scan ($IMAGE): HIGH/CRITICAL with a fix available"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker compose build api
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/.trivyignore:/.trivyignore:ro" "$TRIVY_IMAGE" \
  image --quiet --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --ignorefile /.trivyignore --exit-code 1 "$IMAGE"
echo "security scan passed"
