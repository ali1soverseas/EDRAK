"""Which search results the competitor worker is allowed to read.

A result is kept only when it is about the competitor and comes from a source worth reading:

  * the competitor's own site (official) is always kept when it has text;
  * aggregators, resellers, social feeds and SEO content farms are dropped (social and review
    content is the Customer worker's job, and listicles restate vendor pages badly);
  * any other page must name the competitor and carry enough text to state a fact.

Official domains are registered per run from the competitors' real websites (the task's
CompetitorRef.website, or what discovery found), never from a hard-coded table.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Iterable
from urllib.parse import urlparse

from edrak.agents.market_intelligence.source_tiers import DEFAULT_SOURCE_TIERS, source_weight

# SEO content farms, AI-written comparison sites and coupon/directory pages seen in real runs.
# A page from these hosts restates other pages; the worker reads the original instead.
LOW_VALUE_HOSTS: tuple[str, ...] = (
    "eesel.ai",
    "checkthat.ai",
    "tech-insider.org",
    "cicdcalculator.com",
    "microsoftnegotiations.com",
    "slashdot.org",
    "saasworthy.com",
    "softwaresuggest.com",
    "crozdesk.com",
    "alternativeto.net",
    "similarweb.com",
    "crunchbase.com",
    "zoominfo.com",
    "owler.com",
    "craft.co",
    "comparably.com",
    "producthunt.com",
    "pinterest.com",
    "scribd.com",
    "slideshare.net",
    "studocu.com",
    "coursehero.com",
)
# Social and community hosts belong to the Customer worker.
SOCIAL_HOSTS: tuple[str, ...] = (
    "x.com", "twitter.com", "facebook.com", "instagram.com", "tiktok.com", "youtube.com",
    "reddit.com", "quora.com", "linkedin.com", "medium.com",
)
MIN_TEXT_CHARS = 200

_registry: dict[str, set[str]] = {}
_lock = threading.Lock()


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def host_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def domain_of(url_or_host: str | None) -> str:
    if not url_or_host:
        return ""
    value = url_or_host if "://" in url_or_host else "https://" + url_or_host
    return host_of(value)


def _matches(host: str, domain: str) -> bool:
    return bool(domain) and (host == domain or host.endswith("." + domain))


def register_official_domains(competitor: str, domains: Iterable[str]) -> None:
    """Remember a competitor's official domains for this process (same name, same company)."""
    clean = {domain_of(d) for d in domains if d}
    clean.discard("")
    if not clean:
        return
    with _lock:
        _registry.setdefault(competitor.strip().lower(), set()).update(clean)


def official_domains(competitor: str) -> list[str]:
    with _lock:
        return sorted(_registry.get(competitor.strip().lower(), set()))


def is_official(url: str, competitor: str) -> bool:
    """The URL is on the competitor's own site.

    A registered domain is authoritative. Without one, a host whose first label is the
    competitor's compact name (kanzpay.com for "Kanz Pay") counts as official.
    """
    host = host_of(url)
    if not host:
        return False
    domains = official_domains(competitor)
    if domains:
        return any(_matches(host, d) for d in domains)
    name = compact(competitor)
    if len(name) < 4:
        return False
    labels = host.split(".")
    return any(label == name for label in labels[:-1])


def _names(competitor: str, aliases: Iterable[str] = ()) -> list[str]:
    names = [competitor, *aliases]
    out: list[str] = []
    for n in names:
        n = (n or "").strip()
        if n and n.lower() not in {o.lower() for o in out}:
            out.append(n)
    return out


def mentions(text: str, competitor: str, aliases: Iterable[str] = ()) -> bool:
    """The text names the competitor (any alias, ignoring case, spacing and punctuation)."""
    lowered = (text or "").lower()
    squashed = compact(text)
    for name in _names(competitor, aliases):
        if name.lower() in lowered:
            return True
        c = compact(name)
        if len(c) >= 4 and c in squashed:
            return True
    return False


def judge(result: dict[str, Any], competitor: str, aliases: Iterable[str] = ()) -> tuple[bool, str]:
    """(keep, reason). `result` has url, title and text."""
    url = result.get("url") or ""
    host = host_of(url)
    text = result.get("text") or ""
    if not host:
        return False, "no url"
    if is_official(url, competitor):
        return (True, "official") if len(text.strip()) >= 80 else (False, "official page without text")
    if any(_matches(host, h) for h in LOW_VALUE_HOSTS):
        return False, "low-value site"
    if any(_matches(host, h) for h in SOCIAL_HOSTS):
        return False, "social or community site (the Customer worker reads these)"
    if source_weight(url, DEFAULT_SOURCE_TIERS) <= -0.25:
        return False, "reseller or aggregator"
    if len(text.strip()) < MIN_TEXT_CHARS:
        return False, "too little text"
    if not mentions(f"{result.get('title') or ''}\n{text[:6000]}", competitor, aliases):
        return False, "does not name the competitor"
    return True, "relevant"


def best_passage(text: str, statement: str, limit: int = 500) -> str:
    """The sentences of `text` that share the most words with `statement`, up to `limit` chars."""
    words = {w for w in re.findall(r"[a-z0-9]{4,}", statement.lower())}
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if len(s.strip()) > 20]
    if not sentences:
        return (text or "")[:limit]
    scored = sorted(
        enumerate(sentences),
        key=lambda pair: (-len(words & set(re.findall(r"[a-z0-9]{4,}", pair[1].lower()))), pair[0]),
    )
    chosen: list[tuple[int, str]] = []
    size = 0
    for index, sentence in scored:
        if size + len(sentence) > limit and chosen:
            break
        chosen.append((index, sentence))
        size += len(sentence) + 1
    return " ".join(s for _, s in sorted(chosen))[:limit]
