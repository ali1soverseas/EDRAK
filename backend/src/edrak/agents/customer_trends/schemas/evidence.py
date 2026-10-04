"""One collected piece of public evidence, and the filters used to query stored evidence."""

from datetime import date
from typing import Annotated, Any

from pydantic import Field, StringConstraints

from edrak.agents.customer_trends.schemas.common import (
    LanguageCode,
    NonEmpty,
    Platform,
    SourceType,
    StrictModel,
    UtcDatetime,
)

Sha1Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
EvidenceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]

_NOT_ENGAGEMENT = frozenset({"rating", "views"})


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
