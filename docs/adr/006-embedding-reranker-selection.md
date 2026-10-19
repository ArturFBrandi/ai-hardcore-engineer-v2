# ADR 006 — Embedding, sparse and reranker models chosen by benchmark

**Status:** accepted · 2026-10-19

## Context
Local CPU models (not the reasoning LLM). Mainly-PT corpus, PT+EN questions. The default stack must stay
laptop-friendly; models live in a volume, not in the image.

## Decision
- Dense: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (ONNX via fastembed), **384-d, cosine**.
- Sparse: `Qdrant/bm25` with Portuguese stemming.
- Reranker: `jinaai/jina-reranker-v2-base-multilingual`, applied to the **top 8** RRF candidates, passages truncated to **700 chars**.
- Pipeline: 15 fused candidates → dedup → rerank 8 → threshold → 5 evidence passages.
- Chunking: H1–H3 sections; FAQ Q/A, numbered procedures, tables and lists atomic; paragraph groups ≈900 chars (max 1,600);
  oversized paragraphs split on sentences with one-sentence overlap; heading path prefixed to every chunk; PDFs: one section per page.
- All configurable (`EMBEDDING_MODEL`, `RERANKER_MODEL`, …); changing the dense model requires `make reindex` (snapshot re-embeds).

## Measured evidence
`evaluation/results/retrieval_benchmark.md` (66 golden questions, hybrid top-15, CPU):

| Dense model (dim) | hybrid Recall@5 | + jina rerank Recall@5 / MRR@10 | index 925 chunks |
|---|---:|---:|---:|
| **MiniLM-L12 (384)** | 0.833 | 0.894 / 0.841 | 33 s |
| mpnet-base (768) | 0.864 | 0.909 / 0.858 | 261 s |
| e5-large (1024) | 0.848 | **0.939** / 0.868 | ≈11 min |
| potion-128M (256) | 0.788 | 0.833 / 0.799 | 25 s |

Rerankers on MiniLM candidates: jina-v2 0.894 / 0.841 (p50 2.97 s for 15 passages), bge-reranker-v2-m3-int8 0.894 / 0.843
(p50 6.1 s), bge-reranker-base 0.894 / 0.839 (p50 2.8 s). jina chosen: multilingual by design, equal quality, half
the latency of bge-m3; bge-base is primarily zh/en.

Rerank budget (`evaluation/results/rerank_budget.md`): top-8 @700 chars → Recall@5 0.894, MRR 0.846, p50 1.16 s
(vs. ≈3 s for 15 full passages, same Recall@5).

**Trade-off:** e5-large is +4.5 pp Recall@5 but ~10× larger (2.2 GB), needs >2 GB RAM per replica and ~20× the indexing
time; it is the documented "quality" option (`EMBEDDING_MODEL=intfloat/multilingual-e5-large` + `make reindex`).
bge-m3 dense is not available as a fastembed ONNX model and was not benchmarked.
