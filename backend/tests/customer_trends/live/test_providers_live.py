"""One real call to each data provider, with tiny limits."""

from collections.abc import AsyncIterator

import pytest

from edrak.agents.customer_trends.providers.base import ProviderExhausted, ProviderResult
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.schemas.common import Budget
from tests.customer_trends.factories import call_params
from tests.customer_trends.live.conftest import live_settings

pytestmark = pytest.mark.live
SETTINGS = live_settings()


@pytest.fixture
async def registry() -> AsyncIterator[ProviderRegistry]:
    live = ProviderRegistry.from_config(
        live_settings(),
        BudgetTracker(Budget(max_tool_calls=3, max_cost_usd=0.05, max_seconds=120)),
        CircuitBreaker(),
    )
    yield live
    await live.aclose()


async def call(registry: ProviderRegistry, capability: str, **params: object) -> ProviderResult:
    try:
        return await registry.call(capability, call_params(**params))
    except ProviderExhausted as exc:
        pytest.skip(f"{capability} is unavailable right now: {exc}")


@pytest.mark.skipif(not SETTINGS.has_key("serper_api_key"), reason="SERPER_API_KEY is not set")
async def test_serper_web_search(registry: ProviderRegistry) -> None:
    result = await call(registry, "web_search", query="GitLab Duo", max_results=3)
    assert result.provider == "serper" and 0 < len(result.items) <= 3


async def test_gdelt_news(registry: ProviderRegistry) -> None:
    result = await call(registry, "news:gdelt", query="GitLab", max_results=3)
    assert result.provider == "gdelt" and len(result.items) <= 3


@pytest.mark.skipif(not SETTINGS.has_key("youtube_api_key"), reason="YOUTUBE_API_KEY is not set")
async def test_youtube_search(registry: ProviderRegistry) -> None:
    result = await call(registry, "social_search:youtube", query="GitLab Duo", max_results=3)
    assert result.provider == "youtube_api" and 0 < len(result.items) <= 3


@pytest.mark.skipif(not SETTINGS.has_key("apify_token"), reason="APIFY_TOKEN is not set")
async def test_apify_one_platform(registry: ProviderRegistry) -> None:
    result = await call(registry, "social_search:x", query="gitlab duo", max_results=3)
    assert result.provider == "apify" and 0 < len(result.items) <= 3
    assert result.cost_estimate < 0.05


@pytest.mark.skipif(
    not SETTINGS.has_key("socialcrawl_api_key"), reason="SOCIALCRAWL_API_KEY is not set"
)
async def test_socialcrawl_credits(registry: ProviderRegistry) -> None:
    provider = registry.providers["socialcrawl"]
    credits = await provider.credits_remaining()  # type: ignore[attr-defined]  # SocialCrawlProvider
    assert credits >= 0
