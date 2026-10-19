"""Local embedding, sparse (BM25) and reranking models via fastembed (ONNX Runtime, CPU).

Models are NOT baked into the image: ``models-init`` downloads pinned revisions into the ``models``
volume once; the API loads them lazily from ``MODELS_DIR`` (offline). These are retrieval models, not
the reasoning LLM (MiniMax-M3).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.observability.logging import get_logger

log = get_logger(__name__)

# Some embedding families need instruction prefixes (E5: "query: " / "passage: ").
_PREFIXES: dict[str, tuple[str, str]] = {
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
    "intfloat/multilingual-e5-base": ("query: ", "passage: "),
    "intfloat/multilingual-e5-small": ("query: ", "passage: "),
}

BM25_LANGUAGE = "portuguese"  # Getnet content is mainly PT; Snowball PT stemming for the sparse side.


@dataclass(frozen=True)
class SparseVec:
    indices: list[int]
    values: list[float]


class RetrievalModels:
    """Thread-safe lazy holder for the three local models."""

    def __init__(self, settings: Settings, *, offline: bool = True) -> None:
        self.embedding_name = settings.embedding_model
        self.sparse_name = settings.sparse_model
        self.reranker_name = settings.reranker_model
        self.cache_dir = settings.models_dir
        self.offline = offline
        self._lock = threading.Lock()
        self._dense: Any = None
        self._sparse: Any = None
        self._reranker: Any = None

    # ---- loading -------------------------------------------------------------------------
    def _kw(self) -> dict[str, Any]:
        return {"cache_dir": self.cache_dir, "local_files_only": self.offline}

    def dense(self) -> Any:
        with self._lock:
            if self._dense is None:
                from fastembed import TextEmbedding  # noqa: PLC0415 - heavy ONNX import, loaded lazily

                self._dense = TextEmbedding(self.embedding_name, **self._kw())
                log.info("model_loaded", kind="dense", model=self.embedding_name)
            return self._dense

    def sparse(self) -> Any:
        with self._lock:
            if self._sparse is None:
                from fastembed import SparseTextEmbedding  # noqa: PLC0415 - heavy ONNX import, loaded lazily

                kw: dict[str, Any] = self._kw()
                if "bm25" in self.sparse_name.lower():
                    kw["language"] = BM25_LANGUAGE
                self._sparse = SparseTextEmbedding(self.sparse_name, **kw)
                log.info("model_loaded", kind="sparse", model=self.sparse_name)
            return self._sparse

    def reranker(self) -> Any:
        with self._lock:
            if self._reranker is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder  # noqa: PLC0415 - heavy ONNX import, loaded lazily

                self._reranker = TextCrossEncoder(self.reranker_name, **self._kw())
                log.info("model_loaded", kind="reranker", model=self.reranker_name)
            return self._reranker

    def warmup(self) -> None:
        self.embed_query("warmup")
        self.sparse_query("warmup")
        self.rerank("warmup", ["warmup"])

    @property
    def dense_dim(self) -> int:
        return len(self.embed_query("dimension probe"))

    # ---- sync compute (run in threads by async wrappers) ----------------------------------
    def embed_query(self, text: str) -> list[float]:
        prefix = _PREFIXES.get(self.embedding_name, ("", ""))[0]
        vec = next(iter(self.dense().query_embed([prefix + text])))
        return [float(x) for x in vec]

    def embed_passages(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        prefix = _PREFIXES.get(self.embedding_name, ("", ""))[1]
        return [[float(x) for x in v] for v in self.dense().embed([prefix + t for t in texts], batch_size=batch_size)]

    def sparse_query(self, text: str) -> SparseVec:
        emb = next(iter(self.sparse().query_embed(text)))
        return SparseVec(emb.indices.tolist(), emb.values.tolist())

    def sparse_passages(self, texts: list[str]) -> list[SparseVec]:
        return [SparseVec(e.indices.tolist(), e.values.tolist()) for e in self.sparse().embed(texts, batch_size=64)]

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        return [float(s) for s in self.reranker().rerank(query, documents, batch_size=16)]

    # ---- async wrappers (CPU work off the event loop) -------------------------------------
    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)

    async def asparse_query(self, text: str) -> SparseVec:
        return await asyncio.to_thread(self.sparse_query, text)

    async def arerank(self, query: str, documents: list[str]) -> list[float]:
        return await asyncio.to_thread(self.rerank, query, documents)


def download_models(settings: Settings, extra: list[str] | None = None) -> None:
    """Used by ``models-init`` (one-shot) to populate the models volume."""
    m = RetrievalModels(settings, offline=False)
    m.warmup()
    log.info("models_ready", dense=m.embedding_name, sparse=m.sparse_name, reranker=m.reranker_name, dim=m.dense_dim)
    for name in extra or []:
        RetrievalModels(settings.model_copy(update={"embedding_model": name}), offline=False).embed_query("x")
