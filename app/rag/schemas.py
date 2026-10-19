"""RAG domain models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievedDocument(BaseModel):
    chunk_id: str
    text: str
    title: str
    url: str
    heading: str
    category: str
    product: str | None = None
    products: list[str] = Field(default_factory=list)
    language: str | None = None
    retrieved_at: str | None = None
    fusion_score: float = 0.0
    rerank_score: float | None = None
    evidence_id: str = ""  # E1..En, assigned when given to the generator


class RetrievalFilters(BaseModel):
    product: str | None = None
    category: str | None = None


class RetrievalResult(BaseModel):
    query: str
    documents: list[RetrievedDocument]
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @property
    def top_rerank_score(self) -> float | None:
        scores = [d.rerank_score for d in self.documents if d.rerank_score is not None]
        return max(scores) if scores else None
