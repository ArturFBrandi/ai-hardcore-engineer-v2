"""Retry with exponential backoff + jitter, and a circuit breaker.

Only retryable failures (429, timeouts, connection errors, selected 5xx) are retried; validation and
auth failures are never retried. Breaker state is per process and exported as a metric — it is a
protection mechanism, not correctness-relevant state (each replica independently sheds load).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from enum import IntEnum

from app.observability import metrics
from app.observability.logging import get_logger

log = get_logger(__name__)


class BreakerState(IntEnum):
    CLOSED = 0
    HALF_OPEN = 1
    OPEN = 2


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._export()

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._clock() - self._opened_at >= self.reset_seconds:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def before_call(self) -> None:
        if self.state is BreakerState.OPEN:
            raise CircuitOpenError(f"circuit {self.name} open")

    def on_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._export()

    def on_failure(self) -> None:
        self._failures += 1
        if self.state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
            if self._opened_at is None or self.state is BreakerState.HALF_OPEN:
                log.warning("circuit_opened", dependency=self.name, failures=self._failures)
            self._opened_at = self._clock()
        self._export()

    def _export(self) -> None:
        metrics.CIRCUIT_BREAKER_STATE.labels(dependency=self.name).set(int(self.state))


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Full-jitter exponential backoff."""
    return random.uniform(0, min(cap, base * (2**attempt)))  # noqa: S311 - jitter, not crypto


async def with_retries[T](
    call: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[BaseException], bool],
    max_retries: int,
    breaker: CircuitBreaker | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    name: str = "dependency",
) -> T:
    attempt = 0
    while True:
        if breaker is not None:
            breaker.before_call()
        try:
            result = await call()
        except BaseException as exc:
            retryable = is_retryable(exc)
            if breaker is not None and retryable:
                breaker.on_failure()
            if not retryable or attempt >= max_retries:
                raise
            delay = backoff_delay(attempt)
            log.warning(
                "retrying", dependency=name, attempt=attempt + 1, delay_s=round(delay, 2), error=type(exc).__name__
            )
            await sleep(delay)
            attempt += 1
            continue
        if breaker is not None:
            breaker.on_success()
        return result
