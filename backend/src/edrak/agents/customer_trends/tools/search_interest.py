"""search_interest: Google search interest over time for up to five keywords."""

import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from functools import partial
from typing import Any, Literal

from pydantic import Field

from edrak.agents.customer_trends.providers.base import CallParams, ProviderResult, new_evidence
from edrak.agents.customer_trends.schemas.common import (
    NonEmpty,
    RequestBase,
    SourceType,
    ToolResponse,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.agents.customer_trends.tools.base import (
    StoredBatch,
    ToolContext,
    ToolSpec,
    execute_collection,
)

TREND_TOLERANCE = 0.05
MAX_KEYWORDS = 5
PREVIEW_RELATED = 3
TIMEFRAME_PATTERN = r"^(now \d+-[Hd]|today \d+-[my]|all|\d{4}-\d{2}-\d{2} \d{4}-\d{2}-\d{2})$"

DESCRIPTION = """Measure how much people search Google for up to 5 keywords over time.

Use it to judge whether demand for a product, feature or problem is rising, flat or falling, to
compare a brand with its competitors, and to find related queries. Values are relative (0 to 100,
100 is the peak of the period), not search counts. Pick the timeframe for the question: "today
12-m" for a year, "today 5-y" for the long trend, "today 3-m" for recent movement. Do NOT use it
for what people say (use social_search) or news (use news_coverage).

For each keyword the response preview gives the first and last value, the peak and its date, the
direction (up, flat or down), and the top related queries. The full series is stored. A
trend_point item per keyword is stored as evidence so findings can cite it.

Example: {"keywords": ["gitlab duo", "github copilot"], "timeframe": "today 12-m", "geo": "US"}"""


class SearchInterestInput(RequestBase):
    keywords: list[NonEmpty] = Field(
        min_length=1, max_length=MAX_KEYWORDS, description="1 to 5 search terms to compare."
    )
    timeframe: str = Field(
        default="today 12-m",
        pattern=TIMEFRAME_PATTERN,
        description="Google Trends period, for example 'today 12-m', 'today 3-m', 'today 5-y'.",
    )
    granularity: Literal["day", "week", "month"] = Field(
        default="week", description="A hint: Google picks the resolution from the timeframe."
    )
    include_related: bool = Field(default=True, description="Also keep related queries.")


def trend_direction(
    points: Sequence[tuple[date, float]], tolerance: float = TREND_TOLERANCE
) -> Literal["up", "flat", "down"]:
    """Direction of the least-squares line through the points.

    The change along the whole line must exceed `tolerance` times the series peak to count as
    movement; smaller changes are flat.
    """
    values = [value for _, value in points]
    if len(values) < 2 or max(values) <= 0:
        return "flat"
    mean_x = (len(values) - 1) / 2
    mean_y = sum(values) / len(values)
    spread = sum((i - mean_x) ** 2 for i in range(len(values)))
    slope = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / spread
    change = slope * (len(values) - 1)
    if abs(change) <= tolerance * max(values):
        return "flat"
    return "up" if change > 0 else "down"


def summarize_series(series: TrendSeries) -> dict[str, Any]:
    values = [value for _, value in series.points]
    peak_index = max(range(len(values)), key=lambda i: values[i])
    return {
        "keyword": series.keyword,
        "first": values[0],
        "last": values[-1],
        "peak_value": values[peak_index],
        "peak_date": series.points[peak_index][0].isoformat(),
        "direction": trend_direction(series.points),
        "points": len(values),
        "granularity": series.granularity,
        "related": series.related_queries[:PREVIEW_RELATED],
    }


def _sentence(series: TrendSeries, summary: dict[str, Any]) -> str:
    where = f" in {series.geo}" if series.geo else ""
    related = f" Related queries: {', '.join(summary['related'])}." if summary["related"] else ""
    return (
        f"Search interest for '{series.keyword}'{where} over {series.timeframe} is "
        f"{summary['direction']}: from {summary['first']:g} to {summary['last']:g} "
        f"(relative scale), peak {summary['peak_value']:g} on {summary['peak_date']}.{related}"
    )


def persist_trends(
    ctx: ToolContext, tool: str, result: ProviderResult, *, include_related: bool
) -> StoredBatch:
    series_list = [s for s in result.items if isinstance(s, TrendSeries) and s.points]
    if not series_list:
        return StoredBatch(None, 0, 0, [], {})
    params = CallParams(run_id=ctx.run_id, task_id=ctx.task_id)
    entries: list[tuple[TrendSeries, EvidenceItem, dict[str, Any]]] = []
    gaps: list[str] = []
    for series in series_list:
        if not include_related:
            series = series.model_copy(update={"related_queries": []})
        summary = summarize_series(series)
        item = new_evidence(
            params,
            provider=result.provider or series.source,
            source_type=SourceType.TREND_POINT,
            text=_sentence(series, summary),
            key=f"trend|{series.keyword}|{series.geo or '-'}|{series.timeframe}",
            metadata={k: v for k, v in summary.items() if k != "related"},
            collected_at=datetime.now(UTC),
        )
        if summary["peak_value"] == 0:
            gaps.append(f"no search interest data for '{series.keyword}' (all values are zero)")
        entries.append((series, item, summary))
    existing = ctx.store.existing_ids(ctx.run_id, [item.id for _, item, _ in entries])
    fresh = [entry for entry in entries if entry[1].id not in existing]
    if not fresh:
        return StoredBatch(None, 0, len(entries), [], {}, extra_gaps=gaps)
    batch_id, inserted, duplicates = ctx.store.add_batch(
        ctx.run_id,
        ctx.task_id,
        tool,
        [item for _, item, _ in fresh],
        {"provider": result.provider, "timeframe": fresh[0][0].timeframe},
    )
    ctx.store.save_trend_series(
        ctx.run_id, [series.model_copy(update={"batch_id": batch_id}) for series, _, _ in fresh]
    )
    preview = [
        {"id": item.id, "platform": "trend", "snippet": item.text, "url": None, **summary}
        for _, item, summary in fresh
    ]
    coverage = {
        "keywords": [series.keyword for series, _, _ in fresh],
        "timeframe": fresh[0][0].timeframe,
        "points": {series.keyword: len(series.points) for series, _, _ in fresh},
    }
    return StoredBatch(
        batch_id, inserted, duplicates + len(existing), preview, coverage, extra_gaps=gaps
    )


async def search_interest(ctx: ToolContext, inp: SearchInterestInput) -> ToolResponse:
    started = time.perf_counter()
    params = {
        "keywords": inp.keywords,
        "timeframe": inp.timeframe,
        "granularity": inp.granularity,
        "geo": inp.geo,
        "max_results": len(inp.keywords),
    }
    return await execute_collection(
        ctx, "search_interest", "search_interest", inp, params,
        persist=partial(persist_trends, include_related=inp.include_related), started=started,
    )  # fmt: skip


SPEC = ToolSpec("search_interest", DESCRIPTION, SearchInterestInput, search_interest)
