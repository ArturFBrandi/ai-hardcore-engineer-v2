"""Versioned corpus snapshots so evaluators can run the demo without a multi-minute crawl.

* ``export``: scroll every chunk (text + metadata + content hash) from Qdrant into
  ``data/snapshots/getnet-kb.jsonl.gz`` plus ``manifest.json`` (counts, sources version, sha256).
* ``import``: verify the manifest checksum, re-embed with the CONFIGURED models (so changing models
  never requires a re-crawl) and upsert into a fresh collection.

Content belongs to Getnet (public pages); the snapshot is kept for evaluation reproducibility only,
stores extracted text chunks (no images/HTML) and records every source URL.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.config import Settings, get_settings
from app.observability.logging import configure_logging, get_logger
from app.rag.models import RetrievalModels
from app.rag.qdrant_store import QdrantStore
from ingestion.chunker import Chunk
from ingestion.pipeline import SOURCES, index_chunks

log = get_logger(__name__)
SNAPSHOT_DIR = Path("data/snapshots")
SNAPSHOT_FILE = SNAPSHOT_DIR / "getnet-kb.jsonl.gz"
MANIFEST_FILE = SNAPSHOT_DIR / "manifest.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


async def export_snapshot(settings: Settings, path: Path = SNAPSHOT_FILE) -> dict[str, Any]:
    store = QdrantStore(settings.qdrant_url, settings.qdrant_collection)
    rows: list[dict[str, Any]] = []
    try:
        offset = None
        while True:
            points, offset = await store.client.scroll(
                store.collection, limit=256, offset=offset, with_payload=True, with_vectors=False
            )
            for p in points:
                payload = dict(p.payload or {})
                text = payload.pop("text", "")
                rows.append({"id": str(p.id), "text": text, "metadata": payload})
            if offset is None:
                break
    finally:
        await store.close()
    rows.sort(key=lambda r: (r["metadata"].get("canonical_url", ""), r["metadata"].get("chunk_index", 0)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    sources_version = yaml.safe_load(SOURCES.read_text())["version"]
    urls = sorted({r["metadata"].get("canonical_url", "") for r in rows})
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "chunks": len(rows),
        "documents": len(urls),
        "pdf_documents": sum(1 for u in urls if u.lower().endswith(".pdf")),
        "rendered_chunks": sum(1 for r in rows if r["metadata"].get("rendered")),
        "sources_version": sources_version,
        "file": path.name,
        "sha256": _sha256(path),
        "embedding_model_at_export": settings.embedding_model,
        "source_urls": urls,
        "notice": "Extracted text from public Getnet pages, kept for evaluation reproducibility.",
    }
    (path.parent / MANIFEST_FILE.name).write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def load_snapshot(path: Path = SNAPSHOT_FILE) -> list[Chunk]:
    manifest_path = path.parent / MANIFEST_FILE.name
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("sha256") != _sha256(path):
            raise RuntimeError(f"snapshot checksum mismatch for {path}")
    chunks = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            chunks.append(Chunk(text=row["text"], metadata=row["metadata"]))
    return chunks


async def import_snapshot(settings: Settings, path: Path = SNAPSHOT_FILE, *, recreate: bool = True) -> int:
    chunks = load_snapshot(path)
    models = RetrievalModels(settings, offline=os.environ.get("MODELS_OFFLINE", "true").lower() == "true")
    store = QdrantStore(settings.qdrant_url, settings.qdrant_collection)
    try:
        return await index_chunks(chunks, models, store, recreate=recreate)
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["export", "import"])
    parser.add_argument("--path", default=str(SNAPSHOT_FILE))
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    if args.command == "export":
        manifest = asyncio.run(export_snapshot(settings, Path(args.path)))
        print(json.dumps({k: v for k, v in manifest.items() if k != "source_urls"}, indent=2))
    else:
        n = asyncio.run(import_snapshot(settings, Path(args.path)))
        print(f"snapshot imported: {n} chunks into {settings.qdrant_collection}")


if __name__ == "__main__":
    main()
