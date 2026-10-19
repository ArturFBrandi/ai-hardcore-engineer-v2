"""Polite, bounded crawler for the official Getnet domains.

* robots.txt honoured per host (missing robots.txt → allowed, per RFC 9309).
* Per-host rate limit, timeouts, max depth/pages, allow/deny patterns from ``sources.yaml``.
* Conditional requests with stored ETag / Last-Modified → ``304`` documents are reused unchanged.
* Only ``text/html`` and ``application/pdf`` are accepted; everything else is skipped and reported.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib import robotparser
from urllib.parse import urlparse, urlunparse

import httpx
import yaml

from app.observability.logging import get_logger

log = get_logger(__name__)

MAX_BYTES = 12 * 1024 * 1024


@dataclass
class Seed:
    url: str
    category: str
    language: str
    product: str | None = None
    render: bool = False


@dataclass
class SourceCatalogue:
    seeds: list[Seed]
    allowed_domains: list[str]
    allow: list[re.Pattern[str]]
    deny: list[re.Pattern[str]]
    max_depth: int
    max_pages: int
    freshness_days: int
    categories: list[dict[str, Any]]
    products: list[dict[str, Any]]
    discovered: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> SourceCatalogue:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return cls(
            seeds=[
                Seed(
                    url=s["url"],
                    category=s.get("category", "faq"),
                    language=s.get("language", "pt"),
                    product=s.get("product"),
                    render=s.get("render") == "js",
                )
                for s in raw["seeds"]
            ],
            allowed_domains=list(raw["allowed_domains"]),
            allow=[re.compile(p) for p in raw.get("allow", [])],
            deny=[re.compile(p) for p in raw.get("deny", [])],
            max_depth=int(raw.get("max_depth", 2)),
            max_pages=int(raw.get("max_pages", 200)),
            freshness_days=int(raw.get("freshness_days", 14)),
            categories=list(raw.get("categories", [])),
            discovered=[str(u) for u in raw.get("discovered_seeds", [])],
            products=list(raw.get("products", [])),
        )

    def accepts(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self.allowed_domains:
            return False
        if any(p.search(url) for p in self.deny):
            return False
        return any(p.search(url) for p in self.allow)


def normalize_url(url: str) -> str:
    p = urlparse(url)
    path = re.sub(r"/{2,}", "/", p.path or "/")
    return urlunparse((p.scheme, p.netloc.lower(), path, "", "", ""))


def url_key(url: str) -> str:
    """Slash-insensitive identity used for de-duplication."""
    return normalize_url(url).rstrip("/")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes = b""
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    depth: int = 0
    seed: Seed | None = None
    not_modified: bool = False
    error: str | None = None

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type or self.final_url.lower().endswith(".pdf")


class Crawler:
    def __init__(
        self,
        catalogue: SourceCatalogue,
        *,
        user_agent: str,
        rate_limit_seconds: float = 1.0,
        timeout: float = 20.0,
        known: dict[str, dict[str, str | None]] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.cat = catalogue
        self.ua = user_agent
        self.rate = rate_limit_seconds
        self.known = known or {}
        self._client = client or httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": user_agent}
        )
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self.skipped: list[tuple[str, str]] = []

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _allowed_by_robots(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self._robots:
            rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
            try:
                resp = await self._client.get(f"https://{host}/robots.txt")
                if resp.status_code == 200 and "text/plain" in resp.headers.get("content-type", ""):
                    assert rp is not None  # noqa: S101 - narrowing
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None  # no robots.txt → everything allowed
            except httpx.HTTPError:
                rp = None
            self._robots[host] = rp
        parser = self._robots[host]
        return True if parser is None else parser.can_fetch(self.ua, url)

    async def _throttle(self, host: str) -> None:
        wait = self.rate - (time.monotonic() - self._last_hit.get(host, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_hit[host] = time.monotonic()

    async def fetch(self, url: str, depth: int = 0, seed: Seed | None = None) -> FetchResult:
        host = urlparse(url).netloc
        await self._throttle(host)
        headers: dict[str, str] = {}
        prior = self.known.get(url) or {}
        if prior.get("etag"):
            headers["If-None-Match"] = str(prior["etag"])
        if prior.get("last_modified"):
            headers["If-Modified-Since"] = str(prior["last_modified"])
        try:
            resp = await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            return FetchResult(url, url, 0, "", depth=depth, seed=seed, error=type(exc).__name__)
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        result = FetchResult(
            url=url,
            final_url=normalize_url(str(resp.url)),
            status=resp.status_code,
            content_type=ctype,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            depth=depth,
            seed=seed,
        )
        if resp.status_code == 304:
            result.not_modified = True
            result.etag = result.etag or prior.get("etag")
            result.last_modified = result.last_modified or prior.get("last_modified")
            return result
        if resp.status_code != 200:
            result.error = f"http_{resp.status_code}"
            return result
        if ctype not in ("text/html", "application/pdf", "application/xhtml+xml"):
            result.error = f"content_type:{ctype}"
            return result
        if len(resp.content) > MAX_BYTES:
            result.error = "too_large"
            return result
        result.body = resp.content
        return result

    async def crawl(self, link_extractor: Any) -> list[FetchResult]:
        """BFS from the seeds. ``link_extractor(result) -> list[str]`` supplies outgoing links."""
        queue: list[tuple[str, int, Seed]] = [(normalize_url(s.url), 0, s) for s in self.cat.seeds]
        # Discovered seeds (found by the JS render profile) are fetched but not expanded further.
        queue += [(normalize_url(u), self.cat.max_depth, Seed(u, "faq", "pt")) for u in self.cat.discovered]
        seen: set[str] = set()
        results: list[FetchResult] = []
        while queue and len(results) < self.cat.max_pages:
            url, depth, seed = queue.pop(0)
            if url_key(url) in seen:
                continue
            seen.add(url_key(url))
            is_seed = depth == 0 or url in {normalize_url(u) for u in self.cat.discovered}
            if not is_seed and not self.cat.accepts(url):
                continue
            if not await self._allowed_by_robots(url):
                self.skipped.append((url, "robots"))
                continue
            result = await self.fetch(url, depth, seed)
            if result.error:
                self.skipped.append((url, result.error))
                log.info("crawl_skip", url=url, reason=result.error)
                continue
            if url_key(result.final_url) != url_key(url) and url_key(result.final_url) in seen:
                continue
            seen.add(url_key(result.final_url))
            results.append(result)
            log.info("crawl_ok", url=url, depth=depth, status=result.status, bytes=len(result.body))
            if depth < self.cat.max_depth and not result.is_pdf and not result.not_modified:
                found = link_extractor(result)
                if inspect.isawaitable(found):
                    found = await found
                for link in found:
                    norm = normalize_url(link)
                    if url_key(norm) not in seen and self.cat.accepts(norm):
                        child_seed = Seed(norm, seed.category, seed.language, None, False)
                        queue.append((norm, depth + 1, child_seed))
        return results
