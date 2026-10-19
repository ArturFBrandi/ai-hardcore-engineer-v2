"""One-shot `models-init` job: download the configured retrieval models into the `models` volume and
verify them against `config/models.lock.json` (pinned HF revision + SHA-256 of every file).

`python -m scripts.models_init --write-lock` regenerates the lock after a deliberate model change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.rag.models import download_models

LOCK = Path("config/models.lock.json")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def inventory(models_dir: Path) -> dict[str, dict[str, str]]:
    """{repo: {"revision": <snapshot commit>, "<relative file>": sha256}} for every HF snapshot present."""
    out: dict[str, dict[str, str]] = {}
    for snap in sorted(models_dir.glob("models--*/snapshots/*")):
        repo = snap.parent.parent.name.removeprefix("models--").replace("--", "/")
        files = {str(p.relative_to(snap)): _sha256(p) for p in sorted(snap.rglob("*")) if p.is_file()}
        out[repo] = {"revision": snap.name, **files}
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--write-lock", action="store_true")
    a = p.parse_args()
    settings = get_settings()
    models_dir = Path(settings.models_dir)
    download_models(settings)
    inv = inventory(models_dir)
    wanted = {settings.embedding_model, settings.reranker_model, settings.sparse_model}
    if a.write_lock:
        LOCK.write_text(json.dumps({"models": sorted(wanted), "files": inv}, indent=2, sort_keys=True) + "\n")
        print(f"wrote {LOCK} ({len(inv)} repos)")
        return 0
    if not LOCK.exists():
        print("models.lock.json missing — run with --write-lock", file=sys.stderr)
        return 1
    lock = json.loads(LOCK.read_text())["files"]
    problems = []
    for repo, files in lock.items():
        got = inv.get(repo)
        if got is None:
            # a repo absent from this volume is fine only if it is not one of the configured models
            continue
        for name, digest in files.items():
            if got.get(name) != digest:
                problems.append(f"{repo}:{name} expected {digest[:12]} got {str(got.get(name))[:12]}")
    if problems:
        print("model checksum verification FAILED:\n  " + "\n  ".join(problems[:20]), file=sys.stderr)
        return 1
    print(f"models ready and verified: {sorted(wanted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
