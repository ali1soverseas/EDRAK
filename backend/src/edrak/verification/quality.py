"""Deterministic source quality, claim-type checks, and request coverage."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from edrak.agents.market_intelligence.source_tiers import (
    DEFAULT_SOURCE_TIERS,
    host_of,
    source_weight,
)
from edrak.contracts import (
    BusinessRequest,
    Evidence,
    EvidenceQuality,
    SourceType,
    WorkerResult,
    WorkerType,
)

MIN_CONFIDENCE = 0.60

_ANNOUNCE_PATHS = (
    "/docs",
    "/documentation",
    "/pricing",
    "/changelog",
    "/press",
    "/newsroom",
    "/announcement",
    "/blog/news",
    "/company-news",
    "/release-notes",
)
_WEAK_PATHS = ("/discussions", "/community", "/orgs/", "/search", "/comments")
_NEWS_HOSTS = (
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "techcrunch.com",
    "helpnetsecurity.com",
    "theregister.com",
    "zdnet.com",
    "wired.com",
    "cnbc.com",
    "forbes.com",
)
_PUBLICATION_HOSTS = ("arxiv.org", "doi.org", "acm.org", "ieee.org", "ssrn.com")
_KNOWN_VENDOR_HOSTS = {
    "github": ("github.com", "github.blog", "docs.github.com"),
    "gitlab": ("gitlab.com", "about.gitlab.com", "docs.gitlab.com"),
    "microsoft": ("microsoft.com", "learn.microsoft.com", "azure.microsoft.com"),
    "atlassian": ("atlassian.com", "bitbucket.org"),
}
_TYPE_FALLBACK_HIGH = {
    SourceType.OFFICIAL_DOCUMENTATION,
    SourceType.PRICING_PAGE,
    SourceType.RELEASE_NOTES,
    SourceType.ANNOUNCEMENT,
    SourceType.REGULATORY,
    SourceType.MARKET_REPORT,
}
_TYPE_FALLBACK_MEDIUM = {
    SourceType.NEWS_ARTICLE,
    SourceType.INTERNAL_DOCUMENT,
    SourceType.ECONOMIC,
}
_STOP = {
    "that",
    "this",
    "with",
    "from",
    "have",
    "been",
    "were",
    "their",
    "about",
    "into",
    "over",
    "than",
    "such",
    "also",
    "only",
    "does",
    "each",
    "same",
    "when",
    "what",
    "which",
    "while",
    "where",
    "after",
    "before",
    "between",
    "without",
    "including",
    "across",
    "versus",
    "against",
}
_TOPIC_OWNERS = (
    (("tam", "growth", "spend", "cagr", "demand", "buyer", "barrier", "regulat", "residenc", "packag", "pric", "category", "lifecycle"), WorkerType.MARKET_INTELLIGENCE),
    (("competitor", "github", "copilot", "atlassian", "azure devops"), WorkerType.COMPETITOR_INTELLIGENCE),
    (("customer", "sentiment", "buyer need", "win/loss", "adoption"), WorkerType.CUSTOMER_TRENDS),
    (("internal", "architecture", "telemetry", "self-hosted", "ai gateway"), WorkerType.INTERNAL_INTELLIGENCE),
)
_PRICE_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_NUMBER_RE = re.compile(r"\b\d+(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b|\b\d+\b")
_FREE_RE = re.compile(r"\b(included|no extra cost|at no extra|free)\b")


def vendor_hosts(request: BusinessRequest | None) -> tuple[str, ...]:
    if request is None:
        return ()
    names = [
        request.company_profile.name,
        *request.company_profile.aliases,
        *request.business_context.targets,
        *request.company_profile.products,
    ]
    hosts: list[str] = []
    seen: set[str] = set()
    for name in names:
        first = re.sub(r"[^a-z0-9]+", "", name.lower().split()[0]) if name else ""
        extra = _KNOWN_VENDOR_HOSTS.get(first, (f"{first}.com",) if first else ())
        for host in extra:
            if host not in seen:
                seen.add(host)
                hosts.append(host)
    return tuple(hosts)


def quality_from_url(
    url: str | None,
    *,
    source_type: SourceType | None = None,
    vendor_domains: tuple[str, ...] = (),
    is_synthetic: bool = False,
) -> EvidenceQuality:
    """Quality from the URL and shared source tiers, not the worker's enum."""
    if is_synthetic:
        return EvidenceQuality.MEDIUM
    host = host_of(url or "")
    path = (urlparse(url or "").path or "").lower()
    if host:
        weight = source_weight(url or "", DEFAULT_SOURCE_TIERS, vendor_domains)
        if weight <= -0.20:
            return EvidenceQuality.LOW
        if any(marker in path for marker in _WEAK_PATHS):
            return EvidenceQuality.LOW
        if weight >= 0.35:
            return EvidenceQuality.HIGH
        if any(host == item or host.endswith("." + item) for item in _NEWS_HOSTS):
            return EvidenceQuality.MEDIUM
        if any(host == item or host.endswith("." + item) for item in _PUBLICATION_HOSTS):
            return EvidenceQuality.MEDIUM
        if weight >= 0.20:
            if any(marker in path for marker in _ANNOUNCE_PATHS) or host.startswith("docs."):
                return EvidenceQuality.HIGH
            return EvidenceQuality.MEDIUM
        if any(marker in path for marker in _ANNOUNCE_PATHS) and vendor_domains:
            if any(host == item or host.endswith("." + item) for item in vendor_domains):
                return EvidenceQuality.HIGH
        return EvidenceQuality.LOW
    if source_type in _TYPE_FALLBACK_HIGH:
        return EvidenceQuality.HIGH
    if source_type in _TYPE_FALLBACK_MEDIUM:
        return EvidenceQuality.MEDIUM
    return EvidenceQuality.LOW


