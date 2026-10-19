"""Test doubles (only used by tests)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.errors import ExternalSearchError
from app.services.web_search import WebResult, WebSearchResponse


class FakeWebSearch:
    name = "fake"

    def __init__(self) -> None:
        self.down = False
        self.queries: list[str] = []

    async def search(self, query: str, *, max_results: int = 5, topic: str = "general") -> WebSearchResponse:
        self.queries.append(query)
        if self.down:
            raise ExternalSearchError("web search down (test)")
        now = datetime.now(UTC).isoformat()
        q = query.lower()
        if "euro" in q or "câmbio" in q or "exchange" in q or "cotação" in q:
            results = [
                WebResult(
                    "EUR BRL - Exchange rate",
                    "https://example-fx.test/eur-brl",
                    "1 EUR = 5.68 BRL (test fixture rate)",
                    0.9,
                    now,
                )
            ]
        else:
            results = [
                WebResult(
                    "Porto Alegre weather",
                    "https://example-weather.test/poa",
                    "Tomorrow in Porto Alegre: 24°C, light rain (test fixture forecast)",
                    0.9,
                    now,
                )
            ]
        return WebSearchResponse(query=query, results=results, answer=results[0].content, latency_ms=5.0)
