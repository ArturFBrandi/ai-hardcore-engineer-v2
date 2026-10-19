"""Qdrant collection with named dense + sparse vectors and RRF fusion through the Query API."""

from __future__ import annotations

from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.rag.models import SparseVec

DENSE = "dense"
SPARSE = "sparse"
_INDEXED = {
    "category": models.PayloadSchemaType.KEYWORD,
    "products": models.PayloadSchemaType.KEYWORD,
    "language": models.PayloadSchemaType.KEYWORD,
    "canonical_url": models.PayloadSchemaType.KEYWORD,
    "content_hash": models.PayloadSchemaType.KEYWORD,
    "kind": models.PayloadSchemaType.KEYWORD,
}


class QdrantStore:
    def __init__(self, url: str, collection: str, timeout: float = 10.0) -> None:
        self.collection = collection
        self.client = AsyncQdrantClient(url=url, timeout=int(timeout), check_compatibility=False)

    async def close(self) -> None:
        await self.client.close()

    async def ensure_collection(self, dim: int, *, recreate: bool = False) -> None:
        exists = await self.client.collection_exists(self.collection)
        if exists and recreate:
            await self.client.delete_collection(self.collection)
            exists = False
        if not exists:
            await self.client.create_collection(
                self.collection,
                vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
                sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
            for field, schema in _INDEXED.items():
                await self.client.create_payload_index(self.collection, field, field_schema=schema)

    async def dense_dim(self) -> int | None:
        if not await self.client.collection_exists(self.collection):
            return None
        info = await self.client.get_collection(self.collection)
        vectors = info.config.params.vectors
        if isinstance(vectors, dict) and DENSE in vectors:
            return int(vectors[DENSE].size)
        return None

    async def count(self) -> int:
        if not await self.client.collection_exists(self.collection):
            return 0
        return int((await self.client.count(self.collection, exact=True)).count)

    async def upsert(
        self, ids: list[str], dense: list[list[float]], sparse: list[SparseVec], payloads: list[dict[str, Any]]
    ) -> None:
        points = [
            models.PointStruct(
                id=pid,
                vector={DENSE: d, SPARSE: models.SparseVector(indices=s.indices, values=s.values)},
                payload=p,
            )
            for pid, d, s, p in zip(ids, dense, sparse, payloads, strict=True)
        ]
        for start in range(0, len(points), 128):
            await self.client.upsert(self.collection, points=points[start : start + 128], wait=True)

    async def delete_by_url(self, canonical_url: str, keep_hashes: set[str]) -> int:
        """Remove chunks of a re-crawled page whose content no longer exists (incremental re-index)."""
        flt = models.Filter(
            must=[models.FieldCondition(key="canonical_url", match=models.MatchValue(value=canonical_url))],
            must_not=[models.FieldCondition(key="content_hash", match=models.MatchAny(any=list(keep_hashes)))]
            if keep_hashes
            else None,
        )
        before = await self.client.count(self.collection, count_filter=flt, exact=True)
        if before.count:
            await self.client.delete(self.collection, points_selector=models.FilterSelector(filter=flt), wait=True)
        return int(before.count)

    @staticmethod
    def build_filter(product: str | None = None, category: str | None = None) -> models.Filter | None:
        must: list[models.Condition] = []
        if product:
            # The model's own documents OR generic (model-agnostic) ones, e.g. "how to configure Wi-Fi on your machine".
            must.append(
                models.Filter(
                    should=[
                        models.FieldCondition(key="products", match=models.MatchAny(any=[product])),
                        models.IsEmptyCondition(is_empty=models.PayloadField(key="products")),
                    ]
                )
            )
        if category:
            must.append(models.FieldCondition(key="category", match=models.MatchValue(value=category)))
        return models.Filter(must=must) if must else None

    async def hybrid(
        self, dense: list[float], sparse: SparseVec, limit: int, flt: models.Filter | None
    ) -> list[models.ScoredPoint]:
        res = await self.client.query_points(
            self.collection,
            prefetch=[
                models.Prefetch(query=dense, using=DENSE, limit=limit, filter=flt),
                models.Prefetch(
                    query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                    using=SPARSE,
                    limit=limit,
                    filter=flt,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        return list(res.points)

    async def dense_only(self, dense: list[float], limit: int, flt: models.Filter | None) -> list[models.ScoredPoint]:
        res = await self.client.query_points(
            self.collection, query=dense, using=DENSE, limit=limit, query_filter=flt, with_payload=True
        )
        return list(res.points)

    async def sparse_only(self, sparse: SparseVec, limit: int, flt: models.Filter | None) -> list[models.ScoredPoint]:
        res = await self.client.query_points(
            self.collection,
            query=models.SparseVector(indices=sparse.indices, values=sparse.values),
            using=SPARSE,
            limit=limit,
            query_filter=flt,
            with_payload=True,
        )
        return list(res.points)
