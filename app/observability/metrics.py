"""Prometheus metrics (low-cardinality labels only — never request/customer ids).

Each API/worker process exposes its own registry on ``/metrics`` (worker: port 9100); Prometheus
aggregates across replicas, so no correctness-relevant state lives in process memory.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_LAT = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120)

REQUESTS = Counter("agent_requests_total", "Chat requests", ["status", "intent"])
REQUEST_DURATION = Histogram("agent_request_duration_seconds", "End-to-end chat latency", ["status"], buckets=_LAT)
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "code"])
ROUTES = Counter("agent_routes_total", "Router decisions", ["source", "intent"])
SUPERVISOR_STEPS = Counter("supervisor_steps_total", "Supervisor decisions", ["decision", "reason"])
AGENT_DURATION = Histogram("agent_node_duration_seconds", "Graph node latency", ["node"], buckets=_LAT)

TOOL_CALLS = Counter("agent_tool_calls_total", "Tool invocations", ["tool", "status"])
TOOL_FAILURES = Counter("agent_tool_failures_total", "Tool failures", ["tool", "reason"])

RAG_QUERIES = Counter("rag_queries_total", "RAG queries", ["mode"])
RAG_DURATION = Histogram("rag_query_duration_seconds", "RAG query latency", ["stage"], buckets=_LAT)
RAG_NO_RESULTS = Counter("rag_no_results_total", "RAG queries with no evidence above threshold")
RAG_STALE_DOCUMENTS = Gauge("rag_stale_documents", "Documents whose last successful crawl exceeds the freshness window")

ESCALATIONS = Counter("agent_escalations_total", "Escalations", ["reason"])
HITL_QUEUE = Gauge("hitl_queue_size", "Cases awaiting a human")
HITL_TIME_TO_REPLY = Histogram(
    "hitl_time_to_human_reply_seconds",
    "Escalation to first human action",
    buckets=(60, 300, 900, 1800, 3600, 7200, 86400),
)
HITL_SLA_BREACHES = Counter("hitl_sla_breaches_total", "SLA breaches detected by the worker")
APPROVALS = Counter("approvals_total", "Operator approval decisions", ["decision"])
OUTBOX_DELIVERIES = Counter("outbox_deliveries_total", "Outbox delivery attempts", ["status"])

LLM_REQUESTS = Counter("llm_requests_total", "LLM requests", ["task", "status"])
LLM_FAILURES = Counter("llm_failures_total", "LLM failures", ["task", "reason"])
LLM_DURATION = Histogram("llm_request_duration_seconds", "LLM latency", ["task"], buckets=_LAT)
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["direction"])
LLM_COST = Counter("llm_cost_usd_total", "Estimated LLM cost in USD")
REQUEST_COST = Histogram(
    "agent_request_cost_usd",
    "Estimated LLM cost per chat request",
    buckets=(0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05),
)

WEB_SEARCH_REQUESTS = Counter("web_search_requests_total", "Web search requests", ["status"])
WEB_SEARCH_FAILURES = Counter("web_search_failures_total", "Web search failures", ["reason"])

CIRCUIT_BREAKER_STATE = Gauge("circuit_breaker_state", "0=closed 1=half_open 2=open", ["dependency"])
GUARDRAIL_TRIGGERS = Counter("guardrail_triggers_total", "Guardrail triggers", ["layer", "rule"])
AUTH_FAILURES = Counter("auth_failures_total", "Authentication/authorization failures", ["reason"])
RATE_LIMITED = Counter("rate_limited_total", "Rate-limited requests", ["scope"])
RATE_LIMIT_BACKEND_ERRORS = Counter("rate_limit_backend_errors_total", "Rate limiter backend failures", ["policy"])
MEMORY_SUMMARIES = Counter("memory_summaries_total", "Rolling summaries", ["source"])
MEMORY_OPS = Counter("memory_operations_total", "Memory load/save operations", ["op"])
IDEMPOTENT_REPLAYS = Counter("idempotent_replays_total", "Responses served from the idempotency store")
