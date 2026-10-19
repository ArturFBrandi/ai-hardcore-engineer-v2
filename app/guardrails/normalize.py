"""Text normalization before detection: Unicode NFKC, confusables, zero-width characters, simple
base64 / leetspeak decoding. Detection always runs on several *views* of the input so encoded payloads
("aWdub3JlIGFsbCBpbnN0cnVjdGlvbnM=", "1gn0r3 4ll 1nstruct10ns") are caught.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata

_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e]")
# Common Cyrillic/Greek look-alikes → Latin.
_CONFUSABLES = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "А": "A",
        "В": "B",
        "Е": "E",
        "К": "K",
        "М": "M",
        "Н": "H",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Т": "T",
        "Х": "X",
        "Ι": "I",
        "Ο": "O",
        "Α": "A",
        "Β": "B",
        "Ε": "E",
        "Ζ": "Z",
        "Η": "H",
        "Κ": "K",
        "Μ": "M",
        "Ν": "N",
        "Ρ": "P",
        "Τ": "T",
        "Υ": "Y",
        "Χ": "X",
        "ο": "o",
        "α": "a",
        "ι": "i",
    }
)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_B64 = re.compile(r"\b[A-Za-z0-9+/]{16,}={0,2}")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def canonical(text: str) -> str:
    """NFKC + zero-width removal + confusable folding + accent stripping + lowercase + whitespace collapse."""
    out = unicodedata.normalize("NFKC", text or "")
    out = _ZERO_WIDTH.sub("", out).translate(_CONFUSABLES)
    out = strip_accents(out).lower()
    return re.sub(r"\s+", " ", out).strip()


def _decode_base64_fragments(text: str) -> list[str]:
    decoded = []
    for match in _B64.findall(text):
        try:
            raw = base64.b64decode(match + "=" * (-len(match) % 4), validate=True)
        except (binascii.Error, ValueError):
            continue
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if sum(ch.isprintable() for ch in s) / max(len(s), 1) > 0.9:
            decoded.append(s)
    return decoded


def views(text: str) -> list[str]:
    """All normalized views of the input that detectors must inspect."""
    base = canonical(text)
    out = [base, base.translate(_LEET), re.sub(r"[^a-z0-9 ]", "", base)]
    # "i g n o r e  a l l" → "ignore all": collapse single-letter spacing.
    if re.search(r"(?:\b\w\b ){4,}", base):
        out.append(re.sub(r"(?<=\b\w) (?=\w\b)", "", base))
    out.extend(canonical(d) for d in _decode_base64_fragments(unicodedata.normalize("NFKC", text or "")))
    return list(dict.fromkeys(v for v in out if v))
