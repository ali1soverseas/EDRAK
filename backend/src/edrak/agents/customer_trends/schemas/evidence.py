"""One collected piece of public evidence, and the filters used to query stored evidence."""

from datetime import date
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints

from edrak.agents.customer_trends.schemas.common import (
    LanguageCode,
    NonEmpty,
    Platform,
    SourceType,
    StrictModel,
    UtcDatetime,
)
from edrak.agents.customer_trends.utils.text import truncate
from edrak.contracts import Evidence as SharedEvidence
from edrak.contracts import SourceType as SharedSourceType

Sha1Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
EvidenceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]

_NOT_ENGAGEMENT = frozenset({"rating", "views"})
FACT_CHARS = 280
EXCERPT_CHARS = 500


class EvidenceItem(StrictModel):
    id: EvidenceId
    run_id: NonEmpty
    task_id: NonEmpty
    batch_id: NonEmpty
    source_type: SourceType
    platform: Platform | None = None
    url: str | None = None
    author: str | None = None
    text: NonEmpty
    language: LanguageCode | None = None
    published_at: UtcDatetime | None = None
    collected_at: UtcDatetime
    engagement: dict[str, int] = Field(default_factory=dict)
    snippet_only: bool = False
    provider: NonEmpty
    content_hash: Sha1Hex
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def engagement_total(self) -> int:
        """Sum of interaction counts: ratings and view counts are not interactions."""
        return sum(v for k, v in self.engagement.items() if k not in _NOT_ENGAGEMENT)


class EvidenceFilters(StrictModel):
    """The filters of the evidence_query tool. Date bounds apply to `published_at`."""

    platform: Platform | None = None
    source_type: SourceType | None = None
    language: LanguageCode | None = None
    text_contains: str | None = None
    theme: str | None = None
    min_engagement: int | None = Field(default=None, ge=0)
    since: date | None = None
    until: date | None = None
    batch_ids: list[str] | None = None


# EvidenceItem to the shared Evidence (SPEC 6.0). The shared record has no field for the platform,
# the engagement or the language, so they travel in `metadata`.

_SHARED_SOURCE_TYPES = {
    SourceType.WEB: SharedSourceType.WEB_PAGE,
    SourceType.NEWS: SharedSourceType.NEWS_ARTICLE,
    SourceType.REVIEW: SharedSourceType.REVIEW_SITE,
    SourceType.SOCIAL_POST: SharedSourceType.OTHER,
    SourceType.SOCIAL_COMMENT: SharedSourceType.OTHER,
    SourceType.TREND_POINT: SharedSourceType.OTHER,
}


def to_shared_evidence(item: EvidenceItem, *, synthetic: bool = False) -> SharedEvidence:
    """A compact shared record: a short fact, a longer excerpt only when the text is long, and
    where the item came from. Never the whole stored text of a long item."""
    source_type = _SHARED_SOURCE_TYPES[item.source_type]
    if item.source_type is SourceType.WEB and item.snippet_only:
        source_type = SharedSourceType.SEARCH_RESULT
    host = urlsplit(item.url).hostname if item.url else None
    details: dict[str, Any] = {
        "kind": item.source_type.value,
        "platform": item.platform.value if item.platform else None,
        "language": item.language,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "engagement": item.engagement or None,
        "snippet_only": item.snippet_only or None,
        "provider": item.provider,
    }
    return SharedEvidence(
        evidence_id=item.id,
        source_type=source_type,
        source_url=item.url,
        publisher=item.platform.value if item.platform else host,
        extracted_fact=truncate(item.text, FACT_CHARS),
        excerpt=truncate(item.text, EXCERPT_CHARS) if len(item.text) > FACT_CHARS else None,
        retrieved_at=item.collected_at,
        is_synthetic=synthetic,
        metadata={key: value for key, value in details.items() if value is not None},
    )
