# Rerank budget (golden set, hybrid top-15 candidates)

Dense: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` · reranker: `jinaai/jina-reranker-v2-base-multilingual` · 66 questions · CPU

| Reranked candidates | Passage chars | Recall@5 | MRR@10 | p50 ms | p95 ms |
|---:|---|---:|---:|---:|---:|
| 5 | full | 0.8485 | 0.8194 | 801 | 1333 |
| 5 | 700 | 0.8485 | 0.8194 | 707 | 897 |
| 8 | full | 0.8939 | 0.8460 | 1491 | 2172 |
| 8 | 700 | 0.8939 | 0.8460 | 1160 | 1378 |
| 10 | full | 0.8939 | 0.8497 | 1947 | 2974 |
| 10 | 700 | 0.8939 | 0.8460 | 1477 | 1748 |
| 15 | full | 0.8939 | 0.8384 | 2964 | 4407 |
| 15 | 700 | 0.8939 | 0.8389 | 2766 | 4122 |
