from edrak.agents.customer_trends.relevance.parents import (
    NO_PARENT,
    PostIndex,
    parents_of,
    ref_keys,
)
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from tests.customer_trends.factories import make_evidence


def post(text: str, url: str, platform: Platform, **meta: object):  # type: ignore[no-untyped-def]  # a test helper
    return make_evidence(
        text, platform=platform, source_type=SourceType.SOCIAL_POST, url=url, metadata=dict(meta)
    )


def comment(text: str, url: str, platform: Platform, **meta: object):  # type: ignore[no-untyped-def]  # a test helper
    return make_evidence(
        text, platform=platform, source_type=SourceType.SOCIAL_COMMENT, url=url, metadata=dict(meta)
    )


POSTS = [
    post(
        "YouTube review of the card",
        "https://www.youtube.com/watch?v=abcDEF12345",
        Platform.YOUTUBE,
        video_id="abcDEF12345",
    ),
    post(
        "Reddit thread about fees",
        "https://www.reddit.com/r/egypt/comments/1ab2cd/fees/",
        Platform.REDDIT,
    ),
    post("Instagram launch post", "https://www.instagram.com/p/CxYz_1/", Platform.INSTAGRAM),
    post(
        "TikTok unboxing", "https://www.tiktok.com/@nile/video/7300000000000000001", Platform.TIKTOK
    ),
    post(
        "Facebook announcement", "https://www.facebook.com/nilepay/posts/998877", Platform.FACEBOOK
    ),
    post("X post", "https://x.com/nilepay/status/555", Platform.X),
]
INDEX = PostIndex(POSTS)


def test_a_youtube_comment_finds_its_video_by_id_and_by_url() -> None:
    by_meta = comment(
        "c",
        "https://www.youtube.com/watch?v=abcDEF12345&lc=Ug1",
        Platform.YOUTUBE,
        video_id="abcDEF12345",
    )
    by_url = comment("c", "https://youtube.com/watch?v=abcDEF12345&lc=Ug2", Platform.YOUTUBE)
    for c in (by_meta, by_url):
        assert INDEX.parent_of(c).text == "YouTube review of the card"
    assert INDEX.parent_of(by_meta).key == INDEX.parent_of(by_url).key


def test_a_reddit_comment_finds_its_post_from_the_prefixed_post_id() -> None:
    c = comment(
        "c",
        "https://reddit.com/r/egypt/comments/1ab2cd/fees/zzz/",
        Platform.REDDIT,
        post_id="t3_1ab2cd",
    )
    assert INDEX.parent_of(c).text == "Reddit thread about fees"
    bare = comment("c", "https://old.reddit.com/user/x", Platform.REDDIT, post_id="1ab2cd")
    assert INDEX.parent_of(bare).text == "Reddit thread about fees"


def test_instagram_and_facebook_comments_use_post_url() -> None:
    ig = comment(
        "c",
        "https://www.instagram.com/p/CxYz_1/c/178/",
        Platform.INSTAGRAM,
        post_url="https://www.instagram.com/p/CxYz_1/",
    )
    fb = comment(
        "c",
        "https://www.facebook.com/nilepay/posts/998877?comment_id=1",
        Platform.FACEBOOK,
        post_url="https://m.facebook.com/nilepay/posts/998877/",
    )
    assert INDEX.parent_of(ig).text == "Instagram launch post"
    assert INDEX.parent_of(fb).text == "Facebook announcement"


def test_a_tiktok_comment_carries_the_video_url_as_its_own() -> None:
    c = comment("c", "https://www.tiktok.com/@nile/video/7300000000000000001", Platform.TIKTOK)
    assert INDEX.parent_of(c).text == "TikTok unboxing"


def test_a_comment_whose_post_was_not_collected_is_grouped_by_its_hint_without_text() -> None:
    a = comment(
        "c1",
        "https://www.instagram.com/p/OTHER/c/1/",
        Platform.INSTAGRAM,
        post_url="https://www.instagram.com/p/OTHER/",
    )
    b = comment(
        "c2",
        "https://www.instagram.com/p/OTHER/c/2/",
        Platform.INSTAGRAM,
        post_url="https://instagram.com/p/OTHER",
    )
    pa, pb = INDEX.parent_of(a), INDEX.parent_of(b)
    assert pa.text == "" and pa.key == pb.key != ""


def test_a_comment_with_no_hint_has_no_parent() -> None:
    reply = comment("c", "https://x.com/someone/status/999", Platform.X)
    assert INDEX.parent_of(reply) == NO_PARENT


def test_parents_of_answers_in_the_order_of_the_comments() -> None:
    comments = [
        comment("a", "https://www.tiktok.com/@nile/video/7300000000000000001", Platform.TIKTOK),
        comment("b", "https://x.com/u/status/1", Platform.X),
    ]
    assert [p.text for p in parents_of(comments, POSTS)] == ["TikTok unboxing", ""]


def test_reference_keys_name_a_post_every_way_it_is_known() -> None:
    assert "yt:abcdef12345" in ref_keys("youtube", "https://youtu.be/abcDEF12345")
    assert ref_keys("youtube", "abcDEF12345") == ["yt:abcdef12345"]
    assert ref_keys("reddit", "t3_AbC") == ["reddit:abc"]
    assert "ig:cxyz_1" in ref_keys("instagram", "instagram.com/reel/CxYz_1/")
    assert ref_keys("x", None) == [] and ref_keys("x", "  ") == []
