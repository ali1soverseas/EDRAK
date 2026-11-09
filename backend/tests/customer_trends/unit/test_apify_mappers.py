import json
from datetime import UTC, date, datetime
from typing import Any

import pytest

from edrak.agents.customer_trends.providers.apify.mappers import (
    MAPPERS,
    MapContext,
    google_trends,
)
from edrak.agents.customer_trends.providers.base import CallParams
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from tests.customer_trends.factories import NOW, call_params, fixture_json

CTX = MapContext(CallParams.model_validate(call_params(geo="US", timeframe="today 3-m")), NOW)
ARABIC = "الدعم الفني بطيء جدا والأسعار مرتفعة لكن الميزات ممتازة"

# fixture file, mapper, platform, source type, expected engagement of the first record
CASES = [
    (
        "x_post",
        "x_post",
        Platform.X,
        SourceType.SOCIAL_POST,
        {"likes": 10, "replies": 1, "shares": 1, "views": 700},
    ),
    (
        "x_reply",
        "x_reply",
        Platform.X,
        SourceType.SOCIAL_COMMENT,
        {"likes": 10, "replies": 1, "shares": 1, "views": 700},
    ),
    (
        "tiktok_video",
        "tiktok_video",
        Platform.TIKTOK,
        SourceType.SOCIAL_POST,
        {"likes": 100, "replies": 5, "shares": 3, "views": 5000},
    ),
    (
        "tiktok_comment",
        "tiktok_comment",
        Platform.TIKTOK,
        SourceType.SOCIAL_COMMENT,
        {"likes": 1, "replies": 0},
    ),
    (
        "instagram_post",
        "instagram_post",
        Platform.INSTAGRAM,
        SourceType.SOCIAL_POST,
        {"likes": 40, "replies": 4},
    ),
    (
        "instagram_comment",
        "instagram_comment",
        Platform.INSTAGRAM,
        SourceType.SOCIAL_COMMENT,
        {"likes": 1, "replies": 1},
    ),
    (
        "facebook_post",
        "facebook_post",
        Platform.FACEBOOK,
        SourceType.SOCIAL_POST,
        {"likes": 7, "shares": 1, "views": 300},
    ),
    (
        "facebook_comment",
        "facebook_comment",
        Platform.FACEBOOK,
        SourceType.SOCIAL_COMMENT,
        {"likes": 1, "replies": 0},
    ),
    (
        "youtube_video",
        "youtube_video",
        Platform.YOUTUBE,
        SourceType.SOCIAL_POST,
        {"likes": 90, "replies": 12, "views": 4000},
    ),
    (
        "youtube_comment",
        "youtube_comment",
        Platform.YOUTUBE,
        SourceType.SOCIAL_COMMENT,
        {"likes": 3, "replies": 1},
    ),
    ("app_store_review", "app_store_review", None, SourceType.REVIEW, {"rating": 5}),
    (
        "google_play_review",
        "google_play_review",
        None,
        SourceType.REVIEW,
        {"rating": 5, "likes": 3},
    ),
    ("amazon_review", "amazon_review", None, SourceType.REVIEW, {"rating": 4}),
]


def run_mapper(name: str, records: list[dict[str, Any]]) -> list[EvidenceItem | None]:
    return [MAPPERS[name](record, CTX) for record in records]


@pytest.mark.parametrize(("fixture", "mapper", "platform", "source_type", "engagement"), CASES)
def test_mapper_maps_the_full_record(
    fixture: str,
    mapper: str,
    platform: Platform | None,
    source_type: SourceType,
    engagement: dict[str, int],
) -> None:
    first = run_mapper(mapper, fixture_json("providers", "apify", f"{fixture}.json"))[0]
    assert first is not None
    assert (first.platform, first.source_type, first.provider) == (platform, source_type, "apify")
    assert first.engagement == engagement
    assert first.text and first.content_hash
    assert first.published_at is not None and first.published_at.tzinfo is not None
    assert first.collected_at == NOW
    assert first.batch_id == "pending"
    assert first.snippet_only is False


