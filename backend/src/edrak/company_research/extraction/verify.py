"""Check, with plain code, that each extracted fact quotes the source it cites. No model call.

The quote and the source are normalized the same way (Unicode form, Arabic diacritics and spelling
variants, digits, case, punctuation, whitespace, line-end hyphens) and the quote must be a substring
of the source. Text read by OCR may differ from the quote by a few characters, so it is matched with
a configured similarity instead. A fact whose quote is not found, or that cites a source that was not
sent, is dropped.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Mapping, Sequence

from edrak.agents.customer_trends.utils.text import search_key
from edrak.company_research.extraction.schema import ExtractedFact
from edrak.company_research.extraction.sources import SourceDoc

MIN_QUOTE_CHARS = 8  # a shorter quote proves too little
_LINE_HYPHEN = re.compile(r"(\w)-[ \t]*\r?\n\s*(\w)")
_SPACE = re.compile(r"\s+")
# Arabic-Indic and Eastern Arabic-Indic digits to ASCII.
_DIGITS = {0x0660 + i: ord("0") + i for i in range(10)} | {0x06F0 + i: ord("0") + i for i in range(10)}


def normalize_for_match(text: str) -> str:
    """`text` reduced to letters and digits in lowercase words, the same way for a quote and its source."""
    folded = search_key(_LINE_HYPHEN.sub(r"\1\2", text or "")).translate(_DIGITS)
    out = []
    for char in folded:
        category = unicodedata.category(char)
        if category[0] in ("L", "N"):
            out.append(char)
        elif category in ("Cf", "Mn", "Me"):
            continue  # invisible marks do not split a word
        else:
            out.append(" ")  # punctuation, symbols and spaces
    return _SPACE.sub(" ", "".join(out)).strip()


def similar_span(quote: str, source: str, tolerance: float) -> bool:
    """Whether some run of words in `source` is at least `tolerance` like `quote` (both normalized).

    Windows of the quote's length in words, one word shorter and one longer, are compared, so a word
    that OCR split or joined still matches.
    """
    q_words, s_words = quote.split(), source.split()
    if not q_words or not s_words:
        return False
    for size in sorted({max(1, len(q_words) - 1), len(q_words), len(q_words) + 1}):
        for start in range(max(1, len(s_words) - size + 1)):
            window = " ".join(s_words[start : start + size])
            matcher = SequenceMatcher(None, quote, window, autojunk=False)
            if matcher.real_quick_ratio() >= tolerance and matcher.quick_ratio() >= tolerance and matcher.ratio() >= tolerance:
                return True
    return False


@dataclass
class Verified:
    fact: ExtractedFact
    doc: SourceDoc
    exact: bool  # the quote is in the source as written, not only close to it


@dataclass
class Verification:
    kept: list[Verified]
    unknown_source: int = 0
    no_quote: int = 0  # the quote is missing, too short, or not in the source


def source_id(raw: str) -> str:
    return raw.strip().strip("[]").strip().upper()


def verify_facts(facts: Sequence[ExtractedFact], sources: Mapping[str, SourceDoc], ocr_tolerance: float) -> Verification:
    """The facts whose source was sent and whose quote is in it. `sources` maps an id to its document."""
    out = Verification(kept=[])
    cache: dict[str, str] = {}
    for fact in facts:
        doc = sources.get(source_id(fact.source))
        if doc is None:
            out.unknown_source += 1
            continue
        quote = normalize_for_match(fact.quote)
        if len(quote) < MIN_QUOTE_CHARS:
            out.no_quote += 1
            continue
        if doc.id not in cache:
            cache[doc.id] = normalize_for_match(doc.clean or doc.text)
        text = cache[doc.id]
        if quote in text:
            out.kept.append(Verified(fact, doc, exact=True))
        elif doc.ocr and similar_span(quote, text, ocr_tolerance):
            out.kept.append(Verified(fact, doc, exact=False))
        else:
            out.no_quote += 1
    return out
