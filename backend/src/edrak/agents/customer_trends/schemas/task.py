"""The task brief (internal view of the task) and the query plan the planner produces."""

from pydantic import Field

from edrak.agents.customer_trends.schemas.common import (
    Budget,
    CountryCode,
    Focus,
    NonEmpty,
    Platform,
    ReviewStore,
    RunId,
    ScopeBase,
    StrictModel,
    UseCase,
)

MAX_TREND_KEYWORDS = 5


class TaskBrief(ScopeBase):
    task_id: NonEmpty
    run_id: RunId
    use_case: UseCase
    entity: NonEmpty
    question: NonEmpty
    market: str | None = None
    competitors: list[NonEmpty] = Field(default_factory=list)
    focus: list[Focus] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    notes: str | None = None


class ReviewTarget(StrictModel):
    store: ReviewStore
    target: NonEmpty
    country: CountryCode


class QueryPlan(StrictModel):
    social_queries: dict[Platform, list[str]] = Field(default_factory=dict)
    hashtags: dict[Platform, list[str]] = Field(default_factory=dict)
    trend_keywords: list[NonEmpty] = Field(default_factory=list, max_length=MAX_TREND_KEYWORDS)
    news_queries: list[str] = Field(default_factory=list)
    review_targets: list[ReviewTarget] = Field(default_factory=list)
    competitor_angles: list[str] = Field(default_factory=list)
    rationale: str = ""
