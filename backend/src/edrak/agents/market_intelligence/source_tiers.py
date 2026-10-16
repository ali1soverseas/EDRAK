"""Configurable source-quality priors for market search ranking.

The default list is global. Pass a different tuple to rank with another brief's
sources. First matching tier wins, so put the preferred class earlier.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceTier:
    name: str
    weight: float
    domains: tuple[str, ...]


DEFAULT_SOURCE_TIERS: tuple[SourceTier, ...] = (
    SourceTier(
        "regulator",
        0.50,
        (".gov", ".gc.ca", ".europa.eu", ".go.jp", ".gouv.fr"),
    ),
    SourceTier(
        "law_firm",
        0.35,
        ("lexology.com", "jdsupra.com", "law.com", "law360.com", "legal500.com"),
    ),
    SourceTier(
        "analyst",
        0.35,
        (
            "gartner.com",
            "idc.com",
            "forrester.com",
            "mckinsey.com",
            "bcg.com",
            "statista.com",
            "deloitte.com",
            "pwc.com",
            "kpmg.com",
            "ey.com",
        ),
    ),
    SourceTier("vendor", 0.20, ()),
    SourceTier(
        "reseller",
        -0.35,
        (
            "g2.com",
            "capterra.com",
            "softwareadvice.com",
            "trustradius.com",
            "getapp.com",
            "sourceforge.net",
        ),
    ),
    SourceTier(
        "aggregator",
        -0.25,
        (
            "medium.com",
            "linkedin.com",
            "reddit.com",
            "quora.com",
            "wikipedia.org",
            "facebook.com",
            "x.com",
            "twitter.com",
        ),
    ),
)


def host_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        return host[4:]
    return host


def host_matches(host: str, pattern: str) -> bool:
    host = host.lower().removeprefix("www.")
    pattern = pattern.lower()
    if pattern.startswith("."):
        return host.endswith(pattern) or f"{pattern}." in f".{host}"
    return host == pattern or host.endswith("." + pattern)


def source_weight(
    url: str,
    tiers: tuple[SourceTier, ...] = DEFAULT_SOURCE_TIERS,
    vendor_domains: tuple[str, ...] = (),
) -> float:
    """Return the tier weight for this URL. Unknown hosts score 0."""
    host = host_of(url)
    if not host:
        return 0.0
    for tier in tiers:
        domains = tier.domains
        if tier.name == "vendor" and vendor_domains:
            domains = domains + tuple(vendor_domains)
        if any(host_matches(host, domain) for domain in domains):
            return tier.weight
    return 0.0
