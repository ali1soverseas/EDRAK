import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
    ProviderRateLimited,
)
from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.http import make_client
from edrak.agents.customer_trends.providers.youtube import (
    YouTubeProvider,
    YouTubeQuota,
    video_id_from_url,
)
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from tests.customer_trends.factories import NOW, call_params

SEARCH = "https://www.googleapis.com/youtube/v3/search"
VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
THREADS = "https://www.googleapis.com/youtube/v3/commentThreads"
CONFIG = ProviderConfig(options={"comments_per_page": 100, "search_units": 100, "read_units": 1})


def vid(n: int) -> str:
    return f"vid{n:08d}"


def search_page(start: int, count: int, next_token: str | None = None) -> dict[str, Any]:
    page: dict[str, Any] = {
        "items": [
            {
                "id": {"kind": "youtube#video", "videoId": vid(i)},
                "snippet": {"title": f"Video {i}", "description": "short", "channelTitle": "Chan"},
            }
            for i in range(start, start + count)
        ]
    }
    if next_token:
        page["nextPageToken"] = next_token
    return page


def videos_page(ids: list[str]) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": video,
                "snippet": {
                    "title": f"Full title {video}",
                    "description": "Full description of the video",
                    "channelTitle": "Chan",
                    "channelId": "UC1",
                    "publishedAt": "2026-09-10T08:30:00Z",
                    "defaultAudioLanguage": "en-US",
                },
                "statistics": {"viewCount": "1200", "likeCount": "45", "commentCount": "7"},
            }
            for video in ids
        ]
    }


def thread(i: int, published: str = "2026-09-12T10:00:00Z") -> dict[str, Any]:
    return {
        "id": f"thread{i}",
        "snippet": {
            "totalReplyCount": i,
            "topLevelComment": {
                "id": f"comment{i}",
                "snippet": {
                    "textDisplay": f"تعليق رقم {i}: الدعم بطيء",
                    "authorDisplayName": f"user{i}",
                    "likeCount": 10 + i,
                    "publishedAt": published,
                },
            },
        },
    }


@pytest.fixture
def quota(tmp_path: Path) -> YouTubeQuota:
    return YouTubeQuota(tmp_path / "quota.json", 10_000, 100, clock=lambda: NOW)


async def run(capability: str, quota: YouTubeQuota, **params: Any) -> Any:
    async with make_client(5) as client:
        provider = YouTubeProvider(client, "yt-key", CONFIG, quota, clock=lambda: NOW)
        return await provider.call(capability, call_params(**params))


def evidence(result: Any) -> list[EvidenceItem]:
    return list(result.items)


async def test_search_request_shape_and_batched_video_lookup(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    search = respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 3)))
    videos = respx_mock.get(VIDEOS).mock(
        return_value=httpx.Response(200, json=videos_page([vid(i) for i in range(3)]))
    )
    await run(
        "social_search:youtube",
        quota,
        query="gitlab duo",
        hashtags=["#devops"],
        max_results=3,
        geo="eg",
        languages=["ar", "en"],
        since=date(2026, 4, 1),
        until=date(2026, 9, 30),
        sort="top",
    )
    request = search.calls.last.request
    assert request.headers["x-goog-api-key"] == "yt-key"
    assert "key" not in request.url.params
    assert dict(request.url.params) == {
        "part": "snippet",
        "type": "video",
        "q": "gitlab duo #devops",
        "maxResults": "3",
        "order": "relevance",
        "regionCode": "EG",
        "relevanceLanguage": "ar",
        "publishedAfter": "2026-04-01T00:00:00Z",
        "publishedBefore": "2026-10-01T00:00:00Z",
    }
    assert videos.call_count == 1
    assert dict(videos.calls.last.request.url.params) == {
        "part": "snippet,statistics",
        "id": ",".join(vid(i) for i in range(3)),
        "maxResults": "3",
    }


async def test_recent_sort_uses_date_order(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    search = respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 1)))
    respx_mock.get(VIDEOS).mock(return_value=httpx.Response(200, json=videos_page([vid(0)])))
    await run("social_search:youtube", quota, query="q", max_results=1)
    assert search.calls.last.request.url.params["order"] == "date"


async def test_videos_map_to_evidence_with_engagement(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 1)))
    respx_mock.get(VIDEOS).mock(return_value=httpx.Response(200, json=videos_page([vid(0)])))
    result = await run("social_search:youtube", quota, query="q", max_results=5)
    [item] = evidence(result)
    assert (item.source_type, item.platform, item.provider) == (
        SourceType.SOCIAL_POST,
        Platform.YOUTUBE,
        "youtube_api",
    )
    assert item.text == f"Full title {vid(0)}\nFull description of the video"
    assert item.url == f"https://www.youtube.com/watch?v={vid(0)}"
    assert item.author == "Chan"
    assert item.engagement == {"views": 1200, "likes": 45, "replies": 7}
    assert item.published_at == datetime(2026, 9, 10, 8, 30, tzinfo=UTC)
    assert item.language == "en"
    assert item.snippet_only is False
    assert item.metadata == {"video_id": vid(0), "channel_id": "UC1"}
    assert result.cost_estimate == 0.0
    assert result.meta["quota_units"] == 101


