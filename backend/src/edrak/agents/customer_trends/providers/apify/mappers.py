"""Apify dataset items to evidence and trend series.

Every actor-specific field name lives in this file and nowhere else. Mappers are tolerant:
a missing field is None, engagement keys are normalized to likes, replies, shares, views,
upvotes and rating, and fields no mapper reads are kept in `metadata`. A mapper returns None
for an item it cannot use (no text) or that does not belong (a post among comments).
"""

import html
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from edrak.agents.customer_trends.providers.base import (
    PENDING_BATCH,
    CallParams,
    infer_granularity,
    new_evidence,
    parse_relative_date,
)
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries

PROVIDER = "apify"
DEFAULT_TIMEFRAME = "today 12-m"
MAX_RELATED_QUERIES = 20
_MAX_LEFTOVER_CHARS = 300
_SKIPPED_LEFTOVER = ("avatar", "picture", "thumbnail", "cover", "image", "html", "profilepic")
_TWITTER_DATE = "%a %b %d %H:%M:%S %z %Y"


@dataclass(frozen=True)
class MapContext:
    params: CallParams
    now: datetime


Mapper = Callable[[dict[str, Any], MapContext], EvidenceItem | None]
TrendMapper = Callable[[dict[str, Any], MapContext], TrendSeries | None]


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _engagement(**counts: Any) -> dict[str, int]:
    return {name: n for name, value in counts.items() if (n := _count(value)) is not None}


