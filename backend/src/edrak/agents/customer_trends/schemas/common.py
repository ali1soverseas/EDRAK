"""Enums, shared field types, budgets, the request base and the tool response envelope."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from edrak.agents.customer_trends.utils.text import truncate


class UseCase(StrEnum):
    COMPETITIVE_INTELLIGENCE = "competitive_intelligence"
    MARKET_ENTRY = "market_entry"
    PRODUCT_LAUNCH = "product_launch"


class Depth(StrEnum):
    LIGHT = "light"
    STANDARD = "standard"
    DEEP = "deep"


class Platform(StrEnum):
    X = "x"
    REDDIT = "reddit"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    YOUTUBE = "youtube"


class SourceType(StrEnum):
    WEB = "web"
    NEWS = "news"
    SOCIAL_POST = "social_post"
    SOCIAL_COMMENT = "social_comment"
    REVIEW = "review"
    TREND_POINT = "trend_point"


class ToolStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingType(StrEnum):
    PAIN_POINT = "pain_point"
    UNMET_NEED = "unmet_need"
    DEMAND_SIGNAL = "demand_signal"
    SENTIMENT = "sentiment"
    COMPETITOR_GAP = "competitor_gap"
    TREND = "trend"
    RISK = "risk"


class ReviewStore(StrEnum):
    APP_STORE = "app_store"
    GOOGLE_PLAY = "google_play"
    AMAZON = "amazon"


Focus = Literal["pain_points", "demand", "sentiment", "competitor_gaps"]

DEPTH_CAPS: dict[Depth, int] = {Depth.LIGHT: 50, Depth.STANDARD: 200, Depth.DEEP: 1000}

PREVIEW_MAX_ITEMS = 5
PREVIEW_SNIPPET_MAX_CHARS = 200


def effective_max_results(depth: Depth, requested: int | None) -> int:
    """Results per platform per call: the request clamped to the cap for the depth."""
    cap = DEPTH_CAPS[depth]
    if requested is None:
        return cap
    if requested < 1:
        raise ValueError("max_results must be at least 1")
    return min(requested, cap)


def _to_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def _upper(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]
LanguageCode = Annotated[str, BeforeValidator(_lower), StringConstraints(pattern=r"^[a-z]{2}$")]
CountryCode = Annotated[str, BeforeValidator(_upper), StringConstraints(pattern=r"^[A-Z]{2}$")]
RunId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


class StrictModel(BaseModel):
    """Base for every worker model: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


class Budget(StrictModel):
    max_tool_calls: int = Field(default=60, ge=1)
    max_cost_usd: float = Field(default=2.0, ge=0)
    max_seconds: float = Field(default=300, gt=0)


class BudgetSnapshot(StrictModel):
    tool_calls: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    seconds: float = Field(default=0.0, ge=0)
    limits: Budget = Field(default_factory=Budget)


class ScopeBase(StrictModel):
    """Fields shared by the task brief and every collection tool input."""

    languages: list[LanguageCode] = Field(
        default_factory=lambda: ["ar", "en"],
        min_length=1,
        description="Language codes (ISO 639-1) to cover, for example ['ar', 'en'].",
    )
    geo: CountryCode | None = Field(
        default=None,
        description="Country (ISO 3166-1 alpha-2) the question is about, for example EG.",
    )
    since: date | None = Field(
        default=None, description="Only items published on or after this date (YYYY-MM-DD)."
    )
    until: date | None = Field(
        default=None, description="Only items published on or before this date (YYYY-MM-DD)."
    )
    depth: Depth = Field(
        default=Depth.STANDARD,
        description="light, standard or deep: sets the most results one call may return.",
    )

    @model_validator(mode="after")
    def _check_date_order(self) -> Self:
        if self.since and self.until and self.since > self.until:
            raise ValueError("since must not be after until")
        return self


class RequestBase(ScopeBase):
    run_id: RunId
    task_id: NonEmpty
    max_results: int | None = Field(
        default=None,
        ge=1,
        description="How many results to ask for; the cap for the depth applies.",
    )
    entity: str | None = Field(
        default=None, description="Company, product or category under study, if it matters."
    )

    @model_validator(mode="after")
    def _clamp_max_results(self) -> Self:
        if self.max_results is not None:
            self.max_results = effective_max_results(self.depth, self.max_results)
        return self


class RunScope(StrictModel):
    """The run and task a tool works on. The tool layer injects both, the model never sets them."""

    run_id: RunId
    task_id: NonEmpty


class ProcessingResponse(StrictModel):
    """Envelope of the processing tools: `count` is what the tool worked on or produced."""

    status: ToolStatus = ToolStatus.OK
    count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class ToolResponse(StrictModel):
    status: ToolStatus
    batch_id: str | None = None
    count: int = Field(default=0, ge=0)
    preview: list[dict[str, Any]] = Field(default_factory=list)
    provider_used: str | None = None
    fallback_used: bool = False
    coverage: dict[str, Any] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cost_estimate: float = Field(default=0.0, ge=0)
    next_cursor: str | None = None
    error_code: str | None = None

    @field_validator("preview")
    @classmethod
    def _limit_preview(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        limited = []
        for item in items[:PREVIEW_MAX_ITEMS]:
            snippet = item.get("snippet")
            if isinstance(snippet, str):
                item = {**item, "snippet": truncate(snippet, PREVIEW_SNIPPET_MAX_CHARS)}
            limited.append(item)
        return limited
