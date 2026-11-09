"""Search-interest time series."""

from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from edrak.agents.customer_trends.schemas.common import CountryCode, NonEmpty, StrictModel

NORMALIZED_MAX = 100.0


class TrendSeries(StrictModel):
    keyword: NonEmpty
    geo: CountryCode | None = None
    timeframe: NonEmpty
    granularity: Literal["day", "week", "month"]
    points: list[tuple[date, float]] = Field(default_factory=list)
    normalized: bool = True
    related_queries: list[str] = Field(default_factory=list)
    source: NonEmpty
    batch_id: NonEmpty

    @model_validator(mode="after")
    def _check_normalized_range(self) -> Self:
        if self.normalized and any(not 0 <= value <= NORMALIZED_MAX for _, value in self.points):
            raise ValueError("normalized series values must be between 0 and 100")
        return self
