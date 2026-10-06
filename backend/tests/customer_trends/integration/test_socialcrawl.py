import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderUnavailable,
)
from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.providers.http import RateLimiter, make_client
from edrak.agents.customer_trends.providers.socialcrawl import SocialCrawlProvider
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from tests.customer_trends.factories import NOW, call_params, fixture_json

BASE = "https://www.socialcrawl.dev/v1"
CONFIG = load_providers_config().providers["socialcrawl"]
ARABIC = "الدعم الفني بطيء جدا والأسعار مرتفعة لكن الميزات ممتازة"
ARABIC_COMMENT = "أحتاج ميزة تتبع الدخل غير المنتظم للعاملين المستقلين في مصر"
PLATFORMS = ["x", "tiktok", "instagram", "facebook", "youtube", "reddit"]


def envelope(capability: str) -> dict[str, Any]:
    return fixture_json("providers", "socialcrawl", f"{capability.replace(':', '.')}.json")


def api(respx_mock: respx.MockRouter, path: str, *responses: httpx.Response) -> respx.Route:
    route = respx_mock.get(f"{BASE}{path}")
    return (
        route.mock(side_effect=list(responses))
        if len(responses) > 1
        else route.mock(return_value=responses[0])
    )


async def call(capability: str, **params: Any) -> Any:
    async with make_client(5) as client:
        provider = SocialCrawlProvider(
            client, "sc-key", BASE, CONFIG, clock=lambda: NOW, limiter=RateLimiter(0)
        )
        return await provider.call(capability, call_params(**params))


def items(result: Any) -> list[EvidenceItem]:
    return list(result.items)


def query_of(route: respx.Route) -> dict[str, str]:
    return dict(route.calls.last.request.url.params)


def test_capabilities_are_social_search_and_comments_plus_search_interest() -> None:
    provider = SocialCrawlProvider(httpx.AsyncClient(), "k", BASE, CONFIG)
    expected = {f"{kind}:{p}" for kind in ("social_search", "social_comments") for p in PLATFORMS}
    assert provider.capabilities == {*expected, "search_interest"}


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_search_maps_posts_for_every_platform(
    respx_mock: respx.MockRouter, platform: str
) -> None:
    capability = f"social_search:{platform}"
    path = CONFIG.options["endpoints"][capability]["path"]
    api(respx_mock, path, httpx.Response(200, json=envelope(capability)))
    result = await call(capability, query="gitlab duo", max_results=3, languages=["ar", "en"])
    first, arabic, minimal = items(result)
    assert (first.platform, first.source_type, first.provider) == (
        Platform(platform),
        SourceType.SOCIAL_POST,
        "socialcrawl",
    )
    assert first.text.startswith("GitLab Duo gave better review comments")
    assert first.language == "en"
    assert first.author == "author_1"
    assert first.published_at == datetime(2026, 9, 1, 10, tzinfo=UTC)
    assert arabic.text.startswith(ARABIC) and arabic.language == "ar"
    assert minimal.text == "short" and minimal.published_at is None and minimal.engagement == {}
    assert result.raw_count == 3
    assert result.cost_estimate == pytest.approx(0.0033)
    assert first.batch_id == "pending"


async def test_post_engagement_is_normalized(respx_mock: respx.MockRouter) -> None:
    api(respx_mock, "/twitter/search/tweets", httpx.Response(200, json=envelope("social_search:x")))
    first = items(await call("social_search:x", query="q"))[0]
    assert first.engagement == {"likes": 20, "replies": 2, "shares": 1, "views": 1000}
    assert first.metadata["saves"] == 1
    assert first.metadata["relevance"] == {"p": 0.9, "sense": "target"}
    assert first.metadata["native_id"] == "x-1"


