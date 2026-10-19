"""Hybrid retriever: dense + BM25 → RRF (Qdrant) → dedup → cross-encoder rerank → thresholds → evidence.

Pipeline: retrieve ``RAG_TOP_K`` (15) fused candidates (metadata filters applied inside Qdrant) → dedup
by content hash / (url, heading) → rerank all survivors, keep ``RAG_RERANK_TOP_K`` (8) → optional
deterministic product boost → drop below ``RAG_RERANK_MIN_SCORE`` / ``RAG_MIN_SCORE`` → select
``RAG_CONTEXT_TOP_K`` (5) as evidence. Thresholds come from ``make calibrate``.
"""

from __future__ import annotations

import time
from typing import Literal

from app.config import Settings
from app.errors import RetrievalError
from app.observability import metrics
from app.observability.logging import get_logger
from app.rag.models import RetrievalModels
from app.rag.qdrant_store import QdrantStore
from app.rag.schemas import RetrievalFilters, RetrievalResult, RetrievedDocument

log = get_logger(__name__)

Mode = Literal["hybrid", "dense", "sparse"]


class Retriever:
    def __init__(self, settings: Settings, models: RetrievalModels, store: QdrantStore) -> None:
        self.s = settings
        self.models = models
        self.store = store

    async def retrieve(
        self,
        query: str,
        filters: RetrievalFilters | None = None,
        *,
        mode: Mode = "hybrid",
        rerank: bool = True,
        apply_thresholds: bool = True,
        top_k: int | None = None,
    ) -> RetrievalResult:
        filters = filters or RetrievalFilters()
        k = top_k or self.s.rag_top_k
        t0 = time.perf_counter()
        metrics.RAG_QUERIES.labels(mode=mode).inc()
        try:
            dense = await self.models.aembed_query(query) if mode != "sparse" else []
            sparse = await self.models.asparse_query(query) if mode != "dense" else None
            t_embed = time.perf_counter()
            flt = QdrantStore.build_filter(filters.product, filters.category)
            if mode == "hybrid":
                assert sparse is not None  # noqa: S101 - mode invariant
                points = await self.store.hybrid(dense, sparse, k, flt)
            elif mode == "dense":
                points = await self.store.dense_only(dense, k, flt)
            else:
                assert sparse is not None  # noqa: S101
                points = await self.store.sparse_only(sparse, k, flt)
        except Exception as exc:  # qdrant/http/model errors → explicit retrieval error
            metrics.RAG_DURATION.labels(stage="failed").observe(time.perf_counter() - t0)
            log.error("retrieval_failed", error=type(exc).__name__)
            raise RetrievalError(f"retrieval failed: {type(exc).__name__}") from exc
        t_search = time.perf_counter()

        docs: list[RetrievedDocument] = []
        seen: set[str] = set()
        for p in points:
            pl = p.payload or {}
            key = (
                str(pl.get("content_hash") or "")
                or f"{pl.get('canonical_url')}|{pl.get('heading')}|{pl.get('chunk_index')}"
            )
            if key in seen:
                continue
            seen.add(key)
            docs.append(
                RetrievedDocument(
                    chunk_id=str(p.id),
                    text=str(pl.get("text", "")),
                    title=str(pl.get("title", "")),
                    url=str(pl.get("canonical_url") or pl.get("url", "")),
                    heading=str(pl.get("heading", "")),
                    category=str(pl.get("category", "")),
                    product=pl.get("product"),
                    products=list(pl.get("products") or []),
                    language=pl.get("language"),
                    retrieved_at=pl.get("retrieved_at"),
                    fusion_score=float(p.score),
                )
            )
        candidates = len(points)
        deduped = len(docs)
        t_rerank_start = time.perf_counter()
        if rerank and docs:
            # Rerank budget (ADR 006, evaluation/results/rerank_budget.md): only the top RAG_RERANK_TOP_K fused
            # candidates, passages truncated to RAG_RERANK_MAX_CHARS — same Recall@5 as reranking all 15, ~2.5x faster.
            head, tail = docs[: self.s.rag_rerank_top_k], docs[self.s.rag_rerank_top_k :]
            try:
                scores = await self.models.arerank(query, [d.text[: self.s.rag_rerank_max_chars] for d in head])
            except Exception as exc:  # model missing/corrupt → explicit retrieval error, never a crashed node
                log.error("rerank_failed", error=type(exc).__name__)
                raise RetrievalError(f"rerank failed: {type(exc).__name__}") from exc
            for d, sc in zip(head, scores, strict=True):
                d.rerank_score = sc
            docs = head + tail
            if self.s.rag_product_boost and filters.product is None:
                low = query.lower()
                for d in docs:
                    if any(p.lower() in low for p in d.products):
                        d.rerank_score = (d.rerank_score or 0.0) + self.s.rag_product_boost
            docs = sorted(head, key=lambda d: d.rerank_score or 0.0, reverse=True)
        t_rerank = time.perf_counter()
        reranked = len(docs)
        if apply_thresholds:
            docs = [d for d in docs if d.fusion_score >= self.s.rag_min_score]
            if rerank:
                docs = [d for d in docs if (d.rerank_score or 0.0) >= self.s.rag_rerank_min_score]
        selected = docs[: self.s.rag_context_top_k]
        if not selected:
            metrics.RAG_NO_RESULTS.inc()
        metrics.RAG_DURATION.labels(stage="embed").observe(t_embed - t0)
        metrics.RAG_DURATION.labels(stage="search").observe(t_search - t_embed)
        metrics.RAG_DURATION.labels(stage="rerank").observe(t_rerank - t_rerank_start)
        metrics.RAG_DURATION.labels(stage="total").observe(time.perf_counter() - t0)
        diagnostics = {
            "mode": mode,
            "candidates": candidates,
            "deduped": deduped,
            "reranked": reranked,
            "selected": len(selected),
            "top_fusion_score": round(max((d.fusion_score for d in selected), default=0.0), 4),
            "top_rerank_score": round(max((d.rerank_score or 0.0 for d in selected), default=0.0), 4)
            if selected
            else None,
            "filters": filters.model_dump(exclude_none=True),
            "latency_ms": {
                "embed": round((t_embed - t0) * 1000, 1),
                "search": round((t_search - t_embed) * 1000, 1),
                "rerank": round((t_rerank - t_rerank_start) * 1000, 1),
                "total": round((time.perf_counter() - t0) * 1000, 1),
            },
        }
        return RetrievalResult(query=query, documents=selected, filters=filters, diagnostics=diagnostics)
