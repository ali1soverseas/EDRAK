from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderError,
    ProviderExhausted,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderResult,
    ProviderUnavailable,
)
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetExceeded, BudgetTracker
from edrak.agents.customer_trends.providers.cache import DiskCache
from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.providers.registry import ProviderRegistry, fixture_filename
from edrak.agents.customer_trends.schemas.common import Budget
from tests.customer_trends.factories import call_params, make_evidence

CAPABILITY = "social_search:x"
PARAMS = call_params(query="gitlab duo", max_results=5)

Behavior = Callable[[], Awaitable[ProviderResult]]


class FakeProvider:
    def __init__(self, name: str, behavior: Behavior, capabilities: set[str] | None = None) -> None:
        self.name = name
        self.capabilities = capabilities or {CAPABILITY}
        self._behavior = behavior
        self.calls = 0

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        self.calls += 1
        return await self._behavior()


def serves(text: str = "a post", cost: float = 0.0) -> Behavior:
    async def behavior() -> ProviderResult:
        return ProviderResult(items=[make_evidence(text)], raw_count=1, cost_estimate=cost)

    return behavior


def fails(error: ProviderError) -> Behavior:
    async def behavior() -> ProviderResult:
        raise error

    return behavior


def registry(
    *providers: FakeProvider,
    budget: BudgetTracker | None = None,
    breaker: CircuitBreaker | None = None,
    cache: DiskCache | None = None,
) -> ProviderRegistry:
    return ProviderRegistry(
        load_providers_config(),
        budget=budget or BudgetTracker(Budget()),
        breaker=breaker or CircuitBreaker(),
        cache=cache,
        providers=providers,
    )


async def test_first_provider_serves_without_fallback() -> None:
    apify = FakeProvider("apify", serves("from apify"))
    socialcrawl = FakeProvider("socialcrawl", serves("from socialcrawl"))
    result = await registry(apify, socialcrawl).call(CAPABILITY, PARAMS)
    assert result.provider == "apify"
    assert result.fallback_used is False
    assert (apify.calls, socialcrawl.calls) == (1, 0)


async def test_rate_limited_primary_falls_back_to_the_next_provider() -> None:
    apify = FakeProvider("apify", fails(ProviderRateLimited("slow down")))
    socialcrawl = FakeProvider("socialcrawl", serves("from socialcrawl"))
    result = await registry(apify, socialcrawl).call(CAPABILITY, PARAMS)
    assert result.provider == "socialcrawl"
    assert result.fallback_used is True
    assert result.items[0].text == "from socialcrawl"  # type: ignore[union-attr]  # evidence items
    assert (apify.calls, socialcrawl.calls) == (1, 1)


async def test_breaker_opens_after_three_failures_and_the_provider_is_skipped() -> None:
    breaker = CircuitBreaker()
    apify = FakeProvider("apify", fails(ProviderRateLimited("slow down")))
    socialcrawl = FakeProvider("socialcrawl", serves())
    routed = registry(apify, socialcrawl, breaker=breaker)
    for _ in range(3):
        result = await routed.call(CAPABILITY, PARAMS)
        assert result.provider == "socialcrawl" and result.fallback_used is True
    assert breaker.is_open("apify")
    assert apify.calls == 3
    await routed.call(CAPABILITY, PARAMS)
    assert apify.calls == 3
    assert socialcrawl.calls == 4


async def test_unregistered_providers_are_skipped_and_still_count_as_fallback() -> None:
    serper = FakeProvider("serper", serves("snippet"))
    result = await registry(serper).call(CAPABILITY, PARAMS)
    assert result.provider == "serper"
    assert result.fallback_used is True


async def test_providers_that_do_not_support_the_capability_are_skipped() -> None:
    apify = FakeProvider("apify", serves(), capabilities={"search_interest"})
    socialcrawl = FakeProvider("socialcrawl", serves("ok"))
    result = await registry(apify, socialcrawl).call(CAPABILITY, PARAMS)
    assert result.provider == "socialcrawl"
    assert apify.calls == 0


async def test_all_failing_raises_exhausted_with_every_failure() -> None:
    apify = FakeProvider("apify", fails(ProviderRateLimited("slow down")))
    socialcrawl = FakeProvider("socialcrawl", fails(ProviderUnavailable("down")))
    serper = FakeProvider("serper", fails(ProviderNotConfigured("bad key")))
    with pytest.raises(ProviderExhausted) as caught:
        await registry(apify, socialcrawl, serper).call(CAPABILITY, PARAMS)
    summary = [(f.provider, f.error, f.skipped) for f in caught.value.failures]
    assert summary == [
        ("apify", "ProviderRateLimited", False),
        ("socialcrawl", "ProviderUnavailable", False),
        ("serper", "ProviderNotConfigured", False),
    ]
    assert caught.value.capability == CAPABILITY


