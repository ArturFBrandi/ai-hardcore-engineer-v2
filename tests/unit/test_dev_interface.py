"""Developer interface contract: the Makefile has every required target and every documented `make` target exists."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    "help",
    "setup",
    "up",
    "down",
    "logs",
    "docker-build",
    "migrate",
    "seed",
    "ingest",
    "ingest-snapshot",
    "snapshot-export",
    "reindex",
    "calibrate",
    "demo",
    "demo-scenarios",
    "demo-conversation",
    "operator",
    "keys",
    "token",
    "llm-test",
    "smoke-test",
    "test",
    "test-docker",
    "test-unit",
    "test-integration",
    "test-orchestration",
    "test-security",
    "test-e2e",
    "test-multiturn",
    "test-hitl",
    "test-live",
    "eval",
    "eval-live",
    "coverage",
    "lint",
    "typecheck",
    "security",
    "sbom",
    "verify",
    "purge-expired",
    "export-feedback",
    "dev-reset",
]
DOCS = ["README.md", *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").rglob("*.md"))]
# The challenge statement and the spec are inputs, not documentation of this repository.
DOCS = [d for d in DOCS if not d.endswith(("initial-prompt.md", "Tech-Challenge.md"))]


def makefile_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text()
    return set(re.findall(r"^([a-zA-Z][\w-]*)\s*:(?!=)", text, flags=re.M))


def documented_targets(path: str) -> set[str]:
    """`make` targets in code: fenced blocks (``make a | b``) and inline spans (`make a` · `b`). Prose is ignored."""
    found: set[str] = set()
    in_fence = False
    for line in (ROOT / path).read_text().splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            code = line.split("#", 1)[0].strip()
            if re.match(r"(sudo\s+)?make\s", code):
                for part in code.split("|"):
                    m = re.match(r"\s*(?:make\s+)?(?:-s\s+)?([a-z][\w-]*)", part)
                    if m and m.group(1) != "make":
                        found.add(m.group(1))
            found |= set(re.findall(r"\$\(make\s+-s\s+([a-z][\w-]*)", code))
            continue
        for m in re.finditer(r"`make\s+(?:-s\s+)?([a-z][\w-]*)[^`]*`((?:\s*[·|]\s*`[a-z][\w-]*`)*)", line):
            found.add(m.group(1))
            found |= set(re.findall(r"`([a-z][\w-]*)`", m.group(2)))
    return found


def test_makefile_has_every_required_target() -> None:
    missing = sorted(set(REQUIRED) - makefile_targets())
    assert not missing, f"Makefile is missing required targets: {missing}"


@pytest.mark.parametrize("doc", DOCS)
def test_every_documented_make_target_exists(doc: str) -> None:
    unknown = sorted(documented_targets(doc) - makefile_targets())
    assert not unknown, f"{doc} documents make targets that do not exist: {unknown}"


def test_help_lists_every_target_with_a_description() -> None:
    text = (ROOT / "Makefile").read_text()
    described = set(re.findall(r"^([a-zA-Z][\w-]*)\s*:[^=\n]*##", text, flags=re.M))
    undocumented = sorted(set(REQUIRED) - described)
    assert not undocumented, f"targets without a '## description' for make help: {undocumented}"
