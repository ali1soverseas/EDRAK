"""Stage B: match terms from the task and the company, and the free check for them."""

import re
from collections.abc import Iterable
from re import Pattern

from edrak.agents.customer_trends.relevance.normalize import normalize
from edrak.agents.customer_trends.schemas.task import TaskBrief

MIN_TERM_LENGTH = 2
_ARABIC = re.compile("[؀-ۿݐ-ݿ]")


def match_terms(brief: TaskBrief) -> list[str]:
    """The names worth finding in a comment: the company and its aliases, its products, the
    competitors named in the task and the task's focus keywords. Normalized, without repeats."""
    names: Iterable[str] = [
        brief.entity,
        *brief.aliases,
        *brief.products,
        *brief.competitors,
        *brief.focus_terms,
    ]
    terms = (normalize(name) for name in names)
    return list(dict.fromkeys(t for t in terms if len(t) >= MIN_TERM_LENGTH))


def _pattern(term: str) -> Pattern[str]:
    """A Latin term must match whole words. An Arabic term matches inside a word, because Arabic
    attaches prefixes (the, and, with, to) to the word they belong to."""
    escaped = re.escape(term)
    return re.compile(escaped if _ARABIC.search(term) else rf"(?<!\w){escaped}(?!\w)")


class TermMatcher:
    def __init__(self, terms: Iterable[str]) -> None:
        """Terms are normalized here too (normalizing twice changes nothing), so a caller can pass
        names as they are written."""
        cleaned = (normalize(term) for term in terms)
        self.terms = list(dict.fromkeys(t for t in cleaned if len(t) >= MIN_TERM_LENGTH))
        self._patterns = [_pattern(term) for term in self.terms]

    def __call__(self, text: str) -> bool:
        """Whether `text` holds a term. The text is normalized here, the way the terms were."""
        if not self._patterns:
            return False
        normalized = normalize(text)
        return any(pattern.search(normalized) for pattern in self._patterns)
