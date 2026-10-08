"""Provider contract: protocol, result model, error types, capability names and call params."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Any, Literal, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from edrak.agents.customer_trends.schemas.common import (
    Platform,
    ReviewStore,
    SourceType,
    StrictModel,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.agents.customer_trends.utils.text import (
    content_hash,
    detect_language,
    evidence_id,
)

PENDING_BATCH = "pending"

# Capability names are "<kind>" or "<kind>:<variant>", for example "social_search:x",
# "reviews:google_play", "news:gdelt", "web_search". The variant is a platform, a review
# store or a news source, depending on the kind.
_PLATFORMS = frozenset(p.value for p in Platform)
CAPABILITY_VARIANTS: dict[str, frozenset[str] | None] = {
    "web_search": None,
    "fetch_page": None,
    "search_interest": None,
    "social_search": _PLATFORMS,
    "social_comments": _PLATFORMS,
    "reviews": frozenset(s.value for s in ReviewStore),
    "news": frozenset({"gdelt", "google_news"}),
}

_RELATIVE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE)
RELATIVE_UNIT_DAYS = {
    "minute": 1 / 1440,
    "hour": 1 / 24,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}
_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "igshid", "ref", "ref_src"})


def make_capability(kind: str, variant: str | None = None) -> str:
    """Build a capability name, rejecting unknown kinds and variants."""
    if kind not in CAPABILITY_VARIANTS:
        raise ValueError(f"unknown capability kind: {kind!r}")
    allowed = CAPABILITY_VARIANTS[kind]
    if allowed is None:
        if variant is not None:
            raise ValueError(f"capability {kind!r} takes no variant")
        return kind
    if variant not in allowed:
        raise ValueError(f"capability {kind!r} needs one of {sorted(allowed)}, got {variant!r}")
    return f"{kind}:{variant}"


def parse_capability(capability: str) -> tuple[str, str | None]:
    kind, _, variant = capability.partition(":")
    make_capability(kind, variant or None)
    return kind, variant or None


class ProviderError(Exception):
    """A provider could not serve a call. The registry turns these into fallback."""


class ProviderNotConfigured(ProviderError):
    """Missing or rejected credentials."""


class ProviderRateLimited(ProviderError):
    retryable = True

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ProviderQuotaExceeded(ProviderError):
    """A daily, credit or usage limit is used up."""


class ProviderUnavailable(ProviderError):
    """Timeouts, connection failures and 5xx responses."""

    retryable = True


class ProviderBadResponse(ProviderError):
    """The provider answered, but not with something usable."""


@dataclass(frozen=True)
class ProviderFailure:
    provider: str
    error: str
    message: str
    skipped: bool = False


class ProviderExhausted(ProviderError):
    """Every provider for a capability failed or was skipped."""

    def __init__(self, capability: str, failures: list[ProviderFailure]) -> None:
        self.capability = capability
        self.failures = failures
        detail = "; ".join(f"{f.provider}: {f.error}" for f in failures) or "no provider routed"
        super().__init__(f"{capability}: all providers failed ({detail})")


class ProviderResult(StrictModel):
    """What a provider returns. `provider` and `fallback_used` are set by the registry."""

    items: list[EvidenceItem] | list[TrendSeries] = Field(default_factory=list)
    raw_count: int = 0
    cost_estimate: float = Field(default=0.0, ge=0)
    next_cursor: str | None = None
    warnings: list[str] = Field(default_factory=list)
    partial: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)
    provider: str = ""
    fallback_used: bool = False

    def restamped(self, run_id: str, task_id: str) -> "ProviderResult":
        """The same result for another run, with its evidence items carrying that run's ids."""
        if not self.items or not isinstance(self.items[0], EvidenceItem):
            return self
        stamped = [
            item.model_copy(update={"run_id": run_id, "task_id": task_id})
            for item in self.items
            if isinstance(item, EvidenceItem)
        ]
        return self.model_copy(update={"items": stamped})


class Provider(Protocol):
    name: str
    capabilities: set[str]

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult: ...


class CallParams(BaseModel):
    """The common keys of a provider call. Capability-specific keys are kept as extras."""

    model_config = ConfigDict(extra="allow")

    run_id: str
    task_id: str
    query: str = ""
    languages: list[str] = Field(default_factory=lambda: ["ar", "en"])
    geo: str | None = None
    since: date | None = None
    until: date | None = None
    max_results: int | None = Field(default=None, ge=1)
    cursor: str | None = None
    post_url: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    sort: Literal["recent", "top"] = "recent"
    keywords: list[str] = Field(default_factory=list)
    timeframe: str = "today 12-m"
    target: str | None = None
    country: str | None = None
    url: str | None = None
    max_chars: int = Field(default=20000, ge=1)

    @property
    def wanted(self) -> int:
        return self.max_results or 10


def parse_relative_date(value: Any, now: datetime) -> datetime | None:
    """'3 days ago' and similar English phrases, resolved against `now`."""
    match = _RELATIVE.search(value) if isinstance(value, str) else None
    if match is None:
        return None
    days = int(match.group(1)) * RELATIVE_UNIT_DAYS[match.group(2).lower()]
    return now - timedelta(days=days)


def infer_granularity(days: list[date]) -> Literal["day", "week", "month"]:
    """Day, week or month, from the typical gap between the dates of a series."""
    gaps = [(later - earlier).days for earlier, later in zip(days, days[1:], strict=False)]
    typical = median(gaps) if gaps else 7
    if typical <= 1.5:
        return "day"
    return "week" if typical <= 10 else "month"


def in_window(published: datetime | None, since: date | None, until: date | None) -> bool:
    """False only when `published` is known and falls outside the inclusive day range."""
    if published is None:
        return True
    day = published.astimezone(UTC).date()
    return not ((since and day < since) or (until and day > until))


def canonical_url(url: str) -> str:
    """Lowercase scheme and host, no fragment, no tracking parameters, no trailing slash."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def new_evidence(
    params: CallParams,
    *,
    provider: str,
    source_type: SourceType,
    text: str,
    platform: Platform | None = None,
    url: str | None = None,
    author: str | None = None,
    published_at: datetime | None = None,
    engagement: dict[str, int] | None = None,
    snippet_only: bool = False,
    language: str | None = None,
    key: str | None = None,
    metadata: dict[str, Any] | None = None,
    collected_at: datetime,
) -> EvidenceItem:
    """Build an item with its deterministic id and content hash.

    The id comes from the platform and the canonical URL (or `key` when given), never from
    the provider, so the same post found through two providers gets one id.
    """
    digest = content_hash(text)
    source_key = key or (canonical_url(url) if url else digest)
    return EvidenceItem(
        id=evidence_id(f"{platform.value if platform else 'none'}|{source_key}"),
        run_id=params.run_id,
        task_id=params.task_id,
        batch_id=PENDING_BATCH,
        source_type=source_type,
        platform=platform,
        url=url,
        author=author,
        text=text,
        language=language or detect_language(text),
        published_at=published_at,
        collected_at=collected_at,
        engagement=engagement or {},
        snippet_only=snippet_only,
        provider=provider,
        content_hash=digest,
        metadata=metadata or {},
    )
