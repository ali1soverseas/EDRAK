import json
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from edrak.agents.customer_trends.providers.apify import ApifyProvider
from edrak.agents.customer_trends.providers.apify.client import (
    ApifyClient,
    RunNotFinished,
    actor_path,
)
from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderUnavailable,
)
from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.providers.http import make_client
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from tests.customer_trends.factories import NOW, call_params, fixture_json

RUN_SYNC = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
CONFIG = load_providers_config().providers["apify"]


def route(respx_mock: respx.MockRouter, actor: str, response: httpx.Response) -> respx.Route:
    return respx_mock.post(RUN_SYNC.format(actor=actor.replace("/", "~"))).mock(
        return_value=response
    )


def served(fixture: str) -> httpx.Response:
    return httpx.Response(201, json=fixture_json("providers", "apify", f"{fixture}.json"))


async def call(capability: str, **params: Any) -> Any:
    async with make_client(5) as client:
        provider = ApifyProvider(client, "apify-token", CONFIG, clock=lambda: NOW)
        return await provider.call(capability, call_params(**params))


def sent(route_: respx.Route) -> dict[str, Any]:
    return json.loads(route_.calls.last.request.read())


# the client


async def test_client_request_shape(respx_mock: respx.MockRouter) -> None:
    api = route(
        respx_mock, "apidojo/tweet-scraper", httpx.Response(201, json=[{"a": 1}, "skipped"])
    )
    async with make_client(5) as http:
        items = await ApifyClient(http, "tok").run(
            "apidojo/tweet-scraper", {"searchTerms": ["x"]}, limit=7, timeout_s=999
        )
    request = api.calls.last.request
    assert items == [{"a": 1}]
    assert request.url.path == "/v2/acts/apidojo~tweet-scraper/run-sync-get-dataset-items"
    assert dict(request.url.params) == {"limit": "7", "timeout": "300"}
    assert request.headers["authorization"] == "Bearer tok"
    assert json.loads(request.read()) == {"searchTerms": ["x"]}


def test_actor_path_replaces_the_slash() -> None:
    assert actor_path("apify/instagram-scraper") == "apify~instagram-scraper"


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401), ProviderNotConfigured),
        (httpx.Response(403), ProviderNotConfigured),
        (
            httpx.Response(402, json={"error": {"type": "not-enough-usage-to-run-paid-actor"}}),
            ProviderQuotaExceeded,
        ),
        (
            httpx.Response(
                400,
                json={"error": {"type": "invalid-input", "message": "maxResults must be >= 10"}},
            ),
            ProviderBadResponse,
        ),
        (httpx.Response(404), ProviderBadResponse),
        (httpx.Response(200, json={"not": "a list"}), ProviderBadResponse),
    ],
)
async def test_client_error_mapping(
    respx_mock: respx.MockRouter, response: httpx.Response, error: type[Exception]
) -> None:
    api = route(respx_mock, "a/b", response)
    async with make_client(5) as http:
        with pytest.raises(error):
            await ApifyClient(http, "tok").run("a/b", {}, limit=1)
    assert api.call_count == 1


async def test_rate_limits_and_server_errors_are_retried(respx_mock: respx.MockRouter) -> None:
    api = respx_mock.post(RUN_SYNC.format(actor="a~b")).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(503),
            httpx.Response(201, json=[{"ok": 1}]),
        ]
    )
    async with make_client(5) as http:
        assert await ApifyClient(http, "tok").run("a/b", {}, limit=1) == [{"ok": 1}]
    assert api.call_count == 3


