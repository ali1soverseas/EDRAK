"""Builders for tool tests: fake providers, a tool context and canned provider results."""

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

from edrak.agents.customer_trends.providers.base import ProviderResult
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.schemas.common import Budget, Platform, SourceType
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools.base import ToolContext
from tests.customer_trends.factories import BASE_DAY, RUN_ID, TASK_ID, make_evidence

Behavior = Callable[[str, dict[str, Any]], Awaitable[ProviderResult]]


class FakeProvider:
    """Serves one capability, records every call and delegates to `behavior`."""

    def __init__(self, name: str, capability: str, behavior: Behavior) -> None:
        self.name = name
        self.capabilities = {capability}
        self.behavior = behavior
        self.calls: list[dict[str, Any]] = []

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        self.calls.append(params)
        return await self.behavior(capability, params)


def serves(result: ProviderResult) -> Behavior:
    async def behavior(capability: str, params: dict[str, Any]) -> ProviderResult:
        return result

    return behavior


def fails(error: BaseException) -> Behavior:
    async def behavior(capability: str, params: dict[str, Any]) -> ProviderResult:
        raise error

    return behavior


def evidence_result(
    capability: str,
    count: int = 3,
    *,
    language: str = "en",
    snippet_only: bool = False,
    tag: str = "a",
) -> ProviderResult:
    kind, _, variant = capability.partition(":")
    platform = Platform(variant) if kind.startswith("social") else None
    source_type = {
        "social_search": SourceType.SOCIAL_POST,
        "social_comments": SourceType.SOCIAL_COMMENT,
        "reviews": SourceType.REVIEW,
        "news": SourceType.NEWS,
    }.get(kind, SourceType.WEB)
    items = [
        make_evidence(
            f"{tag} item {i} about GitLab Duo and support quality",
            platform=platform,
            source_type=source_type,
            language=language,
            published_at=BASE_DAY + timedelta(days=i),
            engagement={"likes": 10 * i, "replies": i},
            snippet_only=snippet_only,
            provider="fake",
        )
        for i in range(count)
    ]
    return ProviderResult(items=items, raw_count=count, cost_estimate=0.002)


def trend_series(keyword: str = "gitlab duo", values: list[float] | None = None) -> TrendSeries:
    values = values if values is not None else [10, 20, 35, 50, 70, 90]
    start = date(2026, 1, 4)
    return TrendSeries(
        keyword=keyword,
        geo="US",
        timeframe="today 12-m",
        granularity="week",
        points=[(start + timedelta(weeks=i), float(v)) for i, v in enumerate(values)],
        related_queries=["gitlab duo pricing", "gitlab duo agent", "duo vs copilot", "extra one"],
        source="apify",
        batch_id="pending",
    )


def make_context(
    store: EvidenceStore,
    capability: str,
    behavior: Behavior,
    *,
    budget: Budget | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
    defaults: dict[str, Any] | None = None,
    position: int = 0,
) -> tuple[ToolContext, FakeProvider]:
    """A context whose registry routes `capability` to one fake provider.

    `position` picks which configured provider name the fake takes (0 is the first choice).
    """
    config = load_providers_config()
    name = config.routing[capability][position]
    provider = FakeProvider(name, capability, behavior)
    tracker = BudgetTracker(budget or Budget())
    breaker = CircuitBreaker()
    registry = ProviderRegistry(config, budget=tracker, breaker=breaker, providers=[provider])
    store.create_run(RUN_ID, TASK_ID)
    ctx = ToolContext(
        settings=Settings(_env_file=None),
        store=store,
        providers=registry,
        budget=tracker,
        breaker=breaker,
        run_id=RUN_ID,
        task_id=TASK_ID,
        emit=emit or (lambda event: None),
        defaults=defaults or {},
    )
    return ctx, provider
