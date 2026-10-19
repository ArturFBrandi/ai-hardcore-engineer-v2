"""Sanitized debug traces in Redis (shared across replicas, TTL-bound; development only).

Stores decisions, input/output summaries, scores and latencies — never secrets, raw tool payloads with
PII, prompts or chain-of-thought.
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.guardrails.redaction import redact_text
from app.observability.logging import get_logger

log = get_logger(__name__)
_DROP_KEYS = {"text", "content", "args"}


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {k: ("[omitted]" if k in _DROP_KEYS else _sanitize(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v, depth + 1) for v in value[:50]]
    if isinstance(value, str):
        return redact_text(value)[:500]
    return value


class TraceStore:
    def __init__(self, redis: Redis, ttl_seconds: int, enabled: bool) -> None:
        self.redis = redis
        self.ttl = ttl_seconds
        self.enabled = enabled

    async def save(
        self, request_id: str, trace_id: str, state: dict[str, Any], usage: dict[str, Any], latency_ms: float
    ) -> None:
        if not self.enabled:
            return
        doc = {
            "request_id": request_id,
            "trace_id": trace_id,
            "conversation_id": state.get("conversation_id"),
            "intent": state.get("intent"),
            "route": state.get("route"),
            "route_source": state.get("route_source"),
            "status": state.get("status"),
            "confidence": state.get("confidence"),
            "signals": state.get("signals"),
            "escalation_reasons": state.get("escalation_reasons"),
            "plan": [
                {
                    "id": s["id"],
                    "agent": s["agent"],
                    "objective": s.get("objective"),
                    "depends_on": s.get("depends_on"),
                    "origin": s.get("origin"),
                }
                for s in (state.get("plan") or {}).get("steps", [])
            ],
            "steps": _sanitize(list(state.get("events") or [])),
            "cost": {"agent": "cost", "event": "tokens", "detail": usage},
            "latency_ms": round(latency_ms, 1),
        }
        try:
            await self.redis.set(f"trace:{request_id}", json.dumps(doc, default=str), ex=self.ttl)
        except RedisError:
            log.warning("trace_store_unavailable", request_id=request_id)

    async def get(self, request_id: str) -> dict[str, Any] | None:
        raw = await self.redis.get(f"trace:{request_id}")
        return json.loads(raw) if raw else None