@pytest.mark.parametrize(("fixture", "mapper"), [(c[0], c[1]) for c in CASES])
def test_mapper_keeps_arabic_text_exactly_and_ids_are_distinct(fixture: str, mapper: str) -> None:
    records = fixture_json("providers", "apify", f"{fixture}.json")
    items = [i for i in run_mapper(mapper, records) if i]
    assert len(items) == 3
    assert len({item.id for item in items}) == 3
    arabic = next(i for i in items if any("\u0600" <= ch <= "\u06ff" for ch in i.text))
    raw = json.dumps(records, ensure_ascii=False)
    assert all(line in raw for line in arabic.text.split("\n"))
    assert arabic.language in ("ar", None)


@pytest.mark.parametrize(("fixture", "mapper"), [(c[0], c[1]) for c in CASES])
def test_mapper_tolerates_a_record_with_missing_fields(fixture: str, mapper: str) -> None:
    minimal = run_mapper(mapper, fixture_json("providers", "apify", f"{fixture}.json"))[2]
    if minimal is None:
        pytest.skip("record without text is skipped by design")
    assert minimal.text
    assert isinstance(minimal.engagement, dict)
    assert all(isinstance(v, int) and v >= 0 for v in minimal.engagement.values())


def test_missing_values_become_none_not_errors() -> None:
    [minimal] = [run_mapper("x_post", fixture_json("providers", "apify", "x_post.json"))[2]]
    assert minimal is not None
    assert minimal.published_at is None
    assert minimal.author is None
    assert minimal.language is None
    assert "likes" not in minimal.engagement and "views" not in minimal.engagement


def test_x_post_details() -> None:
    first, arabic, _ = run_mapper("x_post", fixture_json("providers", "apify", "x_post.json"))
    assert first is not None and arabic is not None
    assert first.url == "https://x.com/dev_1/status/210000000000000001"
    assert first.author == "dev_1"
    assert first.published_at == datetime(2026, 10, 1, 19, 16, 36, tzinfo=UTC)
    assert first.language == "en"
    assert first.metadata["author_followers"] == 100
    assert "lang" not in first.metadata and "likeCount" not in first.metadata
    assert arabic.text == ARABIC and arabic.language == "ar"


def test_x_ids_match_across_the_post_and_a_re_collection() -> None:
    records = fixture_json("providers", "apify", "x_post.json")
    first_run = run_mapper("x_post", records)[0]
    again = run_mapper("x_post", records)[0]
    assert first_run is not None and again is not None and first_run.id == again.id


def test_instagram_hidden_likes_are_not_counted() -> None:
    items = run_mapper("instagram_post", fixture_json("providers", "apify", "instagram_post.json"))
    assert items[2] is not None and "likes" not in items[2].engagement
    assert items[0] is not None and items[0].metadata["hashtags"] == ["gitlab", "devops"]


def test_youtube_comment_dates_and_urls() -> None:
    first = run_mapper(
        "youtube_comment", fixture_json("providers", "apify", "youtube_comment.json")
    )[0]
    assert first is not None
    assert first.url == "https://www.youtube.com/watch?v=vid1abcdefg&lc=UgxComment1AaABAg"
    assert first.published_at is not None and (NOW - first.published_at).days == 30


def test_comments_get_stable_ids_from_the_native_comment_id() -> None:
    first = run_mapper("tiktok_comment", fixture_json("providers", "apify", "tiktok_comment.json"))[
        0
    ]
    assert first is not None
    from edrak.agents.customer_trends.utils.text import evidence_id

    cid = fixture_json("providers", "apify", "tiktok_comment.json")[0]["cid"]
    assert first.id == evidence_id(f"tiktok|comment|{cid}")


