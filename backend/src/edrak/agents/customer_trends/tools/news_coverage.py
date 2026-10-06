"""news_coverage: news articles about a topic, with a volume-over-time summary."""

import time
from collections import Counter
from typing import Any, Literal

from pydantic import Field

from edrak.agents.customer_trends.providers.base import ProviderResult
from edrak.agents.customer_trends.schemas.common import RequestBase, ToolResponse
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.tools.base import (
    DEFAULT_RESULTS,
    StoredBatch,
    ToolContext,
    ToolSpec,
    execute_collection,
    persist_evidence,
    results_limit,
)

DESCRIPTION = """Find news articles about a topic and how coverage is spread over time.

Use it to see whether a company, product or issue is in the news, what the headlines say, and
when coverage peaked. source "gdelt" covers about the last three months across many countries
and languages (headlines only); "google_news" gives recent Google News results. Do NOT use it for
customer opinions (use social_search) or full articles (use web_search, then fetch_page).

The response holds a batch_id, how many new articles were stored, a preview of up to 5, coverage
by language including a volume summary (days covered, total, peak day), gaps and warnings. The
day-by-day counts are stored with the batch.

Example: {"query": "GitLab AI", "source": "gdelt", "languages": ["en"]}"""


class NewsCoverageInput(RequestBase):
    query: str = Field(
        min_length=2, description="Topic, company or phrase to look for in the news."
    )
    source: Literal["gdelt", "google_news"] = Field(
        default="gdelt", description="gdelt (3 months, headlines) or google_news (recent)."
    )


def volume_by_day(result: ProviderResult) -> dict[str, int]:
    """Articles per publication day: the provider's own summary, or counted from the items."""
    reported = result.meta.get("volume_by_day")
    if isinstance(reported, dict) and reported:
        return {str(day): int(count) for day, count in reported.items()}
    days = Counter(
        item.published_at.strftime("%Y-%m-%d")
        for item in result.items
        if isinstance(item, EvidenceItem) and item.published_at
    )
    return dict(sorted(days.items()))


def volume_summary(by_day: dict[str, int]) -> dict[str, Any]:
    if not by_day:
        return {}
    peak_day = max(by_day, key=lambda day: by_day[day])
    return {
        "days": len(by_day),
        "total": sum(by_day.values()),
        "peak_day": peak_day,
        "peak_count": by_day[peak_day],
    }


def persist_news(ctx: ToolContext, tool: str, result: ProviderResult) -> StoredBatch:
    by_day = volume_by_day(result)
    result = result.model_copy(update={"meta": {**result.meta, "volume_by_day": by_day}})
    stored = persist_evidence(ctx, tool, result)
    summary = volume_summary(by_day)
    if stored.batch_id is None or not summary:
        return stored
    return StoredBatch(
        stored.batch_id,
        stored.inserted,
        stored.duplicates,
        stored.preview,
        {**stored.coverage, "volume": summary},
    )


async def news_coverage(ctx: ToolContext, inp: NewsCoverageInput) -> ToolResponse:
    started = time.perf_counter()
    params = {
        "query": inp.query,
        "languages": inp.languages,
        "geo": inp.geo,
        "since": inp.since,
        "until": inp.until,
        "max_results": results_limit(inp.depth, inp.max_results, DEFAULT_RESULTS["news"]),
    }
    return await execute_collection(
        ctx, "news_coverage", f"news:{inp.source}", inp, params,
        languages=inp.languages, persist=persist_news, started=started,
    )  # fmt: skip


SPEC = ToolSpec("news_coverage", DESCRIPTION, NewsCoverageInput, news_coverage)