async def test_persistent_rate_limits_are_reported(respx_mock: respx.MockRouter) -> None:
    api = route(respx_mock, "a/b", httpx.Response(429))
    async with make_client(5) as http:
        with pytest.raises(ProviderRateLimited):
            await ApifyClient(http, "tok").run("a/b", {}, limit=1)
    assert api.call_count == 3


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(408),
        httpx.Response(
            400,
            json={
                "error": {
                    "type": "run-failed",
                    "message": "Actor run did not succeed (status: TIMED-OUT).",
                }
            },
        ),
        httpx.Response(
            400,
            json={
                "error": {
                    "type": "run-failed",
                    "message": "Actor run did not succeed (status: FAILED).",
                }
            },
        ),
    ],
)
async def test_runs_that_time_out_or_fail_are_unavailable_and_never_retried(
    respx_mock: respx.MockRouter, response: httpx.Response
) -> None:
    api = route(respx_mock, "a/b", response)
    async with make_client(5) as http:
        with pytest.raises(RunNotFinished) as caught:
            await ApifyClient(http, "tok").run("a/b", {}, limit=1)
    assert isinstance(caught.value, ProviderUnavailable)
    assert api.call_count == 1


async def test_a_connection_timeout_is_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(RUN_SYNC.format(actor="a~b")).mock(side_effect=httpx.ReadTimeout("slow"))
    async with make_client(5) as http:
        with pytest.raises(ProviderUnavailable, match="timed out"):
            await ApifyClient(http, "tok").run("a/b", {}, limit=1)


# the provider


def test_capabilities_cover_every_enabled_actor() -> None:
    provider = ApifyProvider(httpx.AsyncClient(), "tok", CONFIG)
    assert provider.capabilities == {
        *(f"social_search:{p}" for p in ("x", "tiktok", "instagram", "youtube", "reddit")),
        *(
            f"social_comments:{p}"
            for p in ("x", "tiktok", "instagram", "facebook", "youtube", "reddit")
        ),
        "search_interest",
        "reviews:app_store",
        "reviews:google_play",
        "reviews:amazon",
    }
    assert "social_search:facebook" not in provider.capabilities
    assert provider.spec_for("social_search:facebook").enabled is False


async def test_a_disabled_capability_is_refused() -> None:
    with pytest.raises(ProviderBadResponse, match="unsupported"):
        await call("social_search:facebook", query="q")


async def test_x_search_input_and_result(respx_mock: respx.MockRouter) -> None:
    api = route(respx_mock, "apidojo/tweet-scraper", served("x_post"))
    result = await call(
        "social_search:x", query="gitlab duo", hashtags=["devops"], max_results=3, languages=["en"],
        since="2026-09-01", until="2026-10-31", sort="top",
    )  # fmt: skip
    assert sent(api) == {
        "searchTerms": ["gitlab duo devops"], "maxItems": 3, "sort": "Top", "tweetLanguage": "en",
        "start": "2026-09-01", "end": "2026-10-31",
    }  # fmt: skip
    assert dict(api.calls.last.request.url.params)["limit"] == "3"
    assert [i.platform for i in result.items] == [Platform.X] * 3
    assert result.raw_count == 3
    assert result.cost_estimate == pytest.approx(3 * 0.0004)
    assert result.provider == ""  # tagged by the registry, not the provider


async def test_two_languages_leave_the_actors_language_filter_out(
    respx_mock: respx.MockRouter,
) -> None:
    api = route(respx_mock, "apidojo/tweet-scraper", served("x_post"))
    await call("social_search:x", query="q", languages=["ar", "en"])
    assert "tweetLanguage" not in sent(api)
    assert sent(api)["maxItems"] == 10
    assert sent(api)["sort"] == "Latest"


async def test_tiktok_search_input_uses_a_named_date_window(respx_mock: respx.MockRouter) -> None:
    api = route(respx_mock, "clockworks/tiktok-scraper", served("tiktok_video"))
    result = await call(
        "social_search:tiktok", query="gitlab duo", max_results=3, since="2026-08-15", sort="top"
    )
    assert sent(api) == {
        "searchQueries": ["gitlab duo"], "resultsPerPage": 3, "searchSection": "/video",
        "videoSearchSorting": "MOST_LIKED", "videoSearchDateFilter": "LAST_3_MONTHS",
    }  # fmt: skip
    assert result.cost_estimate == pytest.approx(0.001 + 3 * 0.0037)


