"""Ingestion pipeline: Official URLs → crawl (static | optional JS render) → HTML/PDF extraction →
noise removal → normalization → structure-aware chunking → metadata → dense + sparse embeddings → Qdrant.

Usage:
    python -m ingestion.pipeline crawl              # live crawl, incremental (make ingest)
    python -m ingestion.pipeline crawl --recreate   # rebuild the collection (make reindex)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.config import Settings, get_settings
from app.db.base import Database
from app.db.models import KBDocument
from app.observability import metrics
from app.observability.logging import configure_logging, get_logger
from app.rag.models import RetrievalModels
from app.rag.qdrant_store import QdrantStore
from ingestion.chunker import Chunk, Classifier, chunk_document
from ingestion.crawler import Crawler, FetchResult, SourceCatalogue
from ingestion.extract import ExtractedDocument, extract_html, remove_boilerplate
from ingestion.pdf import extract_pdf
from ingestion.render import render_available, render_html

log = get_logger(__name__)
SOURCES = Path(__file__).with_name("sources.yaml")
REPORT = Path("data/ingest-report.json")


@dataclass
class IngestReport:
    started_at: str
    pages_fetched: int = 0
    html_pages: int = 0
    pdf_documents: int = 0
    unchanged: int = 0
    rendered_pages: int = 0
    render_requested: int = 0
    render_unavailable: int = 0
    documents_indexed: int = 0
    chunks_total: int = 0
    chunks_upserted: int = 0
    chunks_deleted: int = 0
    boilerplate_blocks_removed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    categories: dict[str, int] = field(default_factory=dict)
    products: dict[str, int] = field(default_factory=dict)
    stale_documents: int = 0
    finished_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


async def index_chunks(
    chunks: list[Chunk], models: RetrievalModels, store: QdrantStore, *, recreate: bool = False
) -> int:
    dim = await asyncio.to_thread(lambda: models.dense_dim)
    await store.ensure_collection(dim, recreate=recreate)
    existing_dim = await store.dense_dim()
    if existing_dim is not None and existing_dim != dim:
        raise RuntimeError(f"collection dim {existing_dim} != model dim {dim}; run make reindex")
    texts = [c.text for c in chunks]
    dense = await asyncio.to_thread(models.embed_passages, texts)
    sparse = await asyncio.to_thread(models.sparse_passages, texts)
    payloads = [{**c.metadata, "text": c.text} for c in chunks]
    await store.upsert([c.id for c in chunks], dense, sparse, payloads)
    return len(chunks)


def _extract(result: FetchResult, html_override: str | None = None) -> ExtractedDocument:
    if result.is_pdf:
        title = f"Manual {result.seed.product}" if result.seed and result.seed.product else None
        return extract_pdf(result.body, result.final_url, title_hint=title)
    html = html_override or result.body.decode("utf-8", errors="replace")
    return extract_html(html, result.final_url)


async def run_crawl(settings: Settings, *, recreate: bool = False, render: bool | None = None) -> IngestReport:
    cat = SourceCatalogue.load(str(SOURCES))
    classifier = Classifier(cat.categories, cat.products)
    report = IngestReport(started_at=datetime.now(UTC).isoformat())
    db = Database(settings)
    store = QdrantStore(settings.qdrant_url, settings.qdrant_collection, settings.qdrant_timeout_seconds)
    models = RetrievalModels(settings, offline=os.environ.get("MODELS_OFFLINE", "false").lower() == "true")
    render = render_available() if render is None else render
    try:
        if recreate:
            async with db.transaction() as s:
                await s.execute(delete(KBDocument))
        async with db.session() as s:
            rows = (await s.scalars(select(KBDocument))).all()
        known = {} if recreate else {r.url: {"etag": r.etag, "last_modified": r.last_modified} for r in rows}
        if not recreate and await store.count() == 0:
            known = {}  # empty collection → full fetch even if crawl state exists
        crawler = Crawler(
            cat,
            user_agent=settings.ingest_user_agent,
            rate_limit_seconds=settings.ingest_rate_limit_seconds,
            timeout=settings.ingest_timeout_seconds,
            known=known,
        )
        extracted: dict[str, ExtractedDocument] = {}

        rendered_urls: set[str] = set()

        async def links(result: FetchResult) -> list[str]:
            doc = _extract(result)
            if result.seed and result.seed.render and render and not result.is_pdf:
                html = await render_html(result.final_url, settings.ingest_user_agent)
                if html:
                    rendered_doc = _extract(result, html)
                    rendered_urls.add(result.url)
                    extracted[result.url] = rendered_doc
                    return sorted(set(doc.links) | set(rendered_doc.links))
            extracted[result.url] = doc
            return doc.links

        try:
            results = await crawler.crawl(links)
        finally:
            await crawler.aclose()
        report.skipped = dict(Counter(reason.split(":")[0] for _, reason in crawler.skipped))
        report.pages_fetched = len(results)

        docs: list[tuple[FetchResult, ExtractedDocument, bool]] = []
        now = datetime.now(UTC)
        unchanged_urls: list[str] = []
        for r in results:
            if r.not_modified:
                report.unchanged += 1
                unchanged_urls.append(r.url)
                continue
            rendered = r.url in rendered_urls
            if r.seed and r.seed.render and not r.is_pdf:
                report.render_requested += 1
                if not rendered and render and r.depth >= cat.max_depth:
                    html = await render_html(r.final_url, settings.ingest_user_agent)
                    if html:
                        extracted[r.url] = _extract(r, html)
                        rendered = True
                if rendered:
                    report.rendered_pages += 1
                else:
                    report.render_unavailable += 1
            doc = extracted.get(r.url) or _extract(r)
            docs.append((r, doc, rendered))
            if r.is_pdf:
                report.pdf_documents += 1
            else:
                report.html_pages += 1
        report.boilerplate_blocks_removed = remove_boilerplate([d for _, d, _ in docs])

        all_chunks: list[Chunk] = []
        per_doc: dict[str, list[Chunk]] = {}
        for r, doc, rendered in docs:
            seed = r.seed
            chunks = chunk_document(
                doc,
                category=seed.category if seed else "faq",
                language=(doc.language or (seed.language if seed else "pt")),
                classifier=classifier,
                page_product=seed.product if seed else None,
                retrieved_at=r.fetched_at,
                etag=r.etag,
                last_modified=r.last_modified,
                rendered=rendered,
            )
            if not chunks:
                continue
            per_doc[doc.canonical_url] = chunks
            all_chunks.extend(chunks)
        # Dedup identical bodies across pages (FAQ hubs repeat topic-page answers): keep the most
        # specific source (longest URL path), so citations point at the topic page.
        unique: dict[str, Chunk] = {}
        for c in sorted(all_chunks, key=lambda c: -len(c.metadata["canonical_url"])):
            unique.setdefault(c.metadata["body_hash"], c)
        for url in list(per_doc):
            per_doc[url] = [c for c in per_doc[url] if unique.get(c.metadata["body_hash"]) is c]
        report.chunks_total = len(unique)
        report.categories = dict(Counter(c.metadata["category"] for c in unique.values()))
        report.products = dict(Counter(c.metadata["product"] for c in unique.values() if c.metadata["product"]))
        if unique:
            report.chunks_upserted = await index_chunks(list(unique.values()), models, store, recreate=recreate)
        for url, chunks in per_doc.items():
            report.chunks_deleted += await store.delete_by_url(url, {c.metadata["content_hash"] for c in chunks})
        report.documents_indexed = len(per_doc)

        async with db.transaction() as s:
            for r, doc, rendered in docs:
                chunks = per_doc.get(doc.canonical_url, [])
                values = {
                    "url": r.url,
                    "canonical_url": doc.canonical_url,
                    "title": doc.title[:300],
                    "category": (r.seed.category if r.seed else "faq")[:48],
                    "language": doc.language,
                    "content_type": r.content_type[:64],
                    "etag": r.etag,
                    "last_modified": r.last_modified,
                    "doc_hash": chunks[0].metadata["doc_hash"] if chunks else "",
                    "chunk_count": len(chunks),
                    "rendered": rendered,
                    "last_crawled_at": now,
                    "last_changed_at": now,
                }
                stmt = insert(KBDocument).values(**values)
                await s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["url"],
                        set_={k: stmt.excluded[k] for k in values if k != "url"},
                    )
                )
            for url in unchanged_urls:
                doc_row = await s.get(KBDocument, url)
                if doc_row is not None:
                    doc_row.last_crawled_at = now
            stale_before = now - timedelta(days=cat.freshness_days)
            all_rows = (await s.scalars(select(KBDocument))).all()
            report.stale_documents = sum(1 for d in all_rows if d.last_crawled_at < stale_before)
        metrics.RAG_STALE_DOCUMENTS.set(report.stale_documents)
        report.finished_at = datetime.now(UTC).isoformat()
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
        return report
    finally:
        await store.close()
        await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["crawl"])
    parser.add_argument("--recreate", action="store_true", help="drop and rebuild the collection")
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    report = asyncio.run(run_crawl(settings, recreate=args.recreate, render=False if args.no_render else None))
    print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