async def test_reddit_votes_are_upvotes_and_the_body_is_part_of_the_text(
    respx_mock: respx.MockRouter,
) -> None:
    api(respx_mock, "/reddit/search", httpx.Response(200, json=envelope("social_search:reddit")))
    first, *_ = items(await call("social_search:reddit", query="q"))
    assert first.engagement["upvotes"] == 20 and "likes" not in first.engagement
    assert (
        first.text == "GitLab Duo gave better review comments than Copilot on our monorepo, "
        "but setup was confusing.\nDetails of the post body."
    )


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_comments_map_for_every_platform(respx_mock: respx.MockRouter, platform: str) -> None:
    capability = f"social_comments:{platform}"
    path = CONFIG.options["endpoints"][capability]["path"]
    api(
        respx_mock,
        "/credits/balance",
        httpx.Response(200, json={"success": True, "data": {"balance": 99}}),
    )
    route = api(respx_mock, path, httpx.Response(200, json=envelope(capability)))
    result = await call(capability, post_url="https://example.test/post/1", max_results=3)
    first, arabic, minimal = items(result)
    assert query_of(route)["url"] == "https://example.test/post/1"
    assert (first.platform, first.source_type) == (Platform(platform), SourceType.SOCIAL_COMMENT)
    assert first.engagement == {"likes": 1, "replies": 0}
    assert first.author == "reader_1"
    assert arabic.text == ARABIC_COMMENT
    assert minimal.text == "+1" and minimal.engagement == {} and minimal.author is None
    from edrak.agents.customer_trends.utils.text import evidence_id

    assert first.id == evidence_id(f"{platform}|comment|cm-1")


async def test_search_request_shape(respx_mock: respx.MockRouter) -> None:
    route = api(
        respx_mock, "/twitter/search/tweets", httpx.Response(200, json=envelope("social_search:x"))
    )
    await call(
        "social_search:x",
        query="gitlab duo",
        hashtags=["devops"],
        since="2026-09-01",
        until="2026-09-30",
        sort="top",
        max_results=3,
    )
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "sc-key"
    assert "sc-key" not in str(request.url)
    assert query_of(route) == {
        "query": "gitlab duo devops since:2026-09-01 until:2026-09-30",
        "relevance": "filter",
        "sort": "top",
    }


@pytest.mark.parametrize(
    ("capability", "path", "sort", "expected"),
    [
        ("social_search:tiktok", "/tiktok/search", "recent", {}),
        ("social_search:tiktok", "/tiktok/search", "top", {}),
        ("social_search:reddit", "/reddit/search", "recent", {"sort": "new"}),
        ("social_search:youtube", "/youtube/search", "top", {"sortBy": "popular"}),
        ("social_search:youtube", "/youtube/search", "recent", {}),
        ("social_search:instagram", "/instagram/search/popular", "top", {}),
    ],
)
async def test_sort_values_follow_the_endpoint(
    respx_mock: respx.MockRouter, capability: str, path: str, sort: str, expected: dict[str, str]
) -> None:
    route = api(respx_mock, path, httpx.Response(200, json=envelope(capability)))
    await call(capability, query="q", sort=sort, max_results=3)
    sent = query_of(route)
    assert {k: v for k, v in sent.items() if k not in ("query", "relevance")} == expected


async def test_comment_sort_and_pages(respx_mock: respx.MockRouter) -> None:
    route = api(
        respx_mock,
        "/youtube/video/comments",
        httpx.Response(200, json=envelope("social_comments:youtube")),
    )
    await call(
        "social_comments:youtube", post_url="https://www.youtube.com/watch?v=abc", sort="recent"
    )
    assert query_of(route) == {"url": "https://www.youtube.com/watch?v=abc", "order": "newest"}


async def test_pagination_sends_the_cursor_verbatim_until_enough_items(
    respx_mock: respx.MockRouter,
) -> None:
    route = api(
        respx_mock,
        "/twitter/search/tweets",
        httpx.Response(
            200, json=fixture_json("providers", "socialcrawl", "social_search.x.page1.json")
        ),
        httpx.Response(
            200, json=fixture_json("providers", "socialcrawl", "social_search.x.page2.json")
        ),
    )
    result = await call("social_search:x", query="q", max_results=5)
    assert route.call_count == 2
    assert "cursor" not in dict(route.calls[0].request.url.params)
    assert dict(route.calls[1].request.url.params)["cursor"] == "sc.cursor-two"
    assert len(items(result)) == 5
    assert result.raw_count == 6
    assert result.next_cursor is None
    assert result.cost_estimate == pytest.approx(0.0066)


