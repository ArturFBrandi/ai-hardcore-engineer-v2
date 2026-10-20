# ADR 014 — Scaling the API with a fixed published port

**Status:** accepted · 2026-10-20

## Context
The spec's validation step is `docker compose --profile proxy up -d --scale api=2`. In the default stack, `api` publishes host
port 8000, which is the port every README `curl` uses. The final validation run showed that the literal command fails
(`Bind for 0.0.0.0:8000 failed: port is already allocated`), because two replicas can't publish the same host port.

Tried: publishing a range (`8000-8001:8000`). Scaling then worked, but a **single** replica got 8001 in 2 of 3 recreates,
which would break every documented command. Rejected.

## Decision
- The single-replica stack keeps the fixed `8000`.
- Scaled runs use either form, both verified on 2026-10-20:
  - `make scale-test`. This applies `docker/compose.scale.yml` (`ports: !reset []`, so replicas are reachable only through nginx on
    :8080), then runs `tests/e2e/test_scale.py`.
  - `API_PORT=8000-8001 docker compose --profile proxy up -d --scale api=2`. This is the spec's command plus one variable;
    the replicas take 8000 and 8001, and nginx serves :8080.
- The proxy now has a healthcheck, so `--wait` covers it. Without one, nginx returned 503 for about 1 s after start.

## Consequences
The spec's literal command needs one environment variable. Everything that targets port 8000 stays stable.
