from pathlib import Path

import httpx
import pytest
import respx

from edrak.agents.customer_trends.providers.base import ProviderExhausted
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.schemas.common import Budget, Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.agents.customer_trends.settings import Settings
from tests.customer_trends.factories import NOW, call_params, fixture_json

APIFY_RUN = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


def fully_configured(tmp_path: Path, breaker: CircuitBreaker | None = None) -> ProviderRegistry:
    settings = Settings(
        _env_file=None,
        edrak_data_dir=tmp_path / "data",
        apify_token="apify-token",
        socialcrawl_api_key="sc-key",
        serper_api_key="serper-key",
        youtube_api_key="yt-key",
    )  # type: ignore[arg-type]  # secrets are given as plain strings in tests
    return ProviderRegistry.from_config(
        settings, BudgetTracker(Budget()), breaker or CircuitBreaker(), clock=lambda: NOW
    )


def serving_providers(registry: ProviderRegistry, capability: str) -> list[str]:
    return [
        name
        for name in registry.routing_for(capability)
        if (provider := registry.providers.get(name)) and capability in provider.capabilities
    ]


async def test_every_capability_has_a_registered_provider_and_the_table_is_printed(
    tmp_path: Path,
) -> None:
    registry = fully_configured(tmp_path)
    capabilities = sorted(registry._config.routing)
    rows = []
    try:
        for capability in capabilities:
            configured = registry.routing_for(capability)
            serving = serving_providers(registry, capability)
            rows.append(
                f"{capability:<24} {' > '.join(configured):<44} "
                f"serves: {' > '.join(serving) or '-'}"
            )
            assert serving, f"no registered provider can serve {capability}"
    finally:
        print(
            "\n"
            + "\n".join(
                ["capability" + " " * 14 + "routing order" + " " * 31 + "registered", *rows]
            )
        )
        await registry.aclose()


async def test_the_effective_order_per_capability(tmp_path: Path) -> None:
    registry = fully_configured(tmp_path)
    try:
        for capability in ("tiktok", "instagram", "reddit"):
            assert serving_providers(registry, f"social_search:{capability}") == [
                "socialcrawl",
                "apify",
                "serper",
            ]
        assert serving_providers(registry, "social_search:x") == ["apify", "socialcrawl", "serper"]
        assert serving_providers(registry, "social_search:facebook") == ["socialcrawl", "serper"]
        assert serving_providers(registry, "social_search:youtube") == [
            "youtube_api",
            "socialcrawl",
            "apify",
        ]
        for platform in ("x", "tiktok", "instagram", "facebook", "reddit"):
            assert serving_providers(registry, f"social_comments:{platform}") == [
                "socialcrawl",
                "apify",
            ]
        assert serving_providers(registry, "social_comments:youtube") == [
            "youtube_api",
            "socialcrawl",
            "apify",
        ]
        assert serving_providers(registry, "search_interest") == [
            "apify",
            "socialcrawl",
            "google_trends_api",
        ]
        assert serving_providers(registry, "reviews:amazon") == ["apify"]
        assert serving_providers(registry, "news:gdelt") == ["gdelt"]
        assert serving_providers(registry, "news:google_news") == ["serper"]
        assert serving_providers(registry, "web_search") == ["serper"]
        assert serving_providers(registry, "fetch_page") == ["direct_http"]
    finally:
        await registry.aclose()


async def test_without_aggregator_keys_social_search_still_has_the_serper_last_resort(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, edrak_data_dir=tmp_path / "data", serper_api_key="k")  # type: ignore[arg-type]  # plain secret
    registry = ProviderRegistry.from_config(settings, BudgetTracker(Budget()), CircuitBreaker())
    try:
        for platform in ("x", "reddit", "tiktok", "instagram", "facebook"):
            assert serving_providers(registry, f"social_search:{platform}") == ["serper"]
        assert serving_providers(registry, "social_comments:x") == []
    finally:
        await registry.aclose()


async def test_social_search_falls_back_from_apify_to_socialcrawl_to_serper_snippets(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    apify = respx_mock.post(APIFY_RUN.format(actor="apidojo~tweet-scraper")).mock(
        return_value=httpx.Response(
            402, json={"error": {"type": "not-enough-usage-to-run-paid-actor"}}
        )
    )
    socialcrawl = respx_mock.get("https://www.socialcrawl.dev/v1/twitter/search/tweets").mock(
        return_value=httpx.Response(
            503, json={"success": False, "error": {"type": "SERVICE_UNAVAILABLE"}}
        )
    )
    serper = respx_mock.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Duo review",
                        "link": "https://x.com/dev/status/1",
                        "snippet": "Looks good.",
                    }
                ]
            },
        )
    )
    registry = fully_configured(tmp_path)
    try:
        result = await registry.call(
            "social_search:x", call_params(query="gitlab duo", max_results=3)
        )
    finally:
        await registry.aclose()
    assert (apify.call_count, socialcrawl.call_count, serper.call_count) == (1, 3, 1)
    assert result.provider == "serper"
    assert result.fallback_used is True
    [item] = [i for i in result.items if isinstance(i, EvidenceItem)]
    assert item.snippet_only is True
    assert (item.platform, item.source_type) == (Platform.X, SourceType.SOCIAL_POST)
    assert any("snippet-level" in warning for warning in result.warnings)