async def test_a_cursor_is_returned_when_more_pages_exist(respx_mock: respx.MockRouter) -> None:
    api(
        respx_mock,
        "/twitter/search/tweets",
        httpx.Response(
            200, json=fixture_json("providers", "socialcrawl", "social_search.x.page1.json")
        ),
    )
    result = await call("social_search:x", query="q", max_results=3)
    assert result.next_cursor == "sc.cursor-two"


async def test_the_caller_cursor_resumes_a_search(respx_mock: respx.MockRouter) -> None:
    route = api(
        respx_mock, "/twitter/search/tweets", httpx.Response(200, json=envelope("social_search:x"))
    )
    await call("social_search:x", query="q", cursor="sc.resume")
    assert query_of(route)["cursor"] == "sc.resume"


async def test_paging_stops_at_the_page_cap(respx_mock: respx.MockRouter) -> None:
    api(
        respx_mock,
        "/credits/balance",
        httpx.Response(200, json={"success": True, "data": {"balance": 100}}),
    )
    page = fixture_json("providers", "socialcrawl", "social_search.x.page1.json")
    route = api(respx_mock, "/twitter/search/tweets", httpx.Response(200, json=page))
    result = await call("social_search:x", query="q", max_results=1000)
    assert route.call_count == 5
    assert result.raw_count == 15


async def test_items_outside_the_requested_languages_are_dropped_with_a_warning(
    respx_mock: respx.MockRouter,
) -> None:
    api(respx_mock, "/twitter/search/tweets", httpx.Response(200, json=envelope("social_search:x")))
    result = await call("social_search:x", query="q", languages=["en"])
    assert [i.language for i in items(result)] == ["en", None]
    assert result.warnings == ["dropped 1 item(s) outside the requested languages ['en']"]
    assert result.raw_count == 3


async def test_items_outside_the_date_window_are_dropped(respx_mock: respx.MockRouter) -> None:
    api(respx_mock, "/tiktok/search", httpx.Response(200, json=envelope("social_search:tiktok")))
    result = await call("social_search:tiktok", query="q", since="2026-09-02")
    assert [i.published_at.day for i in items(result) if i.published_at] == [2]
    assert len(items(result)) == 2


async def test_credits_remaining_reads_the_free_balance_endpoint(
    respx_mock: respx.MockRouter,
) -> None:
    route = api(
        respx_mock,
        "/credits/balance",
        httpx.Response(
            200, json={"success": True, "data": {"balance": 87, "recent_deductions": 3}}
        ),
    )
    async with make_client(5) as client:
        provider = SocialCrawlProvider(client, "sc-key", BASE, CONFIG, limiter=RateLimiter(0))
        assert await provider.credits_remaining() == 87
    assert route.calls.last.request.headers["x-api-key"] == "sc-key"


async def test_a_large_request_checks_credits_first_and_stops_when_short(
    respx_mock: respx.MockRouter,
) -> None:
    balance = api(
        respx_mock,
        "/credits/balance",
        httpx.Response(200, json={"success": True, "data": {"balance": 3}}),
    )
    search = api(
        respx_mock,
        "/instagram/post/comments",
        httpx.Response(200, json=envelope("social_comments:instagram")),
    )
    with pytest.raises(ProviderQuotaExceeded, match="3 credits left"):
        await call("social_comments:instagram", post_url="https://example.test/p", max_results=30)
    assert balance.call_count == 1 and search.call_count == 0


async def test_a_large_request_goes_ahead_with_enough_credits(respx_mock: respx.MockRouter) -> None:
    balance = api(
        respx_mock,
        "/credits/balance",
        httpx.Response(200, json={"success": True, "data": {"balance": 80}}),
    )
    search = api(
        respx_mock,
        "/instagram/post/comments",
        httpx.Response(200, json=envelope("social_comments:instagram")),
    )
    result = await call(
        "social_comments:instagram", post_url="https://example.test/p", max_results=15
    )
    assert balance.call_count == 1 and search.call_count == 1
    assert len(items(result)) == 3