def best_quality(
    evidence: list[Evidence],
    vendor_domains: tuple[str, ...] = (),
) -> EvidenceQuality:
    if not evidence:
        return EvidenceQuality.LOW
    ranks = {
        EvidenceQuality.LOW: 0,
        EvidenceQuality.MEDIUM: 1,
        EvidenceQuality.HIGH: 2,
    }
    best = EvidenceQuality.LOW
    for item in evidence:
        quality = quality_from_url(
            item.source_url,
            source_type=item.source_type,
            vendor_domains=vendor_domains,
            is_synthetic=item.is_synthetic,
        )
        if ranks[quality] > ranks[best]:
            best = quality
    return best


def source_text(item: Evidence) -> str:
    return f"{item.extracted_fact or ''} {item.excerpt or ''}"


def claim_quantities(text: str) -> set[str]:
    lowered = text.lower().replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
    found: set[str] = set()
    for match in _PRICE_RE.findall(lowered):
        found.add(f"${match.lstrip('0') or '0'}" if match != "0" else "$0")
        found.add(match.lstrip("0") or "0")
    for match in _PERCENT_RE.findall(lowered):
        found.add(f"{match}%")
    for match in _YEAR_RE.findall(lowered):
        found.add(match)
    for match in _NUMBER_RE.findall(lowered):
        compact = match.replace(",", "")
        if compact in {item.replace("$", "").replace("%", "") for item in found}:
            continue
        if _YEAR_RE.fullmatch(compact):
            continue
        found.add(compact)
    return found


def content_terms(text: str) -> list[str]:
    found: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text.lower()):
        if word in _STOP or word in found:
            continue
        found.append(word)
    return found


def quantities_missing(statement: str, evidence: list[Evidence]) -> list[str]:
    needed = claim_quantities(statement)
    if not needed:
        return []
    combined = " ".join(source_text(item) for item in evidence)
    present = claim_quantities(combined)
    missing = sorted(token for token in needed if token not in present and token.lstrip("$") not in present)
    return missing


def overlap_terms(statement: str, evidence: list[Evidence]) -> list[str]:
    source = set(content_terms(" ".join(source_text(item) for item in evidence)))
    return [term for term in content_terms(statement) if term in source]


def support_strength(statement: str, evidence: list[Evidence]) -> str:
    """yes = excerpt backs the claim; no = it does not; uncertain = needs a model check."""
    if not evidence:
        return "no"
    missing = quantities_missing(statement, evidence)
    if missing:
        return "no"
    terms = content_terms(statement)
    matched = overlap_terms(statement, evidence)
    if claim_quantities(statement):
        return "yes"
    if not terms:
        return "uncertain"
    if len(matched) >= min(3, len(terms)) or (len(matched) / max(len(terms), 1) >= 0.5 and matched):
        return "yes"
    if matched:
        return "uncertain"
    return "no"


def extract_prices(text: str) -> set[str]:
    return {f"${match}" for match in _PRICE_RE.findall(text.lower())}


def price_conflicts(evidence: list[Evidence]) -> list[str]:
    """Flag disjoint singleton prices on different URLs, not two SKUs on one page."""
    by_url: dict[str, set[str]] = {}
    free_urls: set[str] = set()
    for item in evidence:
        url = (item.source_url or item.source_title or item.evidence_id).lower()
        text = source_text(item)
        by_url.setdefault(url, set()).update(extract_prices(text))
        if _FREE_RE.search(text.lower()) and not extract_prices(text):
            free_urls.add(url)
    priced = {url: prices for url, prices in by_url.items() if len(prices) == 1}
    urls = list(priced)
    for left in range(len(urls)):
        for right in range(left + 1, len(urls)):
            if priced[urls[left]] != priced[urls[right]]:
                return ["Sources report different prices for the same claim."]
    if priced and free_urls - set(priced):
        return ["Sources report different pricing or packaging for the same claim."]
    return []


def evidence_key(item: Evidence) -> tuple[str, str]:
    url = (item.source_url or item.source_title or "").strip().lower().rstrip("/")
    text = re.sub(r"\s+", " ", source_text(item)).strip().lower()[:400]
    return (url, text)


def dedupe_evidence(items: list[Evidence]) -> list[Evidence]:
    unique: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def research_topics(request: BusinessRequest) -> list[str]:
    goal = request.goal or ""
    match = re.search(r"analyze\s+\d+\s+topics:\s*(.+)$", goal, flags=re.IGNORECASE)
    if match:
        return [part.strip(" .") for part in match.group(1).split(";") if part.strip()]
    return []


def topic_owner(topic: str, default: WorkerType | None = None) -> WorkerType | None:
    lowered = topic.lower()
    for markers, worker in _TOPIC_OWNERS:
        if any(marker in lowered for marker in markers):
            return worker
    return default


def coverage_gaps(
    request: BusinessRequest,
    outputs: list[WorkerResult],
) -> dict[WorkerType, list[str]]:
    """Topics from the request that present workers did not address. Absent workers are skipped."""
    topics = research_topics(request)
    if not topics:
        return {}
    present = {result.worker for result in outputs}
    statements_by_worker = {
        result.worker: " ".join(finding.statement for finding in result.findings)
        for result in outputs
    }
    gaps: dict[WorkerType, list[str]] = {}
    for topic in topics:
        owner = topic_owner(topic, next(iter(present), None))
        if owner is None or owner not in present:
            continue
        haystack = statements_by_worker.get(owner, "")
        needed = [term for term in content_terms(topic)[:8]]
        matched = [term for term in needed if term in haystack.lower()]
        if needed and len(matched) < min(2, len(needed)):
            gaps.setdefault(owner, []).append(f"No finding covering: {topic[:160]}")
    return gaps
