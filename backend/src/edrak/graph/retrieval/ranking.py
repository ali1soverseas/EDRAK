"""Pure ranking helpers shared by the graph and hybrid retrievers (no Neo4j imports)."""

from __future__ import annotations

import re
from typing import Iterable

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')
_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "what", "which", "who", "how", "are", "was",
    "were", "our", "their", "its", "into", "about", "than", "then", "does", "did", "have", "has",
    "will", "would", "should", "could", "can", "they", "them", "versus", "compare", "comparison",
}


def lucene_query(text: str | None, max_terms: int = 12) -> str | None:
    """Turn free text into a safe OR query for db.index.fulltext.queryNodes.

    Raw user text breaks Lucene on characters like ':' or '/', so terms are escaped
    and joined with OR. Returns None when nothing searchable is left.
    """
    terms: list[str] = []
    for word in re.findall(r"[\w$%.]+", (text or "").lower(), flags=re.UNICODE):
        word = word.strip(".")
        if len(word) < 3 or word in _STOP or word in terms:
            continue
        terms.append(word)
        if len(terms) >= max_terms:
            break
    if not terms:
        return None
    return " OR ".join(_LUCENE_SPECIAL.sub(r"\\\1", t) for t in terms)


def evidence_key(source_url: str | None, fact: str | None, evidence_id: str) -> str:
    """Same source + same fact = same evidence, even when two retrievers gave it different ids."""
    fact_norm = " ".join((fact or "").lower().split())[:160]
    if source_url and fact_norm:
        return f"{source_url.strip().lower()}|{fact_norm}"
    return evidence_id


def rrf(rankings: dict[str, Iterable[str]], weights: dict[str, float] | None = None, k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion: score(key) = sum over lists of weight / (k + rank).

    Rank-based, so it needs no calibration between Chroma cosine scores and Lucene scores.
    """
    weights = weights or {}
    scores: dict[str, float] = {}
    for name, keys in rankings.items():
        w = weights.get(name, 1.0)
        for rank, key in enumerate(keys, start=1):
            scores[key] = scores.get(key, 0.0) + w / (k + rank)
    return scores
