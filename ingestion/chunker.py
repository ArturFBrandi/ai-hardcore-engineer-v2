"""Structure-aware chunking + metadata enrichment.

Strategy (ADR 006):
* Section boundaries at H1/H2/H3 (and PDF pages). Each chunk is prefixed with its heading path
  ("Title › Section") so it is self-describing for both dense and BM25 retrieval.
* Semantic units are atomic: a FAQ Q/A pair, a numbered procedure, a table and a list are never split.
  A FAQ entry is always its own chunk (the best retrieval unit for support questions).
* Paragraph groups are packed up to ``target_chars`` (~900 chars ≈ 220 tokens) and never exceed
  ``max_chars`` (1600); only a single oversized paragraph is split, on sentence boundaries, with a
  one-sentence overlap.
* Metadata: url, canonical_url, title, heading, category, product, language, source, retrieved_at,
  content_hash, doc_hash, etag, last_modified, chunk_index, kind, rendered.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from ingestion.extract import Block, ExtractedDocument

TARGET_CHARS = 900
MAX_CHARS = 1600
MIN_CHARS = 80
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9])")
NAMESPACE = uuid.UUID("6f1c5e2a-8d3b-4c7e-9a10-2b5d4e6f7a81")


@dataclass
class Chunk:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Deterministic point id: same URL + same content → same id (idempotent upserts)."""
        return str(uuid.uuid5(NAMESPACE, f"{self.metadata['canonical_url']}#{self.metadata['content_hash']}"))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Classifier:
    categories: list[dict[str, Any]]
    products: list[dict[str, Any]]

    def category(self, url: str, text: str, default: str) -> str:
        low = text.lower()
        for rule in self.categories:
            if rule.get("match") and re.search(rule["match"], url, re.IGNORECASE):
                return str(rule["category"])
        for rule in self.categories:
            if any(k in low for k in rule.get("keywords", [])):
                return str(rule["category"])
        return default

    def products_in(self, text: str) -> list[str]:
        low = f" {text.lower()} "
        return [str(r["product"]) for r in self.products if any(k in low for k in r.get("keywords", []))]


def _split_long(text: str) -> list[str]:
    sentences = _SENTENCE.split(text)
    parts: list[str] = []
    current: list[str] = []
    for s in sentences:
        if current and len(" ".join([*current, s])) > TARGET_CHARS:
            parts.append(" ".join(current))
            current = [current[-1]]  # one-sentence overlap
        current.append(s)
    if current:
        parts.append(" ".join(current))
    return parts


def chunk_document(
    doc: ExtractedDocument,
    *,
    category: str,
    language: str,
    classifier: Classifier,
    page_product: str | None = None,
    retrieved_at: datetime | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    rendered: bool = False,
) -> list[Chunk]:
    retrieved = (retrieved_at or datetime.now(UTC)).isoformat()
    doc_hash = content_hash(doc.text)
    page_products = classifier.products_in(f"{doc.title} {doc.url}")
    sections: list[tuple[str, list[Block]]] = []
    path: dict[int, str] = {}
    current: list[Block] = []
    heading = ""
    for block in doc.blocks:
        if block.kind == "heading" and block.level <= 3:
            if current:
                sections.append((heading, current))
            path = {lvl: h for lvl, h in path.items() if lvl < block.level}
            path[block.level] = block.text
            heading = " › ".join(path[k] for k in sorted(path))
            current = []
        elif block.kind == "heading":
            current.append(Block("paragraph", block.text))
        else:
            current.append(block)
    if current:
        sections.append((heading, current))

    raw: list[tuple[str, str, str]] = []  # (heading, text, kind)
    for sec_heading, blocks in sections:
        buf: list[str] = []

        def flush(h: str = sec_heading, pending: list[str] = buf) -> None:
            if pending:
                raw.append((h, "\n".join(pending), "section"))
                pending.clear()

        for b in blocks:
            if b.kind == "faq":
                flush()
                raw.append((sec_heading, b.text, "faq"))
                continue
            text = b.text
            if len(text) > MAX_CHARS and b.kind == "paragraph":
                flush()
                raw.extend((sec_heading, part, "section") for part in _split_long(text))
                continue
            if buf and len("\n".join([*buf, text])) > TARGET_CHARS:
                flush()
            buf.append(text)
        flush()

    # Merge tiny trailing section chunks into their predecessor when they share a heading.
    merged: list[tuple[str, str, str]] = []
    for h, text, kind in raw:
        if (
            merged
            and kind == "section"
            and merged[-1][2] == "section"
            and merged[-1][0] == h
            and len(text) < MIN_CHARS
            and len(merged[-1][1]) + len(text) < MAX_CHARS
        ):
            merged[-1] = (h, f"{merged[-1][1]}\n{text}", kind)
        else:
            merged.append((h, text, kind))

    chunks: list[Chunk] = []
    host = urlparse(doc.canonical_url).netloc
    seen: set[str] = set()
    for idx, (h, text, kind) in enumerate(merged):
        if len(text) < 40 and kind != "faq":
            continue
        prefix = doc.title if not h else f"{doc.title} › {h}"
        body = f"{prefix}\n{text}"
        digest = content_hash(body)
        if digest in seen:
            continue
        seen.add(digest)
        products = classifier.products_in(f"{h} {text}") or ([page_product] if page_product else []) or page_products
        chunks.append(
            Chunk(
                text=body,
                metadata={
                    "url": doc.url,
                    "canonical_url": doc.canonical_url,
                    "title": doc.title,
                    "heading": h or doc.title,
                    "category": classifier.category(doc.url, f"{h} {text}", category),
                    "page_category": category,
                    "product": products[0] if products else None,
                    "products": products,
                    "language": language,
                    "source": host,
                    "retrieved_at": retrieved,
                    "content_hash": digest,
                    "doc_hash": doc_hash,
                    "body_hash": content_hash(text.strip().lower()),
                    "etag": etag,
                    "last_modified": last_modified,
                    "chunk_index": idx,
                    "kind": "pdf" if doc.url.lower().endswith(".pdf") else kind,
                    "rendered": rendered,
                },
            )
        )
    return chunks
