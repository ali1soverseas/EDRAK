"""Write the demo fixtures: provider results the keyless demo runs read in fixture mode.

The items are synthetic (the demo phrases of `llm/demo_script.py`, not scraped posts), so the
fake model's labels match what the fixtures say. Run from `backend/`:

    uv run python ../scripts/customer_trends/build_demo_fixtures.py
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from edrak.agents.customer_trends.llm.demo_script import DEMO_THEMES
from edrak.agents.customer_trends.providers.base import ProviderResult
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.agents.customer_trends.utils.text import content_hash, evidence_id

OUT = Path(__file__).resolve().parents[2] / "backend/evals/customer_trends/fixtures/providers"
COLLECTED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
NEWS_HEADLINES = [
    "Sector report: customers weigh price against support quality",
    "Vendors announce new tiers as demand for the category grows",
    "Analysts expect more consolidation among tools in the category",
    "Survey: onboarding and documentation decide renewals",
    "Outage reports prompt calls for clearer status pages",
    "Freelancers ask for better tools to track irregular income",
    "Study links faster support replies to lower churn",
    "Free tiers shrink as vendors chase profitability",
    "Buyers compare export options before they commit to a tool",
    "Reliability becomes the first question in vendor reviews",
    "New entrants undercut incumbents on price in the category",
    "Customers say clear documentation is worth paying for",
]
WEB_RESULTS = [
    "Buyer's guide: what to ask a vendor about support response times",
    "How pricing changes affect small teams, with examples",
    "Checklist for judging a tool's onboarding in the first week",
    "Comparison table of export formats offered by popular tools",
    "Community thread: workarounds for tracking income that varies by month",
    "Incident review template for teams that depend on a hosted tool",
]


def item(
    key: str,
    text: str,
    *,
    source_type: SourceType,
    platform: Platform | None,
    language: str,
    published: datetime,
    provider: str,
    engagement: dict[str, int] | None = None,
    snippet_only: bool = False,
    url: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        id=evidence_id(f"{platform.value if platform else 'none'}|{key}"),
        run_id="fixture-run",
        task_id="fixture-task",
        batch_id="pending",
        source_type=source_type,
        platform=platform,
        url=url,
        text=text,
        language=language,
        published_at=published,
        collected_at=COLLECTED,
        engagement=engagement or {},
        snippet_only=snippet_only,
        provider=provider,
        content_hash=content_hash(text),
    )


def day(month: int, number: int) -> datetime:
    return datetime(2026, month, number, 12, 0, tzinfo=UTC)


def posts(
    platform: Platform, name: str, provider: str, count: int, arabic: int, start: datetime
) -> list[EvidenceItem]:
    items = []
    for n in range(count):
        theme = DEMO_THEMES[n % len(DEMO_THEMES)]
        is_arabic = n < arabic
        text = f"{theme.arabic if is_arabic else theme.english} ({name} {n + 1})"
        items.append(
            item(
                f"{name}-{n}",
                text,
                source_type=SourceType.SOCIAL_POST,
                platform=platform,
                language="ar" if is_arabic else "en",
                published=start + timedelta(days=n),
                provider=provider,
                engagement={"likes": (n * 13) % 90 + 5, "replies": n % 9, "shares": n % 4},
                url=f"https://{platform.value}.example.test/demo/{name}/{n + 1}",
            )
        )
    return items


def reviews(count: int) -> list[EvidenceItem]:
    return [
        item(
            f"review-{n}",
            f"{DEMO_THEMES[n % len(DEMO_THEMES)].english} (app review {n + 1})",
            source_type=SourceType.REVIEW,
            platform=None,
            language="en",
            published=day(8, 1) + timedelta(days=n),
            provider="apify",
            engagement={"rating": 1 + (n * 3) % 5},
        )
        for n in range(count)
    ]


def snippets(kind: SourceType, texts: list[str], provider: str) -> list[EvidenceItem]:
    return [
        item(
            f"{kind.value}-{n}",
            text,
            source_type=kind,
            platform=None,
            language="en",
            published=day(9, 1) + timedelta(days=n),
            provider=provider,
            snippet_only=True,
            url=f"https://news.example.test/{kind.value}/{n + 1}",
        )
        for n, text in enumerate(texts)
    ]


def series(keyword: str, values: list[float], related: list[str]) -> TrendSeries:
    start = date(2026, 4, 5)
    return TrendSeries(
        keyword=keyword,
        timeframe="today 12-m",
        granularity="week",
        points=[(start + timedelta(weeks=n), value) for n, value in enumerate(values)],
        related_queries=related,
        source="apify",
        batch_id="pending",
    )


def results() -> dict[str, ProviderResult]:
    def result(items: list[EvidenceItem], provider: str) -> ProviderResult:
        return ProviderResult(
            items=items,
            raw_count=len(items),
            provider=provider,
            meta={"synthetic": True},
        )

    rising = [10.0 + 3 * n for n in range(24)]
    steady = [40.0 + (n % 3) for n in range(24)]
    return {
        "social_search.reddit": result(
            posts(Platform.REDDIT, "reddit", "apify", 30, 0, day(8, 1)), "apify"
        ),
        "social_search.x": result(posts(Platform.X, "x", "apify", 25, 15, day(8, 5)), "apify"),
        "social_search.youtube": result(
            posts(Platform.YOUTUBE, "youtube", "youtube_api", 10, 5, day(9, 1)), "youtube_api"
        ),
        "reviews.app_store": result(reviews(25), "apify"),
        "news.gdelt": result(snippets(SourceType.NEWS, NEWS_HEADLINES, "gdelt"), "gdelt"),
        "web_search": result(snippets(SourceType.WEB, WEB_RESULTS, "serper"), "serper"),
        "search_interest": ProviderResult(
            items=[
                series("customer support", rising, ["support response time", "support chat"]),
                series("pricing", steady, ["pricing plans"]),
            ],
            raw_count=2,
            provider="apify",
            meta={"synthetic": True},
        ),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, result in results().items():
        (OUT / f"{name}.json").write_text(result.model_dump_json(indent=1) + "\n", encoding="utf-8")
        print(f"{name}: {len(result.items)} items")


if __name__ == "__main__":
    main()
