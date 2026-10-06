"""YouTube Data API v3: video search with statistics, and comment threads.

The daily quota (10,000 units) is tracked locally and persisted, because `search.list` costs
100 units: at most `YOUTUBE_SEARCH_DAILY_CAP` searches a day fit under it.
"""

import json
import os
import re
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from edrak.agents.customer_trends.providers.base import (
    CallParams,
    ProviderBadResponse,
    ProviderError,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderResult,
    in_window,
    new_evidence,
)
from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.http import RateLimiter, request_json
from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem

NAME = "youtube_api"
BASE_URL = "https://www.googleapis.com/youtube/v3"
CAPABILITIES = {"social_search:youtube", "social_comments:youtube"}

SEARCH_PAGE_SIZE = 50
VIDEOS_PER_CALL = 50
DEFAULT_COMMENTS_PER_PAGE = 100
DEFAULT_SEARCH_UNITS = 100
DEFAULT_READ_UNITS = 1

_QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
_CREDENTIAL_REASONS = frozenset(
    {"keyInvalid", "accessNotConfigured", "ipRefererBlocked", "forbidden", "keyExpired"}
)
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_ID_PATTERNS = (
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})"),
)


class CommentsDisabled(ProviderBadResponse):
    """The video has comments turned off."""


def video_id_from_url(value: str) -> str:
    """The 11-character video id from a watch, short, embed or youtu.be URL, or a bare id."""
    candidate = value.strip()
    if _VIDEO_ID.match(candidate):
        return candidate
    for pattern in _URL_ID_PATTERNS:
        match = pattern.search(candidate)
        if match:
            return match.group(1)
    raise ProviderBadResponse(f"{NAME}: cannot find a video id in {candidate[:80]!r}")


def _pacific_day(moment: datetime) -> str:
    try:
        return moment.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except ZoneInfoNotFoundError:
        return moment.astimezone(UTC).date().isoformat()


