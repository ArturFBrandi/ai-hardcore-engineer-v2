# Retrieval benchmark (golden set)

Questions with relevance labels: **66** (PT+EN). Corpus: committed snapshot. CPU only.
Recall@5 = share of questions with ≥1 relevant chunk in the top 5; MRR@10; nDCG@5 (binary relevance).

## First stage (top-15 candidates)

| Dense model | dim | mode | Recall@5 | MRR@10 | nDCG@5 | p50 ms |
|---|---:|---|---:|---:|---:|---:|
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | 384 | dense | 0.8182 | 0.7223 | 0.5246 | 11.4 |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | 384 | sparse | 0.6364 | 0.5444 | 0.4463 | 3.8 |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | 384 | hybrid | 0.8333 | 0.7382 | 0.5816 | 11.4 |
| sentence-transformers/paraphrase-multilingual-mpnet-base-v2 | 768 | dense | 0.8485 | 0.7412 | 0.5665 | 24.0 |
| sentence-transformers/paraphrase-multilingual-mpnet-base-v2 | 768 | sparse | 0.6364 | 0.5457 | 0.4469 | 3.7 |
| sentence-transformers/paraphrase-multilingual-mpnet-base-v2 | 768 | hybrid | 0.8636 | 0.7159 | 0.5807 | 28.3 |
| intfloat/multilingual-e5-large | 1024 | dense | 0.9091 | 0.79 | 0.6402 | 79.8 |
| intfloat/multilingual-e5-large | 1024 | sparse | 0.6364 | 0.5437 | 0.4459 | 3.7 |
| intfloat/multilingual-e5-large | 1024 | hybrid | 0.8485 | 0.7295 | 0.5872 | 78.8 |
| minishlab/potion-multilingual-128M | 256 | dense | 0.7121 | 0.6102 | 0.4637 | 7.7 |
| minishlab/potion-multilingual-128M | 256 | sparse | 0.6364 | 0.5457 | 0.4469 | 6.7 |
| minishlab/potion-multilingual-128M | 256 | hybrid | 0.7879 | 0.6322 | 0.5114 | 9.0 |

## Reranking the hybrid top-15

| Dense model | Reranker | Recall@5 | MRR@10 | nDCG@5 | p50 ms | p95 ms |
|---|---|---:|---:|---:|---:|---:|
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | jinaai/jina-reranker-v2-base-multilingual | 0.8939 | 0.8409 | 0.6698 | 2966.9 | 4502.7 |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | BAAI/bge-reranker-v2-m3-int8 | 0.8939 | 0.8432 | 0.6731 | 6108.0 | 9169.5 |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | BAAI/bge-reranker-base | 0.8939 | 0.839 | 0.6726 | 2814.1 | 4201.5 |
| sentence-transformers/paraphrase-multilingual-mpnet-base-v2 | jinaai/jina-reranker-v2-base-multilingual | 0.9091 | 0.858 | 0.6933 | 2908.5 | 4216.2 |
| sentence-transformers/paraphrase-multilingual-mpnet-base-v2 | BAAI/bge-reranker-v2-m3-int8 | 0.9091 | 0.8592 | 0.6976 | 6154.8 | 8196.0 |
| sentence-transformers/paraphrase-multilingual-mpnet-base-v2 | BAAI/bge-reranker-base | 0.9091 | 0.8586 | 0.6995 | 2873.7 | 3924.8 |
| intfloat/multilingual-e5-large | jinaai/jina-reranker-v2-base-multilingual | 0.9394 | 0.8681 | 0.6907 | 3396.7 | 6202.4 |
| intfloat/multilingual-e5-large | BAAI/bge-reranker-v2-m3-int8 | 0.9394 | 0.8908 | 0.7147 | 6871.8 | 10041.2 |
| intfloat/multilingual-e5-large | BAAI/bge-reranker-base | 0.9394 | 0.8737 | 0.6874 | 3272.1 | 5952.9 |
| minishlab/potion-multilingual-128M | jinaai/jina-reranker-v2-base-multilingual | 0.8333 | 0.7992 | 0.6524 | 3508.1 | 7519.4 |
| minishlab/potion-multilingual-128M | BAAI/bge-reranker-v2-m3-int8 | 0.8485 | 0.7879 | 0.6468 | 6004.4 | 9420.4 |
| minishlab/potion-multilingual-128M | BAAI/bge-reranker-base | 0.8333 | 0.7812 | 0.6341 | 2992.5 | 4586.9 |
