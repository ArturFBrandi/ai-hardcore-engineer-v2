# ADR 003 — Qdrant with named dense + sparse vectors and RRF fusion

**Status:** accepted · 2026-10-19

## Context
Getnet content is mostly Portuguese, questions arrive in PT and EN, and many answers hinge on exact product names and
codes ("Get Smart", "ADQ 4-83", "D+2") where lexical matching matters.

## Decision
Qdrant 1.19 (client/server aligned) collection `getnet_kb` with named vectors `dense` (cosine, 384-d MiniLM) and
`sparse` (BM25 with Portuguese stemming, IDF modifier); hybrid retrieval through the Query API (two prefetches →
`FusionQuery(RRF)`), payload indexes on `category`, `products`, `language`, `canonical_url`, `content_hash`, `kind`.

## Alternatives considered
pgvector (no native sparse/BM25 fusion), Chroma (weaker filtering/hybrid at the time), dense-only (measured worse).

## Consequences
One extra datastore with a healthcheck and a named volume; product filters are "model OR generic" (model-agnostic
guides carry no product tag).

## Measured evidence (`evaluation/results/retrieval_benchmark.md`, 66 golden questions)
| MiniLM-L12 first stage | Recall@5 | MRR@10 |
|---|---:|---:|
| dense only | 0.818 | 0.722 |
| BM25 only | 0.636 | 0.544 |
| **hybrid RRF** | **0.833** | **0.738** |
