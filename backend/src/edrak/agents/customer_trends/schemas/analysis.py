"""Theme analysis and computed metric records."""

from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, StringConstraints

from edrak.agents.customer_trends.schemas.common import NonEmpty, StrictModel

MAX_QUOTES = 3
MAX_QUOTE_CHARS = 240

Quote = Annotated[str, StringConstraints(min_length=1, max_length=MAX_QUOTE_CHARS)]


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"


class Theme(StrictModel):
    label: NonEmpty
    description: str = ""
    sentiment: Sentiment
    evidence_ids: list[str] = Field(default_factory=list)
    representative_quotes: list[Quote] = Field(default_factory=list, max_length=MAX_QUOTES)


class ThemeAggregate(StrictModel):
    theme_label: NonEmpty
    count: int = Field(ge=0)
    share: float = Field(ge=0, le=1)
    sentiment_mix: dict[str, float] = Field(default_factory=dict)
    by_platform: dict[str, int] = Field(default_factory=dict)
    by_language: dict[str, int] = Field(default_factory=dict)
    recent_growth: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class MetricResult(StrictModel):
    """A stored output of `compute_metrics`, citable from a finding by `metric_id`."""

    metric_id: NonEmpty
    metric: NonEmpty
    values: dict[str, Any]
    params: dict[str, Any] = Field(default_factory=dict)
    batch_ids: list[str] = Field(default_factory=list)
