"""Theme label normalization, shared by theme analysis and evidence queries."""

import re

from edrak.agents.customer_trends.utils.text import search_key

_WORD = re.compile(r"\w+")
_STOPWORDS = frozenset({"a", "an", "the", "of", "and", "for", "to", "in", "on", "with", "about"})
_ES_ENDINGS = ("sses", "shes", "ches", "xes", "zes")
_KEEP_ENDINGS = ("ss", "us", "is")
_MIN_PLURAL_LENGTH = 4


def _singular(word: str) -> str:
    """Strip a plain English plural. Words in other scripts are left alone."""
    if len(word) < _MIN_PLURAL_LENGTH or not word.isascii():
        return word
    if word.endswith("ies") and len(word) > _MIN_PLURAL_LENGTH:
        return word[:-3] + "y"
    if word.endswith(_ES_ENDINGS):
        return word[:-2]
    if word.endswith("s") and not word.endswith(_KEEP_ENDINGS):
        return word[:-1]
    return word


def label_tokens(label: str) -> list[str]:
    """The words of a label, case-folded, without punctuation, stopwords or plurals."""
    words = _WORD.findall(search_key(label))
    kept = [_singular(word) for word in words if word not in _STOPWORDS]
    return kept or [_singular(word) for word in words]


def normalize_label(label: str) -> str:
    """Key under which spellings of one theme label compare equal."""
    return " ".join(label_tokens(label))


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0
