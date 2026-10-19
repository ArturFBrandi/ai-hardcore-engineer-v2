"""How many fused candidates to rerank? Quality/latency trade-off on the golden set (ADR 006).

Reranks only the top-N RRF candidates (N in 5, 8, 10, 15), optionally truncating passages, and reports
Recall@5 / MRR@10 / p50 / p95 rerank latency. Writes ``evaluation/results/rerank_budget.md``.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

from app.config import get_settings
from app.rag.models import RetrievalModels
from app.rag.qdrant_store import QdrantStore
from evaluation.benchmark_retrieval import golden_items
from evaluation.metrics import hit_at_k, is_relevant, percentile, reciprocal_rank

OUT = Path("evaluation/results/rerank_budget.md")


async def main_async() -> None:
    s = get_settings()
    models = RetrievalModels(s, offline=False)
    store = QdrantStore(s.qdrant_url, s.qdrant_collection)
    items = golden_items()
    cands = []
    for it in items:
        pts = await store.hybrid(models.embed_query(it["question"]), models.sparse_query(it["question"]), 15, None)
        cands.append([{"url": p.payload["canonical_url"], "text": p.payload["text"]} for p in pts])  # type: ignore[index]
    await store.close()
    models.rerank("warmup", ["warmup"])
    rows = []
    for n in (5, 8, 10, 15):
        for trunc in (None, 700):
            rels, lat = [], []
            for it, docs in zip(items, cands, strict=True):
                head, tail = docs[:n], docs[n:]
                texts = [d["text"][:trunc] if trunc else d["text"] for d in head]
                t0 = time.perf_counter()
                scores = models.rerank(it["question"], texts) if texts else []
                lat.append((time.perf_counter() - t0) * 1000)
                order = [head[k] for k in sorted(range(len(head)), key=lambda k: scores[k], reverse=True)] + tail
                rels.append([is_relevant(d["url"], d["text"], it["relevant"]) for d in order])
            rows.append(
                (
                    n,
                    trunc or "full",
                    statistics.mean(hit_at_k(r, 5) for r in rels),
                    statistics.mean(reciprocal_rank(r, 10) for r in rels),
                    percentile(lat, 50),
                    percentile(lat, 95),
                )
            )
            print(rows[-1], flush=True)
    lines = [
        "# Rerank budget (golden set, hybrid top-15 candidates)",
        "",
        f"Dense: `{s.embedding_model}` · reranker: `{s.reranker_model}` · {len(items)} questions · CPU",
        "",
        "| Reranked candidates | Passage chars | Recall@5 | MRR@10 | p50 ms | p95 ms |",
        "|---:|---|---:|---:|---:|---:|",
        *[f"| {n} | {t} | {r:.4f} | {m:.4f} | {p50:.0f} | {p95:.0f} |" for n, t, r, m, p50, p95 in rows],
    ]
    OUT.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    asyncio.run(main_async())