async def test_missing_statistics_are_left_out(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 1)))
    page = videos_page([vid(0)])
    page["items"][0]["statistics"] = {"viewCount": "10"}
    respx_mock.get(VIDEOS).mock(return_value=httpx.Response(200, json=page))
    [item] = evidence(await run("social_search:youtube", quota, query="q"))
    assert item.engagement == {"views": 10}


async def test_search_pages_follow_the_token_and_video_lookups_are_chunked(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    search = respx_mock.get(SEARCH).mock(
        side_effect=[
            httpx.Response(200, json=search_page(0, 50, "TOKEN2")),
            httpx.Response(200, json=search_page(50, 50)),
        ]
    )
    videos = respx_mock.get(VIDEOS).mock(
        side_effect=[
            httpx.Response(200, json=videos_page([vid(i) for i in range(50)])),
            httpx.Response(200, json=videos_page([vid(i) for i in range(50, 100)])),
        ]
    )
    result = await run("social_search:youtube", quota, query="q", max_results=100)
    assert search.call_count == 2
    assert "pageToken" not in search.calls[0].request.url.params
    assert search.calls[1].request.url.params["pageToken"] == "TOKEN2"
    assert videos.call_count == 2
    assert len(evidence(result)) == 100
    assert quota.snapshot() == {"day": "2026-10-01", "units": 202, "searches": 2}
    assert result.next_cursor is None


async def test_next_cursor_is_returned_when_more_pages_exist(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 5, "MORE")))
    respx_mock.get(VIDEOS).mock(
        return_value=httpx.Response(200, json=videos_page([vid(i) for i in range(5)]))
    )
    result = await run("social_search:youtube", quota, query="q", max_results=5)
    assert result.next_cursor == "MORE"


async def test_quota_counter_persists_across_provider_instances(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 1)))
    respx_mock.get(VIDEOS).mock(return_value=httpx.Response(200, json=videos_page([vid(0)])))
    path = tmp_path / "quota.json"
    for _ in range(2):
        fresh = YouTubeQuota(path, 10_000, 100, clock=lambda: NOW)
        await run("social_search:youtube", fresh, query="q", max_results=1)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "day": "2026-10-01",
        "units": 202,
        "searches": 2,
    }
    assert YouTubeQuota(path, 10_000, 100, clock=lambda: NOW).snapshot()["units"] == 202


async def test_search_cap_is_enforced_before_any_request(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    search = respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 1)))
    respx_mock.get(VIDEOS).mock(return_value=httpx.Response(200, json=videos_page([vid(0)])))
    capped = YouTubeQuota(tmp_path / "quota.json", 10_000, 2, clock=lambda: NOW)
    await run("social_search:youtube", capped, query="q", max_results=1)
    await run("social_search:youtube", capped, query="q", max_results=1)
    with pytest.raises(ProviderQuotaExceeded, match="search cap"):
        await run("social_search:youtube", capped, query="q", max_results=1)
    assert search.call_count == 2


async def test_unit_quota_is_enforced_before_any_request(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    search = respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 1)))
    low = YouTubeQuota(tmp_path / "quota.json", 150, 100, clock=lambda: NOW)
    low.reserve(60)
    with pytest.raises(ProviderQuotaExceeded, match="daily quota"):
        await run("social_search:youtube", low, query="q")
    assert search.call_count == 0


async def test_running_out_of_search_quota_midway_returns_what_was_found(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 50, "NEXT")))
    respx_mock.get(VIDEOS).mock(
        return_value=httpx.Response(200, json=videos_page([vid(i) for i in range(50)]))
    )
    one_search = YouTubeQuota(tmp_path / "quota.json", 10_000, 1, clock=lambda: NOW)
    result = await run("social_search:youtube", one_search, query="q", max_results=100)
    assert len(evidence(result)) == 50
    assert result.partial is True
    assert any("daily search cap" in w for w in result.warnings)


