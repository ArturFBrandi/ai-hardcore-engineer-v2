"""External web search behind ``WebSearchProvider`` (Tavily implementation, ADR 005).

Results are untrusted evidence. Failures are explicit (``ExternalSearchError``) so the Knowledge agent
can degrade honestly ("I cannot retrieve current external information right now") — never fabricate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
from opentelemetry import trace

from app.config import Settings, WebSearchProviderName
from app.errors import ExternalSearchError
from app.llm.reliability import CircuitBreaker, CircuitOpenError, with_retries
from app.observability import metrics
from app.observability.logging import get_logger

log = get_logger(__name__)
_tracer = trace.get_tracer("getnet.web")


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    content: str
    score: float | None
    retrieved_at: str


@dataclass(frozen=True)
class WebSearchResponse:
    query: str
    results: list[WebResult]
    answer: str | None
    latency_ms: float


class WebSearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, max_results: int = 5, topic: str = "general") -> WebSearchResponse: ...


class DisabledWebSearch:
    name = "disabled"

    async def search(self, query: str, *, max_results: int = 5, topic: str = "general") -> WebSearchResponse:
        metrics.WEB_SEARCH_FAILURES.labels(reason="disabled").inc()
        raise ExternalSearchError("web search is not configured")


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, httpx.TransportError)


class TavilySearch:
    name = "tavily"
    URL = "https://api.tavily.com/search"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        key = settings.web_search_api_key.get_secret_value()
        if not key:
            raise ExternalSearchError("WEB_SEARCH_API_KEY is not set")
        self._key = key
        self._timeout = settings.web_search_timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        self.breaker = CircuitBreaker("web_search", failure_threshold=4, reset_seconds=60)

    async def search(self, query: str, *, max_results: int = 5, topic: str = "general") -> WebSearchResponse:
        started = time.perf_counter()

        async def _call() -> httpx.Response:
            resp = await self._client.post(
                self.URL,
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "query": query[:400],
                    "max_results": max_results,
                    "include_answer": True,
                    "topic": topic,
                    "search_depth": "basic",
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp

        try:
            with _tracer.start_as_current_span("web_search"):
                resp = await with_retries(
                    _call, is_retryable=_retryable, max_retries=1, breaker=self.breaker, name="web_search"
                )
        except CircuitOpenError as exc:
            metrics.WEB_SEARCH_FAILURES.labels(reason="circuit_open").inc()
            raise ExternalSearchError("web search circuit open") from exc
        except httpx.HTTPStatusError as exc:
            metrics.WEB_SEARCH_FAILURES.labels(reason=f"http_{exc.response.status_code}").inc()
            metrics.WEB_SEARCH_REQUESTS.labels(status="error").inc()
            raise ExternalSearchError(f"web search HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            metrics.WEB_SEARCH_FAILURES.labels(reason=type(exc).__name__).inc()
            metrics.WEB_SEARCH_REQUESTS.labels(status="error").inc()
            raise ExternalSearchError(f"web search failed: {type(exc).__name__}") from exc
        data = resp.json()
        now = datetime.now(UTC).isoformat()
        results = [
            WebResult(
                title=str(r.get("title", ""))[:200],
                url=str(r.get("url", "")),
                content=str(r.get("content", ""))[:1500],
                score=r.get("score"),
                retrieved_at=now,
            )
            for r in data.get("results", [])
            if r.get("url")
        ]
        latency = (time.perf_counter() - started) * 1000
        metrics.WEB_SEARCH_REQUESTS.labels(status="ok").inc()
        log.info("web_search", provider=self.name, results=len(results), latency_ms=round(latency))
        return WebSearchResponse(query=query, results=results, answer=data.get("answer"), latency_ms=round(latency, 1))


def build_web_search(settings: Settings) -> WebSearchProvider:
    if settings.web_search_provider is WebSearchProviderName.DISABLED:
        return DisabledWebSearch()
    try:
        return TavilySearch(settings)
    except ExternalSearchError:
        log.warning("web_search_disabled", reason="missing WEB_SEARCH_API_KEY")
        return DisabledWebSearch()