async def test_a_small_request_skips_the_credit_check(respx_mock: respx.MockRouter) -> None:
    balance = api(
        respx_mock,
        "/credits/balance",
        httpx.Response(200, json={"success": True, "data": {"balance": 0}}),
    )
    api(respx_mock, "/twitter/search/tweets", httpx.Response(200, json=envelope("social_search:x")))
    await call("social_search:x", query="q", max_results=20)
    assert balance.call_count == 0


async def test_paging_stops_when_the_balance_runs_out(respx_mock: respx.MockRouter) -> None:
    api(
        respx_mock,
        "/credits/balance",
        httpx.Response(200, json={"success": True, "data": {"balance": 100}}),
    )
    page = fixture_json("providers", "socialcrawl", "social_search.x.page1.json")
    page["credits_remaining"] = 0
    route = api(respx_mock, "/twitter/search/tweets", httpx.Response(200, json=page))
    result = await call("social_search:x", query="q", max_results=100)
    assert route.call_count == 1
    assert result.partial is True
    assert "stopped early: 0 credits left" in result.warnings


async def test_a_post_without_comments_is_an_empty_result(respx_mock: respx.MockRouter) -> None:
    api(
        respx_mock,
        "/tiktok/post/comments",
        httpx.Response(
            404, json={"success": False, "error": {"type": "RESOURCE_NOT_FOUND", "status": 404}}
        ),
    )
    result = await call("social_comments:tiktok", post_url="https://example.test/gone")
    assert result.items == []
    assert result.warnings == ["socialcrawl: nothing found"]


async def test_error_mapping(respx_mock: respx.MockRouter) -> None:
    def error(status: int, kind: str) -> httpx.Response:
        return httpx.Response(
            status, json={"success": False, "error": {"type": kind, "status": status}}
        )

    route = respx_mock.get(f"{BASE}/twitter/search/tweets")
    route.mock(return_value=error(401, "INVALID_API_KEY"))
    with pytest.raises(ProviderNotConfigured):
        await call("social_search:x", query="q")
    route.mock(return_value=error(402, "INSUFFICIENT_CREDITS"))
    with pytest.raises(ProviderQuotaExceeded):
        await call("social_search:x", query="q")
    route.mock(return_value=error(429, "RATE_LIMITED"))
    with pytest.raises(ProviderRateLimited):
        await call("social_search:x", query="q")
    route.mock(return_value=error(503, "SERVICE_UNAVAILABLE"))
    with pytest.raises(ProviderUnavailable):
        await call("social_search:x", query="q")
    route.mock(return_value=error(400, "INVALID_REQUEST"))
    with pytest.raises(ProviderBadResponse, match="INVALID_REQUEST"):
        await call("social_search:x", query="q")


async def test_a_not_found_search_is_still_an_error(respx_mock: respx.MockRouter) -> None:
    api(
        respx_mock,
        "/twitter/search/tweets",
        httpx.Response(404, json={"success": False, "error": {"type": "RESOURCE_NOT_FOUND"}}),
    )
    with pytest.raises(ProviderBadResponse):
        await call("social_search:x", query="q")


async def test_retry_after_is_honored_on_rate_limits(respx_mock: respx.MockRouter) -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    from edrak.agents.customer_trends.providers import http

    original = http._sleep
    http._sleep = record
    try:
        api(
            respx_mock,
            "/twitter/search/tweets",
            httpx.Response(429, headers={"Retry-After": "12"}),
            httpx.Response(200, json=envelope("social_search:x")),
        )
        await call("social_search:x", query="q")
    finally:
        http._sleep = original
    assert waits and waits[0] >= 12


async def test_missing_input_and_unknown_capabilities_are_rejected(
    respx_mock: respx.MockRouter,
) -> None:
    with pytest.raises(ProviderBadResponse, match="needs query"):
        await call("social_search:x")
    with pytest.raises(ProviderBadResponse, match="needs post_url"):
        await call("social_comments:x")
    with pytest.raises(ProviderBadResponse, match="unsupported"):
        await call("reviews:amazon", target="x")


async def test_an_unsuccessful_envelope_is_a_bad_response(respx_mock: respx.MockRouter) -> None:
    api(respx_mock, "/twitter/search/tweets", httpx.Response(200, json={"success": False}))
    with pytest.raises(ProviderBadResponse):
        await call("social_search:x", query="q")