def test_reddit_posts_and_comments_are_told_apart_and_html_is_unescaped() -> None:
    records = fixture_json("providers", "apify", "reddit.json")
    posts = [i for i in run_mapper("reddit_post", records) if i]
    comments = [i for i in run_mapper("reddit_comment", records) if i]
    assert (len(posts), len(comments)) == (3, 3)
    assert {i.source_type for i in posts} == {SourceType.SOCIAL_POST}
    assert {i.source_type for i in comments} == {SourceType.SOCIAL_COMMENT}
    assert posts[0].text == "Duo vs Copilot cost\nIt's about 5x the cost & the setup is rough."
    assert posts[0].metadata["subreddit"] == "devops"
    assert posts[0].engagement == {}
    assert posts[2].text == "Short post"
    assert comments[0].text == "I ran a similar test and it’s 5x the cost."
    assert comments[1].text == ARABIC


def test_review_mappers_use_rating_and_the_store_in_metadata() -> None:
    app = run_mapper(
        "app_store_review", fixture_json("providers", "apify", "app_store_review.json")
    )
    play = run_mapper(
        "google_play_review", fixture_json("providers", "apify", "google_play_review.json")
    )
    amazon = run_mapper("amazon_review", fixture_json("providers", "apify", "amazon_review.json"))
    assert (
        app[0] is not None and app[0].text == "Great app\nWorks well offline, love the playlists."
    )
    assert app[0].metadata["store"] == "app_store" and app[0].platform is None
    assert play[0] is not None and play[0].metadata["developer_reply"] == "Thanks for the feedback!"
    assert amazon[0] is not None and amazon[0].metadata["verified_purchase"] is True
    assert amazon[0].published_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert amazon[1] is not None and amazon[1].text.endswith(ARABIC)
    assert amazon[2] is not None and amazon[2].engagement == {"rating": 1}


def test_unknown_fields_are_kept_in_metadata_but_bulky_ones_are_not() -> None:
    record = {
        "type": "tweet", "id": "1", "url": "https://x.com/a/status/1",
        "fullText": "hello world text",
        "somethingNew": "kept", "profilePicture": "https://img.test/x.jpg", "huge": ["x" * 400],
    }  # fmt: skip
    item = MAPPERS["x_post"](record, CTX)
    assert item is not None
    assert item.metadata["somethingNew"] == "kept"
    assert "profilePicture" not in item.metadata and "huge" not in item.metadata


def test_items_without_text_are_skipped() -> None:
    assert MAPPERS["x_post"]({"type": "tweet", "id": "1", "text": "  "}, CTX) is None
    assert MAPPERS["instagram_post"]({"id": "1", "caption": None}, CTX) is None
    assert MAPPERS["x_post"]({"noResults": True}, CTX) is None


def test_google_trends_maps_each_term_to_a_series() -> None:
    weekly, daily, monthly = (
        google_trends(r, CTX) for r in fixture_json("providers", "apify", "google_trends.json")
    )
    assert weekly is not None and daily is not None and monthly is not None
    assert (weekly.keyword, weekly.granularity, weekly.source, weekly.normalized) == (
        "gitlab duo",
        "week",
        "apify",
        True,
    )
    assert weekly.geo == "US" and weekly.timeframe == "today 3-m"
    assert [v for _, v in weekly.points] == [20.0, 35.0, 50.0, 80.0, 100.0, 90.0]
    assert weekly.points[0][0] == date(2026, 7, 6)
    assert weekly.related_queries == [
        "gitlab duo agent platform",
        "gitlab duo pricing",
        "gitlab duo vs copilot",
    ]
    assert daily.keyword == "جيت لاب" and daily.granularity == "day"
    assert monthly.granularity == "month" and monthly.related_queries == []


def test_google_trends_skips_items_without_a_timeline() -> None:
    assert google_trends({"searchTerm": "x"}, CTX) is None
    assert google_trends({"searchTerm": "x", "interestOverTime_timelineData": []}, CTX) is None
    assert (
        google_trends({"interestOverTime_timelineData": [{"time": "1", "value": [1]}]}, CTX) is None
    )
