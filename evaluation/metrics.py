"""Pure, deterministic evaluation metrics (no I/O, no randomness)."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any


def normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace — used before regex fact matching."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", no_accents.lower()).strip()


# --------------------------------------------------------------------------------- retrieval
def is_relevant(doc_url: str, doc_text: str, rules: Sequence[dict[str, str]]) -> bool:
    url, text = normalize(doc_url), normalize(doc_text)
    for rule in rules:
        if re.search(normalize(rule["url"]), url) and re.search(normalize(rule.get("text", ".")), text):
            return True
    return False


def hit_at_k(relevance: Sequence[bool], k: int) -> float:
    """'Recall@K' as used in RAG evaluation: 1 if any relevant document is in the top K."""
    return 1.0 if any(relevance[:k]) else 0.0


def reciprocal_rank(relevance: Sequence[bool], k: int | None = None) -> float:
    for i, rel in enumerate(relevance[: k or len(relevance)], 1):
        if rel:
            return 1.0 / i
    return 0.0


def ndcg_at_k(relevance: Sequence[bool], k: int, total_relevant: int) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, rel in enumerate(relevance[:k], 1) if rel)
    ideal_n = min(k, max(total_relevant, 1 if any(relevance) else 0))
    if ideal_n == 0:
        return 0.0
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_n + 1))
    return dcg / idcg


# ------------------------------------------------------------------------------ answer facts
def fact_present(answer: str, patterns: Iterable[str]) -> bool:
    norm = normalize(answer)
    return any(re.search(normalize_pattern(p), norm) for p in patterns)


def normalize_pattern(pattern: str) -> str:
    decomposed = unicodedata.normalize("NFKD", pattern)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def claim_scores(answer: str, facts: Sequence[dict[str, Any]], must_not: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Claim recall (required atomic facts present) and hallucination (forbidden facts present)."""
    present = [fact_present(answer, f["patterns"]) for f in facts]
    violations = [fact_present(answer, f["patterns"]) for f in must_not]
    recall = sum(present) / len(present) if present else 1.0
    return {
        "claim_recall": recall,
        "facts_total": float(len(present)),
        "facts_present": float(sum(present)),
        "hallucinations": float(sum(violations)),
        "hallucinated": 1.0 if any(violations) else 0.0,
    }


# ----------------------------------------------------------------------------- classification
def accuracy(pairs: Iterable[tuple[Any, Any]]) -> float:
    items = list(pairs)
    return sum(1 for a, b in items if a == b) / len(items) if items else 0.0


def set_match(expected: Iterable[str], actual: Iterable[str]) -> bool:
    return set(expected) == set(actual)


def subset_match(required: Iterable[str], actual: Iterable[str]) -> bool:
    return set(required) <= set(actual)


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(q / 100 * len(ordered)) - 1))
    return ordered[idx]


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float:
    """Agreement between two binary raters beyond chance."""
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f