async def test_instagram_search_reads_a_hashtag_page(respx_mock: respx.MockRouter) -> None:
    api = route(respx_mock, "apify/instagram-scraper", served("instagram_post"))
    await call("social_search:instagram", query="GitLab Duo", max_results=5, since="2026-09-20")
    assert sent(api) == {
        "directUrls": ["https://www.instagram.com/explore/tags/gitlabduo/"], "resultsType": "posts",
        "resultsLimit": 5, "onlyPostsNewerThan": "2026-09-20",
    }  # fmt: skip


async def test_reddit_comments_input_asks_for_one_more_item_for_the_post(
    respx_mock: respx.MockRouter,
) -> None:
    api = route(respx_mock, "trudax/reddit-scraper-lite", served("reddit"))
    result = await call(
        "social_comments:reddit",
        post_url="https://www.reddit.com/r/devops/comments/p1/post_1/",
        max_results=5,
    )
    assert sent(api) == {
        "startUrls": [{"url": "https://www.reddit.com/r/devops/comments/p1/post_1/"}],
        "maxItems": 6, "maxComments": 5, "skipUserPosts": True, "skipCommunity": True,
    }  # fmt: skip
    assert [i.source_type for i in result.items] == [SourceType.SOCIAL_COMMENT] * 3


async def test_reddit_search_returns_only_posts(respx_mock: respx.MockRouter) -> None:
    route(respx_mock, "trudax/reddit-scraper-lite", served("reddit"))
    result = await call("social_search:reddit", query="duo", max_results=10)
    assert [i.source_type for i in result.items] == [SourceType.SOCIAL_POST] * 3
    assert result.raw_count == 6


async def test_x_replies_use_the_conversation_id(respx_mock: respx.MockRouter) -> None:
    api = route(respx_mock, "apidojo/tweet-scraper", served("x_reply"))
    await call(
        "social_comments:x",
        post_url="https://x.com/someone/status/2103012192865960302",
        max_results=3,
    )
    assert sent(api) == {"conversationIds": ["2103012192865960302"], "maxItems": 3}


async def test_youtube_inputs(respx_mock: respx.MockRouter) -> None:
    search = route(respx_mock, "streamers/youtube-scraper", served("youtube_video"))
    await call("social_search:youtube", query="duo", max_results=4, since="2026-09-25", sort="top")
    assert sent(search) == {
        "searchQueries": ["duo"], "maxResults": 4, "maxResultsShorts": 0, "maxResultStreams": 0,
        "sortingOrder": "views", "dateFilter": "week",
    }  # fmt: skip
    comments = route(respx_mock, "streamers/youtube-comments-scraper", served("youtube_comment"))
    await call(
        "social_comments:youtube", post_url="https://www.youtube.com/watch?v=abc", max_results=2
    )
    assert sent(comments) == {
        "startUrls": [{"url": "https://www.youtube.com/watch?v=abc"}], "maxComments": 2,
        "sortCommentsBy": "NEWEST_FIRST",
    }  # fmt: skip


async def test_facebook_and_tiktok_comment_inputs(respx_mock: respx.MockRouter) -> None:
    facebook = route(respx_mock, "apify/facebook-comments-scraper", served("facebook_comment"))
    await call(
        "social_comments:facebook", post_url="https://www.facebook.com/p/posts/1", max_results=3
    )
    assert sent(facebook) == {
        "startUrls": [{"url": "https://www.facebook.com/p/posts/1"}],
        "resultsLimit": 3,
    }
    tiktok = route(respx_mock, "clockworks/tiktok-comments-scraper", served("tiktok_comment"))
    await call(
        "social_comments:tiktok", post_url="https://www.tiktok.com/@a/video/1", max_results=3
    )
    assert sent(tiktok) == {"postURLs": ["https://www.tiktok.com/@a/video/1"], "commentsPerPost": 3}