class YouTubeQuota:
    """Daily units and search-call counters, persisted so restarts do not reset them."""

    def __init__(
        self,
        path: Path,
        daily_units: int,
        search_cap: int,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._path = path
        self._daily_units = daily_units
        self._search_cap = search_cap
        self._clock = clock
        self._lock = threading.Lock()

    def _load(self, day: str) -> dict[str, Any]:
        try:
            state = json.loads(self._path.read_text(encoding="utf-8"))
            if state.get("day") == day:
                return {
                    "day": day,
                    "units": int(state["units"]),
                    "searches": int(state["searches"]),
                }
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return {"day": day, "units": 0, "searches": 0}

    def _save(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temp, self._path)

    def reserve(self, units: int, *, search: bool = False) -> None:
        """Count a call before making it; refuse it if it would cross a daily cap."""
        with self._lock:
            state = self._load(_pacific_day(self._clock()))
            if search and state["searches"] + 1 > self._search_cap:
                raise ProviderQuotaExceeded(
                    f"{NAME}: daily search cap reached ({self._search_cap} searches)"
                )
            if state["units"] + units > self._daily_units:
                raise ProviderQuotaExceeded(
                    f"{NAME}: daily quota reached ({state['units']} of {self._daily_units} units)"
                )
            state["units"] += units
            state["searches"] += int(search)
            self._save(state)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._load(_pacific_day(self._clock()))


def _error_reasons(response: httpx.Response) -> list[str]:
    try:
        errors = response.json()["error"]["errors"]
        return [str(e.get("reason")) for e in errors if isinstance(e, dict)]
    except (ValueError, KeyError, TypeError, AttributeError):
        return []


def map_error(response: httpx.Response) -> ProviderError | None:
    """YouTube reports quota and credential problems inside 400 and 403 bodies."""
    if response.status_code not in (400, 403):
        return None
    reasons = set(_error_reasons(response))
    if reasons & _QUOTA_REASONS:
        return ProviderQuotaExceeded(f"{NAME}: YouTube quota exceeded")
    if reasons & _RATE_REASONS:
        return ProviderRateLimited(f"{NAME}: YouTube rate limit")
    if "commentsDisabled" in reasons:
        return CommentsDisabled(f"{NAME}: comments are disabled for this video")
    if reasons & _CREDENTIAL_REASONS:
        return ProviderNotConfigured(f"{NAME}: API key rejected; check YOUTUBE_API_KEY in .env")
    return None


def _count(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _engagement(**counts: Any) -> dict[str, int]:
    return {name: n for name, value in counts.items() if (n := _count(value)) is not None}


def _language(snippet: dict[str, Any]) -> str | None:
    declared = str(snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "")
    code = declared[:2].lower()
    return code if len(code) == 2 and code.isalpha() else None


class YouTubeProvider:
    name = NAME
    capabilities = CAPABILITIES

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        config: ProviderConfig,
        quota: YouTubeQuota,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._quota = quota
        self._clock = clock
        self._limiter = RateLimiter(config.min_interval_s)
        options = config.options
        self._search_units = int(options.get("search_units", DEFAULT_SEARCH_UNITS))
        self._read_units = int(options.get("read_units", DEFAULT_READ_UNITS))
        self._comments_per_page = int(options.get("comments_per_page", DEFAULT_COMMENTS_PER_PAGE))

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        call = CallParams.model_validate(params)
        if capability == "social_search:youtube":
            return await self._search(call)
        if capability == "social_comments:youtube":
            return await self._comments(call)
        raise ProviderBadResponse(f"{NAME}: unsupported capability {capability!r}")

    async def _get(self, resource: str, query: dict[str, Any]) -> dict[str, Any]:
        data = await request_json(
            self._client,
            "GET",
            f"{BASE_URL}/{resource}",
            provider=NAME,
            params=query,
            headers={"X-Goog-Api-Key": self._api_key},
            limiter=self._limiter,
            error_mapper=map_error,
        )
        if not isinstance(data, dict):
            raise ProviderBadResponse(f"{NAME}: unexpected response shape")
        return data

    async def _search(self, call: CallParams) -> ProviderResult:
        now = self._clock()
        wanted = call.wanted
        per_page = min(wanted, SEARCH_PAGE_SIZE)
        query: dict[str, Any] = {
            "part": "snippet",
            "type": "video",
            "q": " ".join([call.query, *call.hashtags]).strip(),
            "maxResults": per_page,
            "order": "relevance" if call.sort == "top" else "date",
        }
        if call.geo:
            query["regionCode"] = call.geo.upper()
        if call.languages:
            query["relevanceLanguage"] = call.languages[0]
        if call.since:
            query["publishedAfter"] = _rfc3339(call.since)
        if call.until:
            query["publishedBefore"] = _rfc3339(call.until + timedelta(days=1))

        found: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        partial = False
        units = 0
        token = call.cursor
        while len(found) < wanted:
            try:
                self._quota.reserve(self._search_units, search=True)
            except ProviderQuotaExceeded as exc:
                if not found:
                    raise
                warnings.append(f"stopped early: {exc}")
                partial = True
                break
            units += self._search_units
            data = await self._get("search", {**query, **({"pageToken": token} if token else {})})
            for entry in data.get("items") or []:
                video = (entry.get("id") or {}).get("videoId") if isinstance(entry, dict) else None
                if video and video not in found:
                    found[video] = entry.get("snippet") or {}
            token = data.get("nextPageToken")
            if not token:
                break

        details, stats_units, stats_warning = await self._video_details(list(found)[:wanted])
        units += stats_units
        if stats_warning:
            warnings.append(stats_warning)
            partial = True
        items = []
        for video_id in list(found)[:wanted]:
            snippet = {**found[video_id], **(details.get(video_id, {}).get("snippet") or {})}
            statistics = details.get(video_id, {}).get("statistics") or {}
            text = f"{snippet.get('title', '')}\n{snippet.get('description', '')}".strip()
            if not text:
                continue
            items.append(
                new_evidence(
                    call,
                    provider=NAME,
                    source_type=SourceType.SOCIAL_POST,
                    platform=Platform.YOUTUBE,
                    text=text,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    author=snippet.get("channelTitle"),
                    published_at=_parse_time(snippet.get("publishedAt")),
                    engagement=_engagement(
                        views=statistics.get("viewCount"),
                        likes=statistics.get("likeCount"),
                        replies=statistics.get("commentCount"),
                    ),
                    language=_language(snippet),
                    metadata={"video_id": video_id, "channel_id": snippet.get("channelId")},
                    collected_at=now,
                )
            )
        return ProviderResult(
            items=items,
            raw_count=len(found),
            next_cursor=token if len(found) >= wanted else None,
            warnings=warnings,
            partial=partial,
            meta={"quota_units": units},
        )

    async def _video_details(
        self, video_ids: list[str]
    ) -> tuple[dict[str, dict[str, Any]], int, str | None]:
        details: dict[str, dict[str, Any]] = {}
        units = 0
        for start in range(0, len(video_ids), VIDEOS_PER_CALL):
            chunk = video_ids[start : start + VIDEOS_PER_CALL]
            try:
                self._quota.reserve(self._read_units)
            except ProviderQuotaExceeded as exc:
                return details, units, f"video statistics skipped: {exc}"
            units += self._read_units
            data = await self._get(
                "videos",
                {"part": "snippet,statistics", "id": ",".join(chunk), "maxResults": len(chunk)},
            )
            for entry in data.get("items") or []:
                if isinstance(entry, dict) and entry.get("id"):
                    details[str(entry["id"])] = entry
        return details, units, None

    async def _comments(self, call: CallParams) -> ProviderResult:
        if not call.post_url:
            raise ProviderBadResponse(f"{NAME}: social_comments needs post_url")
        video_id = video_id_from_url(call.post_url)
        now = self._clock()
        wanted = call.wanted
        per_page = min(wanted, self._comments_per_page)
        query: dict[str, Any] = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": per_page,
            "order": "relevance" if call.sort == "top" else "time",
            "textFormat": "plainText",
        }
        items: list[EvidenceItem] = []
        raw = 0
        units = 0
        token = call.cursor
        warnings: list[str] = []
        partial = False
        while len(items) < wanted:
            try:
                self._quota.reserve(self._read_units)
            except ProviderQuotaExceeded as exc:
                if not items and raw == 0:
                    raise
                warnings.append(f"stopped early: {exc}")
                partial = True
                break
            units += self._read_units
            try:
                data = await self._get(
                    "commentThreads", {**query, **({"pageToken": token} if token else {})}
                )
            except CommentsDisabled as exc:
                warnings.append(str(exc))
                token = None
                break
            for thread in data.get("items") or []:
                raw += 1
                item = self._comment_item(thread, call, video_id, now)
                if item is not None:
                    items.append(item)
            token = data.get("nextPageToken")
            if not token:
                break
        return ProviderResult(
            items=items[:wanted],
            raw_count=raw,
            next_cursor=token if len(items) >= wanted else None,
            warnings=warnings,
            partial=partial,
            meta={"quota_units": units, "video_id": video_id},
        )

    @staticmethod
    def _comment_item(thread: Any, call: CallParams, video_id: str, now: datetime) -> Any:
        if not isinstance(thread, dict):
            return None
        top = (thread.get("snippet") or {}).get("topLevelComment") or {}
        snippet = top.get("snippet") or {}
        text = str(snippet.get("textDisplay") or snippet.get("textOriginal") or "").strip()
        published = _parse_time(snippet.get("publishedAt"))
        if not text or not in_window(published, call.since, call.until):
            return None
        comment_id = str(top.get("id") or thread.get("id") or "")
        return new_evidence(
            call,
            provider=NAME,
            source_type=SourceType.SOCIAL_COMMENT,
            platform=Platform.YOUTUBE,
            text=text,
            url=f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}",
            author=snippet.get("authorDisplayName"),
            published_at=published,
            engagement=_engagement(
                likes=snippet.get("likeCount"),
                replies=(thread.get("snippet") or {}).get("totalReplyCount"),
            ),
            metadata={"video_id": video_id, "comment_id": comment_id},
            collected_at=now,
        )


def _rfc3339(day: date) -> str:
    return datetime.combine(day, time.min, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