def test_the_request_body_never_carries_the_key_in_the_url() -> None:
    assert json.dumps(CONFIG.options["endpoints"]).count("key") == 0


# search interest

EXPLORE = f"{BASE}/google_trends/explore"


def explore_page() -> httpx.Response:
    return httpx.Response(
        200, json=fixture_json("providers", "socialcrawl", "search_interest.json")
    )


def balance(respx_mock: respx.MockRouter, credits: int = 100) -> respx.Route:
    return api(
        respx_mock,
        "/credits/balance",
        httpx.Response(200, json={"success": True, "data": {"balance": credits}}),
    )


async def test_search_interest_sends_keywords_together_and_returns_one_series_each(
    respx_mock: respx.MockRouter,
) -> None:
    balance(respx_mock)
    route = respx_mock.get(EXPLORE).mock(return_value=explore_page())
    result = await call(
        "search_interest",
        keywords=["gitlab duo", "github copilot"],
        timeframe="today 12-m",
        geo="US",
    )
    assert dict(route.calls.last.request.url.params) == {
        "keywords": "gitlab duo,github copilot",
        "timeframe": "past_12_months",
        "location": "US",
    }
    assert route.calls.last.request.headers["x-api-key"] == "sc-key"
    duo, copilot = result.items
    assert isinstance(duo, TrendSeries) and isinstance(copilot, TrendSeries)
    assert (duo.keyword, duo.geo, duo.timeframe, duo.granularity) == (
        "gitlab duo",
        "US",
        "today 12-m",
        "day",
    )
    assert (duo.normalized, duo.source, duo.batch_id) == (True, "socialcrawl", "pending")
    assert [v for _, v in duo.points] == [20.0, 35.0, 50.0, 80.0, 100.0, 90.0, 85.0]
    assert len(copilot.points) == 8
    assert result.cost_estimate == pytest.approx(5 * 0.0033)
    assert result.meta["credits_used"] == 5


@pytest.mark.parametrize(
    ("timeframe", "sent"),
    [
        ("now 1-H", "past_hour"),
        ("now 4-H", "past_4_hours"),
        ("now 1-d", "past_day"),
        ("now 7-d", "past_7_days"),
        ("today 1-m", "past_30_days"),
        ("today 3-m", "past_90_days"),
        ("today 12-m", "past_12_months"),
        ("today 5-y", "past_5_years"),
    ],
)
async def test_search_interest_maps_the_timeframes(
    respx_mock: respx.MockRouter, timeframe: str, sent: str
) -> None:
    balance(respx_mock)
    route = respx_mock.get(EXPLORE).mock(return_value=explore_page())
    await call("search_interest", keywords=["a"], timeframe=timeframe)
    assert dict(route.calls.last.request.url.params)["timeframe"] == sent
    assert "location" not in dict(route.calls.last.request.url.params)


async def test_search_interest_refuses_what_it_cannot_ask_for(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(EXPLORE).mock(return_value=explore_page())
    with pytest.raises(ProviderBadResponse, match="timeframe"):
        await call("search_interest", keywords=["a"], timeframe="all")
    with pytest.raises(ProviderBadResponse, match="1 to 5 keywords"):
        await call("search_interest", keywords=[])
    with pytest.raises(ProviderBadResponse, match="1 to 5 keywords"):
        await call("search_interest", keywords=list("abcdef"))
    assert route.call_count == 0


async def test_search_interest_checks_the_balance_first(respx_mock: respx.MockRouter) -> None:
    balance(respx_mock, credits=2)
    route = respx_mock.get(EXPLORE).mock(return_value=explore_page())
    with pytest.raises(ProviderQuotaExceeded, match="2 credits left, 5 needed"):
        await call("search_interest", keywords=["a"])
    assert route.call_count == 0


async def test_search_interest_without_data_is_an_empty_result(
    respx_mock: respx.MockRouter,
) -> None:
    balance(respx_mock)
    respx_mock.get(EXPLORE).mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": {"series": [{"keyword": "x", "points": []}]}}
        )
    )
    assert (await call("search_interest", keywords=["x"])).items == []