async def test_a_capability_with_no_registered_provider_is_exhausted() -> None:
    with pytest.raises(ProviderExhausted) as caught:
        await registry().call(CAPABILITY, PARAMS)
    assert [f.skipped for f in caught.value.failures] == [True, True, True]


async def test_unexpected_parsing_errors_become_fallback_not_crashes() -> None:
    async def broken() -> ProviderResult:
        return ProviderResult.model_validate({"items": "nope"})

    apify = FakeProvider("apify", broken)
    socialcrawl = FakeProvider("socialcrawl", serves())
    result = await registry(apify, socialcrawl).call(CAPABILITY, PARAMS)
    assert result.provider == "socialcrawl"

    async def key_error() -> ProviderResult:
        raise KeyError("missing field")

    with pytest.raises(ProviderExhausted) as caught:
        await registry(FakeProvider("apify", key_error)).call(CAPABILITY, PARAMS)
    assert caught.value.failures[0].error == ProviderBadResponse.__name__


async def test_invalid_capability_is_rejected() -> None:
    with pytest.raises(ValueError, match="capability"):
        await registry().call("social_search:myspace", PARAMS)


async def test_cost_and_tool_calls_are_recorded_on_the_budget() -> None:
    budget = BudgetTracker(Budget())
    routed = registry(FakeProvider("apify", serves(cost=0.02)), budget=budget)
    await routed.call(CAPABILITY, PARAMS)
    await routed.call(CAPABILITY, PARAMS)
    snapshot = budget.snapshot()
    assert snapshot.tool_calls == 2
    assert snapshot.cost_usd == pytest.approx(0.04)


async def test_a_failed_call_still_counts_as_a_tool_call() -> None:
    budget = BudgetTracker(Budget())
    with pytest.raises(ProviderExhausted):
        await registry(budget=budget).call(CAPABILITY, PARAMS)
    assert budget.snapshot().tool_calls == 1


async def test_budget_exceeded_stops_the_call_before_any_provider() -> None:
    budget = BudgetTracker(Budget(max_tool_calls=1))
    apify = FakeProvider("apify", serves())
    routed = registry(apify, budget=budget)
    await routed.call(CAPABILITY, PARAMS)
    with pytest.raises(BudgetExceeded):
        await routed.call(CAPABILITY, PARAMS)
    assert apify.calls == 1


async def test_cache_hit_skips_the_provider_and_restamps_the_run(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, 100)
    apify = FakeProvider("apify", serves("cached text"))
    routed = registry(apify, cache=cache)
    first = await routed.call(CAPABILITY, PARAMS)
    second = await routed.call(
        CAPABILITY,
        call_params(run_id="run-other", task_id="task-other", query="gitlab duo", max_results=5),
    )
    assert apify.calls == 1
    assert first.items[0].run_id == "run-test-001"  # type: ignore[union-attr]  # evidence items
    assert second.items[0].run_id == "run-other"  # type: ignore[union-attr]  # evidence items
    assert second.provider == "apify"


async def test_failures_are_never_cached(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, 100)
    outcomes = [fails(ProviderUnavailable("down")), serves("recovered")]

    async def behavior() -> ProviderResult:
        return await outcomes.pop(0)()

    apify = FakeProvider("apify", behavior)
    routed = registry(apify, cache=cache)
    with pytest.raises(ProviderExhausted):
        await routed.call(CAPABILITY, PARAMS)
    result = await routed.call(CAPABILITY, PARAMS)
    assert result.items[0].text == "recovered"  # type: ignore[union-attr]  # evidence items
    assert apify.calls == 2


def test_routing_follows_the_config() -> None:
    routed = registry()
    assert routed.routing_for("social_search:youtube") == ["youtube_api", "apify", "socialcrawl"]
    assert routed.routing_for("web_search") == ["serper"]
    assert routed.routing_for("social_search:x")[-1] == "serper"
    assert routed.routing_for("nothing") == []


def test_fixture_file_names() -> None:
    assert fixture_filename("social_search:x") == "social_search.x.json"
    assert fixture_filename("web_search") == "web_search.json"