def _iso(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _text(*parts: Any) -> str:
    return "\n".join(str(p).strip() for p in parts if isinstance(p, str) and p.strip())


def _leftover(item: dict[str, Any], used: set[str]) -> dict[str, Any]:
    """Top-level fields no mapper read, minus media links and anything bulky."""
    extra: dict[str, Any] = {}
    for key, value in item.items():
        if key in used or value in (None, "", [], {}):
            continue
        if any(marker in key.lower() for marker in _SKIPPED_LEFTOVER):
            continue
        if len(json.dumps(value, ensure_ascii=False, default=str)) <= _MAX_LEFTOVER_CHARS:
            extra[key] = value
    return extra


def _evidence(
    item: dict[str, Any],
    ctx: MapContext,
    used: set[str],
    *,
    source_type: SourceType,
    platform: Platform | None,
    text: str,
    url: str | None = None,
    key: str | None = None,
    author: Any = None,
    published_at: datetime | None = None,
    engagement: dict[str, int] | None = None,
    language: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EvidenceItem | None:
    if not text:
        return None
    lang = language if isinstance(language, str) and len(language) == 2 else None
    return new_evidence(
        ctx.params,
        provider=PROVIDER,
        source_type=source_type,
        platform=platform,
        text=text,
        url=url if isinstance(url, str) and url else None,
        key=key,
        author=author if isinstance(author, str) and author else None,
        published_at=published_at,
        engagement=engagement,
        language=lang.lower() if lang else None,
        metadata={
            **_leftover(item, used),
            **{k: v for k, v in (metadata or {}).items() if v is not None},
        },
        collected_at=ctx.now,
    )


def _nested(item: dict[str, Any], *path: str) -> Any:
    value: Any = item
    for step in path:
        if not isinstance(value, dict):
            return None
        value = value.get(step)
    return value


# X


def _twitter_time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(value, _TWITTER_DATE).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _x_item(item: dict[str, Any], ctx: MapContext, source_type: SourceType) -> EvidenceItem | None:
    if item.get("type") not in (None, "tweet") or item.get("noResults"):
        return None
    used = {"type", "id", "url", "twitterUrl", "text", "fullText", "likeCount", "replyCount",
            "retweetCount", "viewCount", "createdAt", "lang", "author", "extendedEntities",
            "entities", "card", "place", "media", "videos"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=source_type,
        platform=Platform.X,
        text=_text(item.get("fullText") or item.get("text")),
        url=item.get("url"),
        key=f"x|{item['id']}" if item.get("id") else None,
        author=_nested(item, "author", "userName"),
        published_at=_twitter_time(item.get("createdAt")),
        engagement=_engagement(
            likes=item.get("likeCount"),
            replies=item.get("replyCount"),
            shares=item.get("retweetCount"),
            views=item.get("viewCount"),
        ),
        language=None if item.get("lang") in ("und", "qme", "zxx") else item.get("lang"),
        metadata={
            "author_followers": _nested(item, "author", "followers"),
            "author_verified": _nested(item, "author", "isBlueVerified"),
        },
    )


def x_post(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    return _x_item(item, ctx, SourceType.SOCIAL_POST)


def x_reply(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    return _x_item(item, ctx, SourceType.SOCIAL_COMMENT)


# TikTok


def tiktok_video(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"id", "text", "textLanguage", "createTime", "createTimeISO", "authorMeta",
            "webVideoUrl", "diggCount", "commentCount", "shareCount", "playCount",
            "musicMeta", "locationMeta", "videoMeta", "mediaUrls", "detailedMentions"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_POST,
        platform=Platform.TIKTOK,
        text=_text(item.get("text")),
        url=item.get("webVideoUrl"),
        author=_nested(item, "authorMeta", "name"),
        published_at=_iso(item.get("createTimeISO") or item.get("createTime")),
        engagement=_engagement(
            likes=item.get("diggCount"),
            replies=item.get("commentCount"),
            shares=item.get("shareCount"),
            views=item.get("playCount"),
        ),
        language=item.get("textLanguage"),
        metadata={
            "saves": item.get("collectCount"),
            "author_followers": _nested(item, "authorMeta", "fans"),
            "duration_s": _nested(item, "videoMeta", "duration"),
        },
    )


def tiktok_comment(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"cid", "text", "createTime", "createTimeISO", "diggCount", "replyCommentTotal",
            "uniqueId", "videoWebUrl", "submittedVideoUrl", "input", "uid", "mentions",
            "detailedMentions"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_COMMENT,
        platform=Platform.TIKTOK,
        text=_text(item.get("text")),
        url=item.get("videoWebUrl") or item.get("submittedVideoUrl"),
        key=f"comment|{item['cid']}" if item.get("cid") else None,
        author=item.get("uniqueId"),
        published_at=_iso(item.get("createTimeISO") or item.get("createTime")),
        engagement=_engagement(likes=item.get("diggCount"), replies=item.get("replyCommentTotal")),
        metadata={"reply_to": item.get("repliesToId")},
    )


# Instagram


def instagram_post(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"id", "type", "shortCode", "caption", "url", "commentsCount", "likesCount",
            "videoViewCount", "videoPlayCount", "timestamp", "ownerUsername", "ownerFullName",
            "ownerId", "inputUrl", "displayUrl", "images", "childPosts", "firstComment",
            "latestComments", "dimensionsHeight", "dimensionsWidth", "musicInfo",
            "mentions"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_POST,
        platform=Platform.INSTAGRAM,
        text=_text(item.get("caption")),
        url=item.get("url"),
        author=item.get("ownerUsername"),
        published_at=_iso(item.get("timestamp")),
        engagement=_engagement(
            likes=item.get("likesCount"),
            replies=item.get("commentsCount"),
            views=item.get("videoViewCount") or item.get("videoPlayCount"),
        ),
        metadata={"post_type": item.get("type"), "hashtags": item.get("hashtags")},
    )


def instagram_comment(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"id", "text", "postUrl", "commentUrl", "ownerUsername", "timestamp", "likesCount",
            "repliesCount", "replies", "owner"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_COMMENT,
        platform=Platform.INSTAGRAM,
        text=_text(item.get("text")),
        url=item.get("commentUrl") or item.get("postUrl"),
        key=f"comment|{item['id']}" if item.get("id") else None,
        author=item.get("ownerUsername"),
        published_at=_iso(item.get("timestamp")),
        engagement=_engagement(likes=item.get("likesCount"), replies=item.get("repliesCount")),
        metadata={"post_url": item.get("postUrl")},
    )


# Facebook


def facebook_post(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"postId", "url", "topLevelUrl", "time", "timestamp", "user", "text", "textReferences",
            "likes", "shares", "viewsCount", "videoPostViewCount", "comments", "commentsCount",
            "media", "feedbackId", "facebookId", "pageAdLibrary", "inputUrl", "facebookUrl",
            "collaborators", "reactionLikeCount", "topReactionsCount"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_POST,
        platform=Platform.FACEBOOK,
        text=_text(item.get("text")),
        url=item.get("url") or item.get("topLevelUrl"),
        key=f"facebook|{item['postId']}" if item.get("postId") else None,
        author=_nested(item, "user", "name") or item.get("pageName"),
        published_at=_iso(item.get("time") or item.get("timestamp")),
        engagement=_engagement(
            likes=item.get("likes"),
            replies=item.get("comments") or item.get("commentsCount"),
            shares=item.get("shares"),
            views=item.get("viewsCount") or item.get("videoPostViewCount"),
        ),
        metadata={"page": item.get("pageName"), "link": item.get("link")},
    )


def facebook_comment(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"commentId", "id", "commentUrl", "facebookUrl", "feedbackId", "date", "text",
            "author", "profileUrl", "profileId", "profileName", "likesCount", "commentsCount",
            "threadingDepth", "facebookId", "pageAdLibrary", "inputUrl"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_COMMENT,
        platform=Platform.FACEBOOK,
        text=_text(item.get("text")),
        url=item.get("commentUrl") or item.get("facebookUrl"),
        key=f"comment|{item.get('commentId') or item['id']}"
        if item.get("commentId") or item.get("id")
        else None,
        author=item.get("profileName") or _nested(item, "author", "name"),
        published_at=_iso(item.get("date")),
        engagement=_engagement(likes=item.get("likesCount"), replies=item.get("commentsCount")),
        metadata={"post_url": item.get("facebookUrl"), "depth": item.get("threadingDepth")},
    )


# YouTube


def youtube_video(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    if item.get("type") not in (None, "video", "shorts", "short"):
        return None
    used = {"title", "type", "id", "url", "viewCount", "date", "likes", "commentsCount",
            "channelName", "channelUrl", "channelUsername", "channelId", "text",
            "descriptionLinks", "duration", "numberOfSubscribers", "order", "fromYTUrl", "input",
            "subtitles", "hashtags", "translatedTitle", "translatedText"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_POST,
        platform=Platform.YOUTUBE,
        text=_text(item.get("title"), item.get("text")),
        url=item.get("url"),
        author=item.get("channelName") or item.get("channelUsername"),
        published_at=_iso(item.get("date")),
        engagement=_engagement(
            likes=item.get("likes"),
            replies=item.get("commentsCount"),
            views=item.get("viewCount"),
        ),
        metadata={
            "video_id": item.get("id"),
            "channel_id": item.get("channelId"),
            "duration": item.get("duration"),
        },
    )


def youtube_comment(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"cid", "replyToCid", "type", "publishedTimeText", "comment", "author", "replyCount",
            "voteCount", "videoId", "pageUrl", "commentsCount", "title"}  # fmt: skip
    page = item.get("pageUrl")
    cid = item.get("cid")
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_COMMENT,
        platform=Platform.YOUTUBE,
        text=_text(item.get("comment")),
        url=f"{page}&lc={cid}" if page and cid else page,
        key=f"comment|{cid}" if cid else None,
        author=item.get("author"),
        published_at=parse_relative_date(item.get("publishedTimeText"), ctx.now),
        engagement=_engagement(likes=item.get("voteCount"), replies=item.get("replyCount")),
        metadata={"video_id": item.get("videoId"), "reply_to": item.get("replyToCid")},
    )


# Reddit (one actor returns posts and comments mixed, told apart by `dataType`)


def reddit_post(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    if item.get("dataType") not in (None, "post"):
        return None
    used = {"id", "parsedId", "url", "username", "title", "communityName", "parsedCommunityName",
            "body", "createdAt", "scrapedAt", "dataType", "category", "upVotes",
            "numberOfComments", "numberOfVotes"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_POST,
        platform=Platform.REDDIT,
        text=_text(
            html.unescape(str(item.get("title") or "")), html.unescape(str(item.get("body") or ""))
        ),
        url=item.get("url"),
        key=f"reddit|{item['parsedId'] or item['id']}"
        if item.get("parsedId") or item.get("id")
        else None,
        author=item.get("username"),
        published_at=_iso(item.get("createdAt")),
        engagement=_engagement(
            upvotes=item.get("upVotes") or item.get("numberOfVotes"),
            replies=item.get("numberOfComments"),
        ),
        metadata={"subreddit": item.get("parsedCommunityName")},
    )


def reddit_comment(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    if item.get("dataType") != "comment":
        return None
    used = {"id", "parsedId", "url", "username", "title", "communityName", "parsedCommunityName",
            "body", "createdAt", "scrapedAt", "dataType", "category", "postId",
            "upVotes"}  # fmt: skip
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.SOCIAL_COMMENT,
        platform=Platform.REDDIT,
        text=_text(html.unescape(str(item.get("body") or ""))),
        url=item.get("url"),
        key=f"comment|{item['parsedId'] or item['id']}"
        if item.get("parsedId") or item.get("id")
        else None,
        author=item.get("username"),
        published_at=_iso(item.get("createdAt")),
        engagement=_engagement(upvotes=item.get("upVotes")),
        metadata={"subreddit": item.get("parsedCommunityName"), "post_id": item.get("postId")},
    )


# Store reviews


def _review(
    item: dict[str, Any],
    ctx: MapContext,
    used: set[str],
    *,
    store: str,
    text: str,
    review_id: Any,
    url: Any,
    author: Any,
    published_at: datetime | None,
    rating: Any,
    helpful: Any = None,
    metadata: dict[str, Any] | None = None,
) -> EvidenceItem | None:
    return _evidence(
        item,
        ctx,
        used,
        source_type=SourceType.REVIEW,
        platform=None,
        text=text,
        url=url,
        key=f"review|{store}|{review_id}" if review_id else None,
        author=author,
        published_at=published_at,
        engagement=_engagement(rating=rating, likes=helpful),
        metadata={"store": store, **(metadata or {})},
    )


def app_store_review(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"id", "date", "userName", "userUrl", "score", "title", "text", "url", "inputSource"}
    return _review(
        item, ctx, used, store="app_store",
        text=_text(item.get("title"), item.get("text")),
        review_id=item.get("id"), url=item.get("url"), author=item.get("userName"),
        published_at=_iso(item.get("date")), rating=item.get("score"),
        metadata={
            "app_id": item.get("appId"),
            "version": item.get("version"),
            "country": item.get("country"),
        },
    )  # fmt: skip


def google_play_review(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"id", "userName", "userImage", "date", "score", "scoreText", "url", "title", "text",
            "thumbsUp", "inputSource", "criterias"}  # fmt: skip
    return _review(
        item, ctx, used, store="google_play",
        text=_text(item.get("title"), item.get("text")),
        review_id=item.get("id"), url=item.get("url"), author=item.get("userName"),
        published_at=_iso(item.get("date")), rating=item.get("score"), helpful=item.get("thumbsUp"),
        metadata={
            "app_id": item.get("appId"),
            "version": item.get("version"),
            "country": item.get("country"),
            "developer_reply": item.get("replyText"),
        },
    )  # fmt: skip


def amazon_review(item: dict[str, Any], ctx: MapContext) -> EvidenceItem | None:
    used = {"reviewTitle", "reviewDescription", "ratingScore", "reviewId", "date", "reviewUrl",
            "reviewedIn", "reviewImages", "userProfileLink", "userId", "input", "position",
            "reviewCategoryUrl", "variantAttributes"}  # fmt: skip
    published = _iso(item.get("date"))
    return _review(
        item, ctx, used, store="amazon",
        text=_text(item.get("reviewTitle"), item.get("reviewDescription")),
        review_id=item.get("reviewId"), url=item.get("reviewUrl"), author=None,
        published_at=published, rating=item.get("ratingScore"),
        metadata={
            "asin": item.get("productAsin"),
            "verified_purchase": item.get("isVerified"),
            "variant": item.get("variant"),
            "marketplace": item.get("country"),
        },
    )  # fmt: skip


# Google Trends


def google_trends(item: dict[str, Any], ctx: MapContext) -> TrendSeries | None:
    keyword = item.get("searchTerm") or item.get("inputUrlOrTerm")
    timeline = item.get("interestOverTime_timelineData")
    if not isinstance(keyword, str) or not keyword or not isinstance(timeline, list):
        return None
    points: list[tuple[date, float]] = []
    for entry in timeline:
        moment = _iso(float(entry["time"])) if str(entry.get("time", "")).isdigit() else None
        values = entry.get("value")
        if moment is None or not isinstance(values, list) or not values:
            continue
        points.append((moment.date(), float(values[0])))
    if not points:
        return None
    related: list[str] = []
    for field in ("relatedQueries_top", "relatedQueries_rising"):
        for entry in item.get(field) or []:
            query = entry.get("query") if isinstance(entry, dict) else None
            if isinstance(query, str) and query not in related:
                related.append(query)
    geo = ctx.params.geo
    return TrendSeries(
        keyword=keyword,
        geo=geo,
        timeframe=ctx.params.timeframe or DEFAULT_TIMEFRAME,
        granularity=infer_granularity([day for day, _ in points]),
        points=points,
        normalized=True,
        related_queries=related[:MAX_RELATED_QUERIES],
        source=PROVIDER,
        batch_id=PENDING_BATCH,
    )


MAPPERS: dict[str, Mapper] = {
    "x_post": x_post,
    "x_reply": x_reply,
    "tiktok_video": tiktok_video,
    "tiktok_comment": tiktok_comment,
    "instagram_post": instagram_post,
    "instagram_comment": instagram_comment,
    "facebook_post": facebook_post,
    "facebook_comment": facebook_comment,
    "youtube_video": youtube_video,
    "youtube_comment": youtube_comment,
    "reddit_post": reddit_post,
    "reddit_comment": reddit_comment,
    "app_store_review": app_store_review,
    "google_play_review": google_play_review,
    "amazon_review": amazon_review,
}
TREND_MAPPERS: dict[str, TrendMapper] = {"google_trends": google_trends}