async def test_search_interest_returns_trend_series(respx_mock: respx.MockRouter) -> None:
    api = route(respx_mock, "apify/google-trends-scraper", served("google_trends"))
    result = await call(
        "search_interest",
        keywords=["gitlab duo", "copilot"],
        timeframe="today 3-m",
        geo="US",
        max_results=2,
    )
    assert sent(api) == {
        "searchTerms": ["gitlab duo", "copilot"],
        "timeRange": "today 3-m",
        "geo": "US",
        "maxItems": 2,
    }
    assert all(isinstance(s, TrendSeries) for s in result.items)
    assert [s.keyword for s in result.items if isinstance(s, TrendSeries)] == [
        "gitlab duo",
        "جيت لاب",
        "copilot",
    ]
    assert result.cost_estimate == pytest.approx(3 * 0.003)


async def test_search_interest_falls_back_to_the_query_as_the_keyword(
    respx_mock: respx.MockRouter,
) -> None:
    api = route(respx_mock, "apify/google-trends-scraper", served("google_trends"))
    await call("search_interest", query="gitlab duo")
    assert sent(api)["searchTerms"] == ["gitlab duo"]
    assert sent(api)["timeRange"] == "today 12-m"


async def test_review_inputs(respx_mock: respx.MockRouter) -> None:
    app = route(respx_mock, "thewolves/appstore-reviews-scraper", served("app_store_review"))
    await call("reviews:app_store", target="324684580", country="US", max_results=3)
    assert sent(app) == {"appIds": ["324684580"], "country": "us", "maxItems": 3}
    play = route(respx_mock, "thewolves/google-play-reviews-scraper", served("google_play_review"))
    await call(
        "reviews:google_play",
        target="com.spotify.music",
        country="eg",
        languages=["ar"],
        max_results=3,
    )
    assert sent(play) == {
        "appIds": ["com.spotify.music"],
        "country": "EG",
        "language": "ar",
        "sort": "NEWEST",
        "maxItems": 3,
    }
    amazon = route(respx_mock, "junglee/amazon-reviews-scraper", served("amazon_review"))
    result = await call("reviews:amazon", target="B0BTYCRJSS", max_results=3)
    assert sent(amazon) == {
        "productUrls": [{"url": "https://www.amazon.com/dp/B0BTYCRJSS"}],
        "maxReviews": 3,
        "sort": "recent",
    }
    assert {i.source_type for i in result.items if isinstance(i, EvidenceItem)} == {
        SourceType.REVIEW
    }


async def test_missing_required_input_is_reported_before_any_run(
    respx_mock: respx.MockRouter,
) -> None:
    api = route(respx_mock, "clockworks/tiktok-comments-scraper", served("tiktok_comment"))
    with pytest.raises(ProviderBadResponse, match="needs url"):
        await call("social_comments:tiktok")
    with pytest.raises(ProviderBadResponse, match="needs query"):
        await call("social_search:x")
    assert api.call_count == 0


async def test_items_outside_the_date_window_are_dropped_and_counted(
    respx_mock: respx.MockRouter,
) -> None:
    route(respx_mock, "apidojo/tweet-scraper", served("x_post"))
    result = await call("social_search:x", query="q", since="2026-10-02", until="2026-10-31")
    assert len(result.items) == 2
    assert result.meta["outside_date_window"] == 1
    assert date(2026, 10, 1) not in {i.published_at.date() for i in result.items if i.published_at}


async def test_results_are_capped_at_the_requested_number(respx_mock: respx.MockRouter) -> None:
    route(respx_mock, "apidojo/tweet-scraper", served("x_post"))
    result = await call("social_search:x", query="q", max_results=2)
    assert len(result.items) == 2 and result.raw_count == 3


async def test_an_actor_run_that_times_out_is_unavailable(respx_mock: respx.MockRouter) -> None:
    route(
        respx_mock,
        "apify/google-trends-scraper",
        httpx.Response(400, json={"error": {"type": "run-failed", "message": "status: TIMED-OUT"}}),
    )
    with pytest.raises(RunNotFinished):
        await call("search_interest", keywords=["x"])
