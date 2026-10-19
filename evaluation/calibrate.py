"""Threshold calibration (``make calibrate``).

For the golden questions (positives, with relevance labels) and out-of-domain / non-existent-entity
questions (negatives), retrieve with thresholds disabled and sweep ``RAG_RERANK_MIN_SCORE``:

* a question is *answered* at threshold t if at least one reranked chunk scores >= t;
* a positive is *correct* if a relevant chunk is among the selected evidence;
* precision = correct / answered (negatives answered count as errors), recall = correct / positives.

The chosen threshold maximizes F1 (ties → the higher threshold). Results and the full curve go to
``evaluation/results/calibration.md``; ``.env.example`` defaults come from this run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.rag.models import RetrievalModels
from app.rag.qdrant_store import QdrantStore
from app.rag.retriever import Retriever
from evaluation.metrics import is_relevant, precision_recall_f1

OUT = Path("evaluation/results/calibration.md")
NEGATIVES = [
    "What's the weather forecast in Porto Alegre tomorrow?",
    "Qual a cotação do euro hoje?",
    "Who won the 2022 World Cup?",
    "Me passa uma receita de bolo de cenoura",
    "How do I reset my iPhone?",
    "Qual a taxa Selic hoje?",
    "Quem é o presidente do Brasil?",
    "Write me a poem about football",
    "Qual é o preço da maquininha Get Ultra Pro?",
    "Qual a taxa de antecipação do plano Diamante Getnet?",
    "Como faço para trocar o óleo do carro?",
    "What is the capital of Australia?",
]


async def collect() -> tuple[list[tuple[list[float], list[bool]]], list[list[float]]]:
    s = get_settings()
    store = QdrantStore(s.qdrant_url, s.qdrant_collection)
    retriever = Retriever(
        s.model_copy(update={"rag_context_top_k": s.rag_rerank_top_k}), RetrievalModels(s, offline=False), store
    )
    golden = [
        g for g in yaml.safe_load(Path("evaluation/golden/golden.yaml").read_text())["items"] if g.get("relevant")
    ]
    pos = []
    for g in golden:
        r = await retriever.retrieve(g["question"], apply_thresholds=False)
        pos.append(
            (
                [d.rerank_score or 0.0 for d in r.documents],
                [is_relevant(d.url, d.text, g["relevant"]) for d in r.documents],
            )
        )
    neg = []
    for q in NEGATIVES:
        r = await retriever.retrieve(q, apply_thresholds=False)
        neg.append([d.rerank_score or 0.0 for d in r.documents])
    await store.close()
    return pos, neg


def sweep(pos: list[tuple[list[float], list[bool]]], neg: list[list[float]], context_k: int) -> list[dict[str, Any]]:
    all_scores = sorted(
        {round(x, 2) for scores, _ in pos for x in scores} | {round(x, 2) for scores in neg for x in scores}
    )
    grid = sorted(
        {-6.0, -5.0, -4.0, -3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0}
        | {round(v, 1) for v in all_scores if -6 <= v <= 2}
    )
    rows = []
    for t in grid:
        tp = fp_pos = 0
        for scores, rel in pos:
            selected = [r for s, r in zip(scores, rel, strict=True) if s >= t][:context_k]
            if selected:
                if any(selected):
                    tp += 1
                else:
                    fp_pos += 1
        fp_neg = sum(1 for scores in neg if any(s >= t for s in scores))
        fn = len(pos) - tp
        p, r, f = precision_recall_f1(tp, fp_pos + fp_neg, fn)
        rows.append(
            {
                "threshold": t,
                "precision": round(p, 4),
                "recall": round(r, 4),
                "f1": round(f, 4),
                "negatives_accepted": fp_neg,
                "positives_answered_wrong": fp_pos,
            }
        )
    return rows


def main() -> None:
    s = get_settings()
    pos, neg = asyncio.run(collect())
    rows = sweep(pos, neg, s.rag_context_top_k)
    best = max(rows, key=lambda r: (r["f1"], r["threshold"]))
    lines = [
        "# RAG threshold calibration",
        "",
        f"Models: dense `{s.embedding_model}`, sparse `{s.sparse_model}`, reranker `{s.reranker_model}` (top-{s.rag_rerank_top_k} reranked).",
        f"Positives: {len(pos)} golden questions with relevance labels · negatives: {len(neg)} out-of-domain / non-existent-entity questions.",
        "",
        f"**Chosen `RAG_RERANK_MIN_SCORE={best['threshold']}`** (max F1 = {best['f1']}, precision {best['precision']}, recall {best['recall']}, "
        f"negatives accepted {best['negatives_accepted']}/{len(neg)}). `RAG_MIN_SCORE` (RRF fusion score) stays `0.0`: RRF scores are rank-based "
        "and carry no absolute relevance signal; filtering is done on the calibrated cross-encoder score.",
        "",
        'Negatives that still pass the threshold (e.g. the non-existent "Get Ultra Pro") are handled downstream by the deterministic '
        'entity-mismatch signal and the grounded "no verified information" answer — the threshold is one layer, not the only one.',
        "",
        "| RAG_RERANK_MIN_SCORE | precision | recall | F1 | negatives accepted | positives answered with irrelevant evidence |",
        "|---:|---:|---:|---:|---:|---:|",
        *[
            f"| {r['threshold']} | {r['precision']} | {r['recall']} | {r['f1']} | {r['negatives_accepted']} | {r['positives_answered_wrong']} |"
            for r in rows
        ],
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    Path("evaluation/results/calibration.json").write_text(json.dumps({"chosen": best, "curve": rows}, indent=2) + "\n")
    print(
        f"RAG_RERANK_MIN_SCORE={best['threshold']}  (F1={best['f1']} P={best['precision']} R={best['recall']})  → {OUT}"
    )


if __name__ == "__main__":
    main()
