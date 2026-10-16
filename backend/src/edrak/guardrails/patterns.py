"""Patterns shared by every guardrail: prompt injection and personal data.

Deterministic on purpose: they run on every question, every document chunk and every web page,
so they must be fast, explainable and never depend on a model being available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- prompt injection

# Text that tries to give the model new instructions. English and Arabic.
_INJECTION = [
    r"ignore (all |any |the )?(previous|prior|above|earlier|preceding) (instructions|prompts?|rules|messages)",
    r"disregard (all |any |the )?(previous|prior|above|earlier|system) (instructions|prompts?|rules)",
    r"forget (all |everything|your) (previous |prior )?(instructions|rules|training)",
    r"(reveal|print|show|repeat|output) (me )?(your|the) (system|hidden|initial) (prompt|instructions|message)",
    r"you are now (?:a|an|in) ",
    r"\bact as (?:a|an) (?!analyst|customer|competitor)",
    r"(developer|jailbreak|god|dan) mode",
    r"new (system )?instructions?:",
    r"^\s*(system|assistant)\s*:\s",
    r"<\s*/?\s*(system|instructions?|im_start|im_end)\s*>",
    r"\[\s*(system|inst)\s*\]",
    r"do not (tell|inform|mention to) the user",
    r"override (the |your )?(safety|guard ?rails?|policy|policies|rules)",
    r"(send|post|upload|exfiltrate) (the |all )?(data|documents?|conversation|secrets?|api keys?) to",
    r"(تجاهل|انس|أهمل)\s+(كل\s+|جميع\s+)?(التعليمات|الأوامر)\s+(السابقة|السالفة)",
    r"(اكشف|اعرض|اطبع)\s+(لي\s+)?(تعليمات|موجه)\s+النظام",
    r"أنت\s+الآن\s+",
]
INJECTION = re.compile("|".join(f"(?:{p})" for p in _INJECTION), re.IGNORECASE | re.MULTILINE)

# Invisible characters used to hide instructions inside otherwise normal text.
HIDDEN_CHARS = re.compile("[​-‏‪-‮⁠-⁤﻿]")


def injection_hits(text: str) -> list[str]:
    """The instruction-like fragments found in `text` (at most 5, shortened)."""
    hits = []
    for match in INJECTION.finditer(text or ""):
        fragment = match.group(0).strip()
        if fragment and fragment.lower() not in {h.lower() for h in hits}:
            hits.append(fragment[:80])
        if len(hits) >= 5:
            break
    return hits


def hidden_char_count(text: str) -> int:
    return len(HIDDEN_CHARS.findall(text or ""))


# --------------------------------------------------------------------------- personal data


@dataclass(frozen=True)
class PiiKind:
    name: str
    pattern: re.Pattern[str]
    placeholder: str


PII_KINDS: tuple[PiiKind, ...] = (
    PiiKind("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
    # Card numbers: 13-19 digits with optional spaces or dashes, checked with Luhn below.
    PiiKind("card", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), "[card number]"),
    # Egyptian national ID: 14 digits starting with 2 or 3.
    PiiKind("national_id", re.compile(r"\b[23]\d{13}\b"), "[national id]"),
    PiiKind("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[iban]"),
    # Phone numbers: international or Egyptian/Gulf mobile formats.
    # Note: bare 10-digit runs (including Gulf national IDs, which have no
    # public checksum and are indistinguishable from 10-digit phones) are
    # caught here, so they are redacted before any external call.
    PiiKind("phone", re.compile(r"(?<![\w])(?:\+\d{1,3}[\s-]?)?(?:\(?0?\d{2,3}\)?[\s-]?)?\d{3,4}[\s-]?\d{3,4}(?![\w])"), "[phone]"),
)


def _luhn(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_phone(match: str) -> bool:
    digits = re.sub(r"\D", "", match)
    # Short numbers are prices, years and counts, not phone numbers.
    return 10 <= len(digits) <= 15


def find_pii(text: str) -> dict[str, int]:
    """Counts of personal data found, by kind."""
    found: dict[str, int] = {}
    for kind in PII_KINDS:
        for m in kind.pattern.finditer(text or ""):
            value = m.group(0)
            if kind.name == "card" and not _luhn(value):
                continue
            if kind.name == "phone" and not _is_phone(value):
                continue
            found[kind.name] = found.get(kind.name, 0) + 1
    return found


def redact_pii(text: str) -> tuple[str, dict[str, int]]:
    """`text` with personal data replaced by placeholders, and what was replaced."""
    counts: dict[str, int] = {}

    def replacer(kind: PiiKind):
        def sub(m: re.Match[str]) -> str:
            value = m.group(0)
            if kind.name == "card" and not _luhn(value):
                return value
            if kind.name == "phone" and not _is_phone(value):
                return value
            counts[kind.name] = counts.get(kind.name, 0) + 1
            return kind.placeholder
        return sub

    for kind in PII_KINDS:
        text = kind.pattern.sub(replacer(kind), text or "")
    return text, counts
