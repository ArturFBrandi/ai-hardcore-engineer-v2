"""Shared (multi-replica) request protection in Redis: sliding-window rate limiting and idempotency keys.

Fail policy (documented in the README / threat model):
* rate limiting — customer traffic fails OPEN when Redis is down (``RATE_LIMIT_FAIL_OPEN=true``, metric
  ``rate_limit_backend_errors_total{policy="open"}``) to keep chat available; operator writes always fail
  CLOSED (503).
* idempotency — a request carrying ``Idempotency-Key`` fails CLOSED (503) when Redis is down, because
  exactly-once cannot be guaranteed.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.errors import ConflictError, DependencyUnavailableError, RateLimitedError
from app.observability import metrics
from app.observability.logging import get_logger

log = get_logger(__name__)

_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, tonumber(oldest[2])}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, window)
return {1, 0}
"""


class RateLimiter:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.s = settings
        self._script = redis.register_script(_SLIDING_WINDOW)

    async def _hit(self, key: str, limit: int, scope: str, fail_open: bool, window_ms: int = 60_000) -> None:
        now = int(time.time() * 1000)
        try:
            allowed, oldest = await self._script(
                keys=[key], args=[now, window_ms, limit, f"{now}-{uuid.uuid4().hex[:8]}"]
            )
        except RedisError as exc:
            metrics.RATE_LIMIT_BACKEND_ERRORS.labels(policy="open" if fail_open else "closed").inc()
            if fail_open:
                log.warning("rate_limiter_fail_open", scope=scope)
                return
            raise DependencyUnavailableError("rate limiter unavailable") from exc
        if not int(allowed):
            retry_after = max(1, math.ceil((int(oldest) + window_ms - now) / 1000))
            metrics.RATE_LIMITED.labels(scope=scope).inc()
            raise RateLimitedError(retry_after, f"rate limit exceeded; retry in {retry_after}s")

    async def check_ip(self, ip: str) -> None:
        await self._hit(f"rl:ip:{ip}", self.s.rate_limit_ip_per_minute, "ip", fail_open=True)

    async def check_subject(self, subject: str, operator_write: bool = False) -> None:
        if operator_write:
            await self._hit(
                f"rl:opw:{subject}", self.s.rate_limit_operator_writes_per_minute, "operator_write", fail_open=False
            )
        else:
            await self._hit(
                f"rl:sub:{subject}", self.s.rate_limit_per_minute, "subject", fail_open=self.s.rate_limit_fail_open
            )


class IdempotencyStore:
    """``Idempotency-Key`` bound to the authenticated subject (a key replayed by another subject is a new
    key in a different namespace — never a cross-customer replay)."""

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.ttl = settings.idempotency_ttl_seconds
        self.in_flight_ttl = int(settings.request_timeout_seconds) + 30

    @staticmethod
    def _key(subject: str, key: str) -> str:
        digest = hashlib.sha256(f"{subject}\x00{key}".encode()).hexdigest()
        return f"idem:{digest}"

    @staticmethod
    def fingerprint(body: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    async def begin(self, subject: str, key: str, fingerprint: str) -> dict[str, Any] | None:
        """Return a stored response to replay, or None if this caller should process the request."""
        rkey = self._key(subject, key)
        try:
            claimed = await self.redis.set(
                rkey, json.dumps({"state": "in_progress", "fp": fingerprint}), nx=True, ex=self.in_flight_ttl
            )
            if claimed:
                return None
            raw = await self.redis.get(rkey)
        except RedisError as exc:
            raise DependencyUnavailableError("idempotency store unavailable") from exc
        if raw is None:
            return await self.begin(subject, key, fingerprint)
        doc = json.loads(raw)
        if doc.get("fp") != fingerprint:
            raise ConflictError("Idempotency-Key reused with a different request body")
        if doc.get("state") == "in_progress":
            raise ConflictError("a request with this Idempotency-Key is still in progress")
        metrics.IDEMPOTENT_REPLAYS.inc()
        response: dict[str, Any] = doc["response"]
        return response

    async def complete(self, subject: str, key: str, fingerprint: str, response: dict[str, Any]) -> None:
        try:
            await self.redis.set(
                self._key(subject, key),
                json.dumps({"state": "done", "fp": fingerprint, "response": response}, default=str),
                ex=self.ttl,
            )
        except RedisError:
            log.warning("idempotency_store_write_failed")

    async def abort(self, subject: str, key: str) -> None:
        try:
            await self.redis.delete(self._key(subject, key))
        except RedisError:
            log.warning("idempotency_store_abort_failed")