async def test_exhausted_unit_quota_for_statistics_keeps_the_videos(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(SEARCH).mock(return_value=httpx.Response(200, json=search_page(0, 2)))
    videos = respx_mock.get(VIDEOS).mock(return_value=httpx.Response(200, json={}))
    tight = YouTubeQuota(tmp_path / "quota.json", 100, 100, clock=lambda: NOW)
    result = await run("social_search:youtube", tight, query="q", max_results=2)
    assert videos.call_count == 0
    assert [i.engagement for i in evidence(result)] == [{}, {}]
    assert result.partial is True
    assert any("statistics skipped" in w for w in result.warnings)


async def test_quota_counters_reset_on_a_new_pacific_day(tmp_path: Path) -> None:
    moments = [datetime(2026, 10, 1, 20, tzinfo=UTC), datetime(2026, 10, 3, 20, tzinfo=UTC)]
    state = {"now": moments[0]}
    daily = YouTubeQuota(tmp_path / "quota.json", 10_000, 100, clock=lambda: state["now"])
    daily.reserve(100, search=True)
    assert daily.snapshot() == {"day": "2026-10-01", "units": 100, "searches": 1}
    state["now"] = moments[1]
    assert daily.snapshot() == {"day": "2026-10-03", "units": 0, "searches": 0}


async def test_youtube_error_bodies_are_mapped(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    def error(reason: str, status: int = 403) -> httpx.Response:
        return httpx.Response(status, json={"error": {"errors": [{"reason": reason}]}})

    route = respx_mock.get(SEARCH)
    route.mock(return_value=error("quotaExceeded"))
    with pytest.raises(ProviderQuotaExceeded):
        await run("social_search:youtube", quota, query="q")
    route.mock(return_value=error("rateLimitExceeded"))
    with pytest.raises(ProviderRateLimited):
        await run("social_search:youtube", quota, query="q")
    route.mock(return_value=error("keyInvalid", 400))
    with pytest.raises(ProviderNotConfigured, match="YOUTUBE_API_KEY"):
        await run("social_search:youtube", quota, query="q")


async def test_comments_request_shape_and_mapping(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    route = respx_mock.get(THREADS).mock(
        return_value=httpx.Response(200, json={"items": [thread(1), thread(2)]})
    )
    result = await run(
        "social_comments:youtube",
        quota,
        post_url="https://www.youtube.com/watch?v=abcDEF12345&t=5s",
        max_results=10,
        sort="top",
    )
    assert dict(route.calls.last.request.url.params) == {
        "part": "snippet",
        "videoId": "abcDEF12345",
        "maxResults": "10",
        "order": "relevance",
        "textFormat": "plainText",
    }
    first, second = evidence(result)
    assert (first.source_type, first.platform) == (SourceType.SOCIAL_COMMENT, Platform.YOUTUBE)
    assert first.text == "تعليق رقم 1: الدعم بطيء"
    assert first.author == "user1"
    assert first.engagement == {"likes": 11, "replies": 1}
    assert first.url == "https://www.youtube.com/watch?v=abcDEF12345&lc=comment1"
    assert first.language == "ar"
    assert first.metadata == {"video_id": "abcDEF12345", "comment_id": "comment1"}
    assert second.engagement == {"likes": 12, "replies": 2}
    assert result.meta["quota_units"] == 1
    assert quota.snapshot()["units"] == 1


async def test_comment_pages_follow_the_token(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    route = respx_mock.get(THREADS).mock(
        side_effect=[
            httpx.Response(
                200, json={"items": [thread(i) for i in range(3)], "nextPageToken": "P2"}
            ),
            httpx.Response(200, json={"items": [thread(i) for i in range(3, 5)]}),
        ]
    )
    result = await run("social_comments:youtube", quota, post_url="abcDEF12345", max_results=5)
    assert route.call_count == 2
    assert route.calls[1].request.url.params["pageToken"] == "P2"
    assert route.calls[0].request.url.params["maxResults"] == "5"
    assert len(evidence(result)) == 5


async def test_comments_outside_the_date_window_are_dropped(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    respx_mock.get(THREADS).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    thread(1, "2026-08-31T23:59:59Z"),
                    thread(2, "2026-09-01T00:00:00Z"),
                    thread(3, "2026-09-30T23:59:59Z"),
                    thread(4, "2026-10-01T00:00:00Z"),
                ]
            },
        )
    )
    result = await run(
        "social_comments:youtube",
        quota,
        post_url="abcDEF12345",
        since=date(2026, 9, 1),
        until=date(2026, 9, 30),
    )
    assert [i.metadata["comment_id"] for i in evidence(result)] == ["comment2", "comment3"]
    assert result.raw_count == 4


async def test_disabled_comments_are_an_empty_result_with_a_warning(
    respx_mock: respx.MockRouter, quota: YouTubeQuota
) -> None:
    respx_mock.get(THREADS).mock(
        return_value=httpx.Response(
            403, json={"error": {"errors": [{"reason": "commentsDisabled"}]}}
        )
    )
    result = await run("social_comments:youtube", quota, post_url="abcDEF12345")
    assert result.items == []
    assert any("comments are disabled" in w for w in result.warnings)


async def test_comments_need_a_video_url(quota: YouTubeQuota) -> None:
    with pytest.raises(ProviderBadResponse, match="needs post_url"):
        await run("social_comments:youtube", quota)
    with pytest.raises(ProviderBadResponse, match="cannot find a video id"):
        await run("social_comments:youtube", quota, post_url="https://example.test/page")


async def test_unsupported_capability_is_rejected(quota: YouTubeQuota) -> None:
    with pytest.raises(ProviderBadResponse, match="unsupported"):
        await run("social_search:x", quota, query="q")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abcDEF12345",
        "https://www.youtube.com/watch?feature=share&v=abcDEF12345&t=3",
        "https://youtu.be/abcDEF12345?si=zz",
        "https://www.youtube.com/shorts/abcDEF12345",
        "https://www.youtube.com/embed/abcDEF12345",
        "https://www.youtube.com/live/abcDEF12345",
        "abcDEF12345",
    ],
)
def test_video_id_from_url(url: str) -> None:
    assert video_id_from_url(url) == "abcDEF12345"
