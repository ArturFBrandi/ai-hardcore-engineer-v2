"""Deterministic redaction of secrets, card numbers and Brazilian documents.

Applied before persistence (memory, audit), in logs/traces and on outputs. Patterns are conservative:
masked values already stored in the synthetic backend (``***.456.789-**``) are left alone.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # API keys / bearer tokens / JWTs
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\b(?:sk|tvly|pk|rk)-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_SECRET]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|secret|password|senha|token)\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
        r"\1=[REDACTED_SECRET]",
    ),
    # Payment card numbers (13–19 digits, optional separators)
    (re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), "[REDACTED_CARD]"),
    # CNPJ 00.000.000/0000-00 and CPF 000.000.000-00 (formatted or bare)
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[REDACTED_CNPJ]"),
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "[REDACTED_CPF]"),
    (re.compile(r"\b\d{11}\b"), "[REDACTED_CPF]"),
    # CVV after keyword
    (re.compile(r"(?i)\b(cvv|cvc|código de segurança)\s*[:=]?\s*\d{3,4}\b"), r"\1 [REDACTED_CVV]"),
]


def redact_text(text: str) -> str:
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def contains_sensitive(text: str) -> bool:
    return redact_text(text) != text
