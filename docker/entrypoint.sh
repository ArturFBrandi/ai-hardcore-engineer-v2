#!/bin/sh
# Single image, several roles (modular monolith).
set -eu
role="${1:-api}"
shift || true
case "$role" in
  api)
    exec uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8000 \
      --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
      --timeout-graceful-shutdown "${GRACEFUL_SHUTDOWN_SECONDS:-60}" --no-access-log "$@"
    ;;
  worker)   exec python -m app.worker.main "$@" ;;
  migrate)  exec python -m app.db.migrate "$@" ;;
  seed)     exec python -m app.db.seed "$@" ;;
  models)   exec python -m scripts.models_init "$@" ;;
  snapshot) exec python -m ingestion.snapshot "$@" ;;
  ingest)   exec python -m ingestion.pipeline "$@" ;;
  *)        exec "$role" "$@" ;;
esac
