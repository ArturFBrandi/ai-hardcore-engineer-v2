# Service level objectives

Measured from Prometheus (`/metrics` on every API replica and the worker; scraped by the `observability` profile).
Alert rules: `docs/observability/alerts.yml`. Dashboard: `docs/observability/dashboard.json`.

| SLO | Objective (30-day) | SLI / query | Alert |
|---|---|---|---|
| Availability | 99.5% of `/v1/*` requests are not 5xx | `1 - sum(rate(http_requests_total{code=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` | `HighErrorRate` (>5% for 5 min) |
| Latency | p95 chat latency < 20 s (MiniMax-bound; see README performance table) | `histogram_quantile(0.95, sum by (le) (rate(agent_request_duration_seconds_bucket[5m])))` | `HighP95ChatLatency` |
| Dependency health | breakers closed | `max by (dependency) (circuit_breaker_state)` | `CircuitBreakerOpen` |
| LLM reliability | < 20% failed LLM calls | `llm_failures_total / llm_requests_total` | `LLMFailureRate` |
| Retrieval quality | < 40% of RAG queries without evidence | `rag_no_results_total / rag_queries_total` | `RagNoResultsRatioHigh` |
| Escalation load | < 30% of chats escalate | `agent_escalations_total / agent_requests_total` | `EscalationRateSpike` |
| Human response | 100% of cases get an operator action within the SLA (urgent 15 min, high 30, normal 120) | `hitl_sla_breaches_total`, `hitl_time_to_human_reply_seconds` | `HitlSlaBreach` |
| Notifications | no dead-lettered operator notifications | `outbox_deliveries_total{status="dead"}` | `OutboxDeadLetters` |
| Cost | < USD 2/hour LLM spend at expected load | `rate(llm_cost_usd_total[1h]) * 3600` | `LLMCostSpike` |
| Answer quality | offline gates (`evaluation/thresholds.yaml`) never regress; live eval weekly | `make eval --strict` in CI, `live.yml` | CI failure |
