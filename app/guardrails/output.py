"""Output guardrails: canary leak detection, secret/PII redaction, unsupported numeric claims, internal
payload leakage and language checks."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field

from app.guardrails.normalize import strip_accents
from app.guardrails.redaction import redact_text
from app.observability import metrics

# One canary per process, embedded in every system prompt. Any output containing it is blocked.
CANARY = f"CANARY-{secrets.token_hex(8)}"

_NUMBER = re.compile(r"(?<![\w-])(?:r\$\s?)?\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d+)?\s?%?(?![\w-])", re.IGNORECASE)
_INTERNAL = re.compile(
    r"<\s*/?\s*(task_input|retrieved_evidence|conversation_history|tool_result)|\"tool_calls\"|\bstep_id\b|\bevidence_id\b",
    re.IGNORECASE,
)
_TRIVIAL = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "0", "24", "12"}


@dataclass
class OutputCheck:
    text: str
    flags: list[dict[str, str]] = field(default_factory=list)
    blocked: bool = False


def _num_key(raw: str) -> str:
    s = raw.lower().replace("r$", "").replace(" ", "").replace("%", "")
    s = s.replace(".", "").replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    try:
        value = float(s)
    except ValueError:
        return s
    return f"{value:.2f}"


def numbers_in(text: str) -> set[str]:
    return {_num_key(m.group(0)) for m in _NUMBER.finditer(text or "")}


def unsupported_numbers(answer: str, grounding: str) -> list[str]:
    """Numbers in the answer that do not appear in the evidence/tool/operator text it was based on.

    Dates/times are ignored (the synthesizer may restate them in another format); trivial small integers
    used in list markers/ordinals are tolerated.
    """
    stripped = re.sub(
        r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}h\d{0,2}\b|\b\d{1,2}:\d{2}\b", " ", answer
    )
    stripped = re.sub(r"\[\d+\]", " ", stripped)  # citation markers
    allowed = numbers_in(grounding)
    out = []
    for m in _NUMBER.finditer(stripped):
        raw = m.group(0).strip()
        key = _num_key(raw)
        if key in allowed or raw.strip("% ") in _TRIVIAL:
            continue
        out.append(raw)
    return out


def check_output(text: str, *, language: str | None = None) -> OutputCheck:
    result = OutputCheck(text=text)
    if CANARY in text or "canary-" in text.lower():
        result.blocked = True
        result.flags.append(
            {"layer": "output", "rule": "canary_leak", "severity": "block", "detail": "system prompt leak"}
        )
    if _INTERNAL.search(text):
        result.flags.append(
            {"layer": "output", "rule": "internal_payload", "severity": "warn", "detail": "internal markup removed"}
        )
        result.text = _INTERNAL.sub("", result.text)
    redacted = redact_text(result.text)
    if redacted != result.text:
        result.flags.append(
            {"layer": "output", "rule": "sensitive_data_redacted", "severity": "warn", "detail": "redacted"}
        )
        result.text = redacted
    if language in ("pt", "en") and len(text) > 40 and detect_language(text) not in (language, None):
        result.flags.append(
            {"layer": "output", "rule": "language_mismatch", "severity": "info", "detail": f"expected {language}"}
        )
    for f in result.flags:
        metrics.GUARDRAIL_TRIGGERS.labels(layer="output", rule=f["rule"]).inc()
    return result


_PT_HINTS = re.compile(
    r"\b(voce|nao|sim|para|como|minha|meu|sua|seu|vendas|quando|qual|quais|obrigad|ola|maquininha|esta|sao|tambem|ontem|hoje|preciso|posso|tenho|com|uma|das|dos|nos|pelo|pela)\b"
)
_EN_HINTS = re.compile(
    r"\b(you|your|the|what|when|how|which|my|is|are|can|will|yesterday|today|please|thanks|hello|sales|machine|need|do|does|i|of|and|for|with)\b"
)


def detect_language(text: str) -> str | None:
    """Deterministic PT/EN detection by function-word counts (None when undecidable)."""
    t = strip_accents((text or "").lower())
    pt, en = len(_PT_HINTS.findall(t)), len(_EN_HINTS.findall(t))
    if re.search(r"[ãõçáéíóúâê]", (text or "").lower()):
        pt += 2
    if pt == en:
        return None
    return "pt" if pt > en else "en"
