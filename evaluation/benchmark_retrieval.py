"""Benchmark embedding / sparse / reranker candidates on the golden set (``make benchmark-retrieval``).

For every dense candidate a temporary Qdrant collection is built from the committed snapshot; each golden
question with relevance rules is retrieved in dense, sparse (BM25-PT) and hybrid (RRF) modes, then the
hybrid candidates are reranked by each cross-encoder. Reports Recall@5 (hit rate), MRR@10, nDCG@5 and
CPU latency, and writes ``evaluation/results/retrieval_benchmark.{json,md}``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import yaml

from app.config import Settings, get_settings
from app.rag.models import RetrievalModels
from app.rag.qdrant_store import QdrantStore
from evaluation.metrics import hit_at_k, is_relevant, ndcg_at_k, reciprocal_rank
from ingestion.chunker import Chunk
from ingestion.snapshot import load_snapshot

GOLDEN = Path("evaluation/golden/golden.yaml")
OUT = Path("evaluation/results")
DENSE = [
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
    "intfloat/multilingual-e5-large",
    "minishlab/potion-multilingual-128M",
]
RERANKERS = [
    "jinaai/jina-reranker-v2-base-multilingual",
    "BAAI/bge-reranker-v2-m3-int8",
    "BAAI/bge-reranker-base",
]


def golden_items() -> list[dict[str, Any]]:
    data = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    return [i for i in data["items"] if i.get("relevant")]


def _metrics(rels: list[list[bool]], totals: list[int]) -> dict[str, float]:
    return {
        "recall@5": round(statistics.mean(hit_at_k(r, 5) for r in rels), 4),
        "mrr@10": round(statistics.mean(reciprocal_rank(r, 10) for r in rels), 4),
        "ndcg@5": round(statistics.mean(ndcg_at_k(r, 5, t) for r, t in zip(rels, totals, strict=True)), 4),
    }


async def bench_dense(
    settings: Settings, dense_name: str, chunks: list[Chunk], items: list[dict[str, Any]], rerankers: list[str]
) -> dict[str, Any]:
    s = settings.model_copy(
        update={
            "embedding_model": dense_name,
            "qdrant_collection": "bench_" + dense_name.rsplit("/", maxsplit=1)[-1].lower().replace(".", "-"),
        }
    )
    models = RetrievalModels(s, offline=False)
    store = QdrantStore(s.qdrant_url, s.qdrant_collection)
    t0 = time.perf_counter()
    from ingestion.pipeline import index_chunks

    await index_chunks(chunks, models, store, recreate=True)
    index_s = time.perf_counter() - t0
    totals = [sum(1 for c in chunks if is_relevant(c.metadata["canonical_url"], c.text, i["relevant"])) for i in items]
    result: dict[str, Any] = {
        "model": dense_name,
        "dim": models.dense_dim,
        "index_seconds": round(index_s, 1),
        "modes": {},
    }
    candidates: list[list[dict[str, Any]]] = []
    for mode in ("dense", "sparse", "hybrid"):
        rels, lat = [], []
        for item in items:
            q0 = time.perf_counter()
            dv = models.embed_query(item["question"]) if mode != "sparse" else []
            sv = models.sparse_query(item["question"]) if mode != "dense" else None
            if mode == "dense":
                pts = await store.dense_only(dv, 15, None)
            elif mode == "sparse":
                assert sv is not None
                pts = await store.sparse_only(sv, 15, None)
            else:
                assert sv is not None
                pts = await store.hybrid(dv, sv, 15, None)
            lat.append((time.perf_counter() - q0) * 1000)
            docs = [{"url": p.payload["canonical_url"], "text": p.payload["text"]} for p in pts]  # type: ignore[index]
            rels.append([is_relevant(d["url"], d["text"], item["relevant"]) for d in docs])
            if mode == "hybrid":
                candidates.append(docs)
        result["modes"][mode] = {**_metrics(rels, totals), "p50_ms": round(statistics.median(lat), 1)}
    result["rerank"] = {}
    for rr in rerankers:
        rmodels = RetrievalModels(s.model_copy(update={"reranker_model": rr}), offline=False)
        rels, lat = [], []
        for item, docs in zip(items, candidates, strict=True):
            q0 = time.perf_counter()
            scores = rmodels.rerank(item["question"], [d["text"] for d in docs]) if docs else []
            lat.append((time.perf_counter() - q0) * 1000)
            order = sorted(range(len(docs)), key=lambda k: scores[k], reverse=True)
            rels.append([is_relevant(docs[k]["url"], docs[k]["text"], item["relevant"]) for k in order])
        result["rerank"][rr] = {
            **_metrics(rels, totals),
            "p50_ms": round(statistics.median(lat), 1),
            "p95_ms": round(sorted(lat)[int(len(lat) * 0.95) - 1], 1),
        }
    await store.client.delete_collection(s.qdrant_collection)
    await store.close()
    return result


def render_md(results: list[dict[str, Any]], n_items: int) -> str:
    lines = [
        "# Retrieval benchmark (golden set)",
        "",
        f"Questions with relevance labels: **{n_items}** (PT+EN). Corpus: committed snapshot. CPU only.",
        "Recall@5 = share of questions with ≥1 relevant chunk in the top 5; MRR@10; nDCG@5 (binary relevance).",
        "",
        "## First stage (top-15 candidates)",
        "",
        "| Dense model | dim | mode | Recall@5 | MRR@10 | nDCG@5 | p50 ms |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for r in results:
        for mode, m in r["modes"].items():
            lines.append(
                f"| {r['model']} | {r['dim']} | {mode} | {m['recall@5']} | {m['mrr@10']} | {m['ndcg@5']} | {m['p50_ms']} |"
            )
    lines += [
        "",
        "## Reranking the hybrid top-15",
        "",
        "| Dense model | Reranker | Recall@5 | MRR@10 | nDCG@5 | p50 ms | p95 ms |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        for rr, m in r["rerank"].items():
            lines.append(
                f"| {r['model']} | {rr} | {m['recall@5']} | {m['mrr@10']} | {m['ndcg@5']} | {m['p50_ms']} | {m['p95_ms']} |"
            )
    return "\n".join(lines) + "\n"


async def main_async(dense: list[str], rerankers: list[str]) -> None:
    settings = get_settings()
    chunks = load_snapshot()
    items = golden_items()
    results = []
    for i, name in enumerate(dense):
        print(f"[{name}] indexing + evaluating…", flush=True)
        results.append(await bench_dense(settings, name, chunks, items, rerankers))
        print(json.dumps(results[-1], indent=1), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "retrieval_benchmark.json").write_text(json.dumps({"items": len(items), "results": results}, indent=2))
    (OUT / "retrieval_benchmark.md").write_text(render_md(results, len(items)))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dense", nargs="*", default=DENSE)
    p.add_argument("--rerankers", nargs="*", default=RERANKERS)
    a = p.parse_args()
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    asyncio.run(main_async(a.dense, a.rerankers))


if __name__ == "__main__":
    main()
