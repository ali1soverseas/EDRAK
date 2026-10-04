from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
import respx

from edrak.agents.customer_trends.providers.base import ProviderBadResponse
from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.gdelt import GdeltProvider
from edrak.agents.customer_trends.providers.http import RateLimiter, make_client
from edrak.agents.customer_trends.schemas.common import SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from tests.customer_trends.factories import NOW, FakeClock, call_params

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
CONFIG = ProviderConfig(min_interval_s=5.0, max_per_request=250, options={"window_days": 90})

ARTICLES = {
    "articles": [
        {
            "url": "https://news.example.test/a",
            "title": "GitLab expands Duo for enterprises",
            "seendate": "20260918T080000Z",
            "domain": "news.example.test",
            "language": "English",
            "sourcecountry": "United States",
        },
        {
            "url": "https://news.example.test/b",
            "title": "GitHub Copilot pricing criticized",
            "seendate": "20260918T150000Z",
            "domain": "news.example.test",
            "language": "English",
            "sourcecountry": "United States",
        },
        {
            "url": "https://arabic.example.test/c",
            "title": "أتلاسيان تعلن عن ميزات ذكاء اصطناعي جديدة",
            "seendate": "20260920T100000Z",
            "domain": "arabic.example.test",
            "language": "Arabic",
            "sourcecountry": "Egypt",
        },
        {"url": "https://x.example.test/d", "title": "", "seendate": "20260920T100000Z"},
    ]
}


async def run(clock: FakeClock | None = None, **params: Any) -> Any:
    clock = clock or FakeClock()
    async with make_client(5) as client:
        provider = GdeltProvider(
            client,
            CONFIG,
            clock=lambda: NOW,
            limiter=RateLimiter(5.0, clock=clock, sleep=clock.sleep),
        )
        return await provider.call("news:gdelt", call_params(**params))


async def test_request_params(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(200, json={"articles": []}))
    await run(
        query="gitlab duo",
        max_results=1000,
        languages=["ar", "en"],
        since=date(2026, 9, 1),
        until=date(2026, 9, 25),
    )
    params = dict(route.calls.last.request.url.params)
    assert params == {
        "query": "gitlab duo (sourcelang:arabic OR sourcelang:english)",
        "mode": "artlist",
        "format": "json",
        "maxrecords": "250",
        "sort": "datedesc",
        "startdatetime": "20260901000000",
        "enddatetime": "20260925235959",
    }


async def test_single_language_and_default_window(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(200, json={"articles": []}))
    await run(query="gitlab", max_results=20, languages=["en"])
    params = dict(route.calls.last.request.url.params)
    assert params["query"] == "gitlab sourcelang:english"
    assert params["maxrecords"] == "20"
    assert params["startdatetime"] == "20260703090000"
    assert params["enddatetime"] == "20261001090000"


async def test_unmapped_languages_are_reported(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(200, json={"articles": []}))
    result = await run(query="gitlab", languages=["en", "xx"])
    assert dict(route.calls.last.request.url.params)["query"] == "gitlab sourcelang:english"
    assert result.warnings == ["no GDELT source language for 'xx': not filtered"]


async def test_windows_longer_than_coverage_are_clamped_with_a_warning(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(200, json={"articles": []}))
    result = await run(query="gitlab", since=date(2025, 1, 1))
    assert dict(route.calls.last.request.url.params)["startdatetime"] == "20260703090000"
    assert any("clamped to 2026-07-03" in w for w in result.warnings)


async def test_a_window_entirely_outside_coverage_makes_no_request(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(200, json={}))
    result = await run(query="gitlab", since=date(2025, 1, 1), until=date(2025, 2, 1))
    assert route.call_count == 0
    assert result.items == []
    assert any("outside GDELT's coverage" in w for w in result.warnings)


async def test_articles_map_to_news_evidence_with_volume_by_day(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).mock(return_value=httpx.Response(200, json=ARTICLES))
    result = await run(query="gitlab", max_results=10)
    first, second, arabic = result.items
    assert isinstance(first, EvidenceItem)
    assert (first.source_type, first.platform, first.provider) == (SourceType.NEWS, None, "gdelt")
    assert first.text == "GitLab expands Duo for enterprises"
    assert first.published_at == datetime(2026, 9, 18, 8, tzinfo=UTC)
    assert first.language == "en"
    assert first.author == "news.example.test"
    assert first.snippet_only is True
    assert first.metadata["sourcecountry"] == "United States"
    assert arabic.language == "ar"
    assert second.published_at == datetime(2026, 9, 18, 15, tzinfo=UTC)
    assert result.raw_count == 4
    assert len(result.items) == 3
    assert result.meta["volume_by_day"] == {"2026-09-18": 2, "2026-09-20": 1}
    assert result.meta["volume_basis"] == "returned_articles"
    assert result.cost_estimate == 0.0


async def test_empty_body_and_missing_articles_are_empty_results(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(URL).mock(return_value=httpx.Response(200, text=""))
    assert (await run(query="nothing")).items == []
    respx_mock.get(URL).mock(return_value=httpx.Response(200, json={}))
    assert (await run(query="nothing")).raw_count == 0


async def test_html_error_pages_are_bad_responses(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).mock(
        return_value=httpx.Response(200, text="Your query was too short or too long.")
    )
    with pytest.raises(ProviderBadResponse, match="too short"):
        await run(query="a")


async def test_requests_are_spaced_by_at_least_five_seconds(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).mock(return_value=httpx.Response(200, json={"articles": []}))
    clock = FakeClock()
    async with make_client(5) as client:
        provider = GdeltProvider(
            client,
            CONFIG,
            clock=lambda: NOW,
            limiter=RateLimiter(5.0, clock=clock, sleep=clock.sleep),
        )
        for _ in range(3):
            await provider.call("news:gdelt", call_params(query="gitlab"))
    assert clock.sleeps == [5.0, 5.0]


async def test_the_default_limiter_never_goes_below_five_seconds() -> None:
    async with make_client(5) as client:
        provider = GdeltProvider(client, ProviderConfig(min_interval_s=0.1))
        assert provider._limiter._interval == 5.0


async def test_unsupported_capability_is_rejected() -> None:
    async with make_client(5) as client:
        provider = GdeltProvider(client, CONFIG)
        with pytest.raises(ProviderBadResponse, match="unsupported"):
            await provider.call("news:google_news", call_params())
