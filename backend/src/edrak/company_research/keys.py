"""Match a key or label a model proposed to a canonical fact key. Plain code, no model call.

A phrase is folded the same way on both sides before it is compared: case, Arabic spelling variants
(through the shared `search_key`), punctuation, and English plurals. A phrase that matches nothing in
the category returns None, and the caller decides what to do with it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

from edrak.agents.customer_trends.utils.text import search_key
from edrak.company_research.models import FactKeySpec

_APOSTROPHES = re.compile("['’‘`´]")
_SPACE = re.compile(r"\s+")
_LATIN_WORD = re.compile(r"^[a-z]+$")


def _singular(word: str) -> str:
    """A simple English singular for a folded word. Only used to compare two phrases, never shown."""
    if not _LATIN_WORD.match(word) or len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "ches", "shes", "xes")):
        return word[:-2]
    if word.endswith(("ss", "us", "is")):
        return word
    return word[:-1] if word.endswith("s") else word


def normalize_phrase(text: str) -> str:
    """`text` folded for comparison: lowercase, Arabic variants joined, punctuation gone, plurals singular."""
    folded = search_key(_APOSTROPHES.sub("", text or ""))
    spaced = "".join(" " if unicodedata.category(c)[0] in ("P", "S") else c for c in folded)
    return " ".join(_singular(word) for word in _SPACE.split(spaced.strip()) if word)


def _phrases(spec: FactKeySpec) -> Iterable[str]:
    yield spec.key
    yield spec.label_en
    yield spec.label_ar
    yield from spec.synonyms.en
    yield from spec.synonyms.ar


def build_index(keys: Iterable[FactKeySpec]) -> dict[str, dict[str, str]]:
    """category -> folded phrase -> canonical key. The first key to claim a phrase in a category keeps it."""
    index: dict[str, dict[str, str]] = {}
    for spec in keys:
        phrases = index.setdefault(spec.category, {})
        for phrase in _phrases(spec):
            folded = normalize_phrase(phrase)
            if folded:
                phrases.setdefault(folded, spec.key)
    return index


_seed_index: Optional[dict[str, dict[str, str]]] = None


def normalize_key(category: str, proposed: str, keys: Optional[Iterable[FactKeySpec]] = None) -> Optional[str]:
    """The canonical key in `category` that `proposed` names, or None.

    `proposed` may be a snake_case key, an English or Arabic label, or any synonym. `keys` are the
    canonical keys to match against (the seeds and any promoted ones); the seeds are used when it is
    not given.
    """
    global _seed_index
    if keys is None:
        if _seed_index is None:
            from edrak.company_research.seed_keys import SEED_KEYS

            _seed_index = build_index(SEED_KEYS)
        index = _seed_index
    else:
        index = build_index(keys)
    folded = normalize_phrase(proposed)
    if not folded:
        return None
    return index.get(category, {}).get(folded)
