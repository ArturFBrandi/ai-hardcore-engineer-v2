"""PDF (official manuals) → structured blocks, one section per page.

Manual pages are self-contained topics ("Configure o wi-fi", "Solução de erros"), so the page is the
natural structural unit. Line-wrapped sentences are re-joined, hyphenation is undone, running headers
("Manual Get Smart 4 Índice«") are removed and numbered steps are kept together.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

from ingestion.extract import Block, ExtractedDocument

_RUNNING_HEADER = re.compile(r"^(manual( de uso)?( »)?\s*get\s*\w+|índice«?|\d{1,2})$", re.IGNORECASE)
_SENT_END = re.compile(r"[.:;!?)]$")
_STEP = re.compile(r"^(\d{1,2}[.)]|passo \d+|■|•|-)\s*", re.IGNORECASE)


def _clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.replace("\r", "").split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        line = line.replace("Índice«", "").strip()
        if not line or _RUNNING_HEADER.match(line):
            continue
        lines.append(line)
    return lines


def _join_wrapped(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if out and not _STEP.match(line):
            prev = out[-1]
            if prev.endswith("-") and line[:1].islower():
                out[-1] = prev[:-1] + line
                continue
            if not _SENT_END.search(prev) and (line[:1].islower() or len(prev) > 40):
                out[-1] = f"{prev} {line}"
                continue
        out.append(line)
    return out


def _page_title(lines: list[str]) -> str:
    for line in lines[:6]:
        stripped = re.sub(r"^\d{1,2}[.)]?\s*", "", line)
        if 4 <= len(stripped) <= 70 and not stripped.lower().startswith("manual"):
            return stripped
    return "Manual"


def extract_pdf(data: bytes, url: str, title_hint: str | None = None) -> ExtractedDocument:
    reader = PdfReader(io.BytesIO(data))
    meta_title = (reader.metadata.title if reader.metadata and reader.metadata.title else None) or title_hint
    title = meta_title or url.rsplit("/", 1)[-1].replace(".pdf", "").replace("_", " ").replace("-", " ")
    blocks: list[Block] = []
    for number, page in enumerate(reader.pages, 1):
        lines = _clean_lines(page.extract_text() or "")
        if len(" ".join(lines)) < 60:
            continue  # cover/index/blank pages
        joined = _join_wrapped(lines)
        blocks.append(Block("heading", f"{_page_title(joined)} (página {number})", level=2))
        para: list[str] = []
        steps: list[str] = []
        for line in joined:
            if _STEP.match(line):
                if para:
                    blocks.append(Block("paragraph", " ".join(para)))
                    para = []
                steps.append(line)
            else:
                if steps:
                    blocks.append(Block("procedure", "\n".join(steps)))
                    steps = []
                para.append(line)
                if _SENT_END.search(line) and len(" ".join(para)) > 300:
                    blocks.append(Block("paragraph", " ".join(para)))
                    para = []
        if steps:
            blocks.append(Block("procedure", "\n".join(steps)))
        if para:
            blocks.append(Block("paragraph", " ".join(para)))
    return ExtractedDocument(url=url, title=title, canonical_url=url, language="pt", blocks=blocks, links=[])
