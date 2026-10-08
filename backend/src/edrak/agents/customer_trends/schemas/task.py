"""The task brief (internal view of the task) and the query plan the planner produces."""

import hashlib
import re
from datetime import UTC, date, datetime, timedelta

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
from edrak.contracts import ResearchTask
from edrak.contracts import UseCase as SharedUseCase

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


class PlanDraft(QueryPlan):
    """What the planner model returns: the same shape as `QueryPlan`, but a sixth trend keyword
    is trimmed instead of failing the whole answer."""

    trend_keywords: list[NonEmpty] = Field(default_factory=list)

    def to_plan(self) -> QueryPlan:
        data = self.model_dump()
        data["trend_keywords"] = self.trend_keywords[:MAX_TREND_KEYWORDS]
        return QueryPlan.model_validate(data)


# ResearchTask to TaskBrief (SPEC 6.0). The shared task carries a goal, a focus text, the company
# and a business context; the rest of the brief is derived from that text or defaulted.

_RUN_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_RUN_ID_MAX = 128
_USE_CASES = {
    SharedUseCase.COMPETITIVE_INTELLIGENCE: UseCase.COMPETITIVE_INTELLIGENCE,
    SharedUseCase.MARKET_ENTRY_EXPANSION: UseCase.MARKET_ENTRY,
    SharedUseCase.PRODUCT_LAUNCH: UseCase.PRODUCT_LAUNCH,
}
_FOCUS_WORDS: dict[Focus, re.Pattern[str]] = {
    "pain_points": re.compile(r"pain|problem|complain|frustrat|issue", re.IGNORECASE),
    "demand": re.compile(r"demand|trend|interest|growth|growing|rising", re.IGNORECASE),
    "sentiment": re.compile(r"sentiment|opinion|perception|satisf|feel", re.IGNORECASE),
    "competitor_gaps": re.compile(r"competitor|gap|compar|weakness|alternative", re.IGNORECASE),
}
_COUNTRIES = {
    "egypt": "EG",
    "saudi arabia": "SA",
    "saudi": "SA",
    "united arab emirates": "AE",
    "uae": "AE",
    "jordan": "JO",
    "morocco": "MA",
    "tunisia": "TN",
    "algeria": "DZ",
    "lebanon": "LB",
    "iraq": "IQ",
    "kuwait": "KW",
    "qatar": "QA",
    "bahrain": "BH",
    "oman": "OM",
    "libya": "LY",
    "sudan": "SD",
    "palestine": "PS",
    "united states": "US",
    "united kingdom": "GB",
}
_COUNTRY_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, _COUNTRIES), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def run_id_for(task: ResearchTask) -> str:
    """A valid, stable run id for one attempt of a task: a retry is a new run."""
    base = _RUN_ID_UNSAFE.sub("-", task.task_id).strip("-._") or "task"
    run_id = f"{base}.a{task.attempt}"
    if len(run_id) > _RUN_ID_MAX:
        digest = hashlib.sha1(run_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
        run_id = f"{base[: _RUN_ID_MAX - 16]}.{digest}.a{task.attempt}"
    return run_id


def brief_from_task(task: ResearchTask, *, today: date | None = None) -> TaskBrief:
    """Turn the shared task into the worker's brief.

    The entity is the company, the question is the goal, the targets are the competitors (for a
    market entry they describe the market instead). The focus comes from the words of the task
    and is left empty, to take the use case defaults, when none match. The country is read from
    the task text, and `time_window_days` becomes `since`.
    """
    context = task.business_context
    use_case = _USE_CASES[context.use_case]
    text = " ".join(
        [task.goal, task.focus, *context.focus_areas, *context.targets, *context.constraints]
    )
    focus_text = " ".join([task.focus, *context.focus_areas])
    focus: list[Focus] = [
        name for name, pattern in _FOCUS_WORDS.items() if pattern.search(focus_text)
    ]
    country = _COUNTRY_PATTERN.search(text)
    day = today or datetime.now(UTC).date()
    market_entry = use_case is UseCase.MARKET_ENTRY
    notes = "; ".join([f"Focus: {task.focus}", *context.constraints])
    return TaskBrief(
        task_id=task.task_id,
        run_id=run_id_for(task),
        use_case=use_case,
        entity=task.company_profile.name,
        question=task.goal,
        market=", ".join(context.targets) if market_entry and context.targets else None,
        geo=_COUNTRIES[country.group(1).lower()] if country else None,
        competitors=[] if market_entry else list(context.targets),
        focus=focus,
        since=day - timedelta(days=context.time_window_days) if context.time_window_days else None,
        notes=notes,
    )