async def test_a_successful_primary_is_not_a_fallback(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(APIFY_RUN.format(actor="apidojo~tweet-scraper")).mock(
        return_value=httpx.Response(
            201,
            json=[
                {
                    "type": "tweet",
                    "id": "1",
                    "url": "https://x.com/a/status/1",
                    "fullText": "GitLab Duo works well for us",
                }
            ],
        )
    )
    registry = fully_configured(tmp_path)
    try:
        result = await registry.call("social_search:x", call_params(query="duo", max_results=3))
    finally:
        await registry.aclose()
    assert (result.provider, result.fallback_used) == ("apify", False)
    assert result.cost_estimate == pytest.approx(0.0004)


async def test_search_interest_exhausts_when_every_provider_fails_or_is_unavailable(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(APIFY_RUN.format(actor="apify~google-trends-scraper")).mock(
        return_value=httpx.Response(
            400, json={"error": {"type": "run-failed", "message": "status: TIMED-OUT"}}
        )
    )
    respx_mock.get("https://www.socialcrawl.dev/v1/credits/balance").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"balance": 100}})
    )
    respx_mock.get("https://www.socialcrawl.dev/v1/google_trends/explore").mock(
        return_value=httpx.Response(
            503, json={"success": False, "error": {"type": "SERVICE_UNAVAILABLE"}}
        )
    )
    registry = fully_configured(tmp_path)
    try:
        with pytest.raises(ProviderExhausted) as caught:
            await registry.call("search_interest", call_params(keywords=["gitlab duo"]))
    finally:
        await registry.aclose()
    assert [(f.provider, f.error) for f in caught.value.failures] == [
        ("apify", "RunNotFinished"),
        ("socialcrawl", "ProviderUnavailable"),
        ("google_trends_api", "ProviderNotConfigured"),
    ]


async def test_search_interest_falls_back_to_socialcrawl_when_the_apify_run_times_out(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(APIFY_RUN.format(actor="apify~google-trends-scraper")).mock(
        return_value=httpx.Response(408)
    )
    respx_mock.get("https://www.socialcrawl.dev/v1/credits/balance").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"balance": 100}})
    )
    explore = respx_mock.get("https://www.socialcrawl.dev/v1/google_trends/explore").mock(
        return_value=httpx.Response(
            200, json=fixture_json("providers", "socialcrawl", "search_interest.json")
        )
    )
    registry = fully_configured(tmp_path)
    try:
        result = await registry.call(
            "search_interest",
            call_params(keywords=["gitlab duo", "github copilot"], timeframe="today 3-m", geo="US"),
        )
    finally:
        await registry.aclose()
    assert (result.provider, result.fallback_used) == ("socialcrawl", True)
    assert dict(explore.calls.last.request.url.params) == {
        "keywords": "gitlab duo,github copilot",
        "timeframe": "past_90_days",
        "location": "US",
    }
    assert [s.keyword for s in result.items if isinstance(s, TrendSeries)] == [
        "gitlab duo",
        "github copilot",
    ]


async def test_reddit_search_goes_to_apify_when_socialcrawl_is_out_of_credit(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    sc = respx_mock.get("https://www.socialcrawl.dev/v1/reddit/search").mock(
        return_value=httpx.Response(
            402, json={"success": False, "error": {"type": "INSUFFICIENT_CREDITS", "status": 402}}
        )
    )
    apify = respx_mock.post(APIFY_RUN.format(actor="trudax~reddit-scraper-lite")).mock(
        return_value=httpx.Response(201, json=fixture_json("providers", "apify", "reddit.json"))
    )
    registry = fully_configured(tmp_path)
    try:
        result = await registry.call(
            "social_search:reddit", call_params(query="gitlab duo", max_results=3)
        )
    finally:
        await registry.aclose()
    assert (sc.call_count, apify.call_count) == (1, 1)
    assert (result.provider, result.fallback_used) == ("apify", True)


async def test_facebook_search_skips_the_disabled_apify_actor(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://www.socialcrawl.dev/v1/facebook/search/posts").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [
                        {
                            "post": {
                                "id": "f1",
                                "url": "https://www.facebook.com/p/posts/1",
                                "content": {"text": "Duo launch post"},
                                "engagement": {"likes": 4},
                            },
                            "computed": {"language": "en"},
                        }
                    ]
                },
                "pagination": {"next_cursor": None, "has_more": False},
                "credits_used": 1,
                "credits_remaining": 50,
            },
        )
    )
    registry = fully_configured(tmp_path)
    try:
        result = await registry.call(
            "social_search:facebook", call_params(query="duo", max_results=3)
        )
    finally:
        await registry.aclose()
    assert (result.provider, result.fallback_used) == ("socialcrawl", False)
    assert [i.text for i in result.items if isinstance(i, EvidenceItem)] == ["Duo launch post"]
