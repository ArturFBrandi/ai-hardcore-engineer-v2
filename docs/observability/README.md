# Observability assets

- `dashboard.json` — Grafana dashboard (20 panels), provisioned by `docker/grafana/provisioning` in the
  `observability` profile (`make observability-up`, http://localhost:3000).
- `alerts.yml` — Prometheus alert rules (mounted by the profile; see `docs/SLO.md`).
- `grafana-dashboard.png` — dashboard after `make demo-scenarios` on the offline stack (LLM_PROVIDER=fake). The
  "LLM latency" and "circuit breakers" panels read "No data" there because those series are emitted by the real
  MiniMax/Tavily clients; they populate when the stack runs MiniMax-M3.
- `jaeger-trace-workflow1-parallel.png` — settlement question: `agent.customer_support` (tool spans) and
  `agent.knowledge` (rag spans) run concurrently in one superstep.
- `jaeger-trace-workflow2-dependency.png` — connectivity question: Customer Support (terminals, statuses, today's
  sales) → supervisor → Knowledge with the Get Smart filter (rag.embed → rag.vector_search → rag.rerank).
- `prometheus-alerts.png` — alert rules loaded in Prometheus.
