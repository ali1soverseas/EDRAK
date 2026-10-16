"""SocialCrawl: one API and one response shape for the social platforms.

Every endpoint answers with the same envelope: `data.items[]` holding a `post` (search) or a
`comment`, a `computed` block of free judgments, and `pagination.next_cursor`. Which endpoint
serves which capability is configuration (providers.yaml), not code.
"""

import math
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal

import httpx
from pydantic import Field

from edrak.agents.customer_trends.providers.base import (
    PENDING_BATCH,
    CallParams,
    ProviderBadResponse,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderResult,
    in_window,
    infer_granularity,
    new_evidence,
)
from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.http import RateLimiter, request_json
from edrak.agents.customer_trends.providers.keys import KeyRing, with_failover
from edrak.agents.customer_trends.schemas.common import (
    Platform,
    SourceType,
    StrictModel,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries

NAME = "socialcrawl"
DEFAULT_BASE_URL = "https://www.socialcrawl.dev/v1"
DEFAULT_MAX_PAGES = 5
DEFAULT_PREFLIGHT_MIN_CREDITS = 5
MAX_TREND_KEYWORDS = 5
_METADATA_KEYS = ("relevance", "labels")
_MAX_METADATA_CHARS = 600


class SortSpec(StrictModel):
    param: str
    values: dict[str, str]


class EndpointSpec(StrictModel):
    path: str
    input: str = "query"
    kind: Literal["posts", "trends"] = "posts"
    credits_per_page: int = Field(default=1, ge=0)
    page_size: int = Field(default=20, ge=1)
    params: dict[str, str] = Field(default_factory=dict)
    sort: SortSpec | None = None
    date_operators: bool = False
    expected_yield: float = Field(default=1.0, gt=0, le=1)
    timeframes: dict[str, str] = Field(default_factory=dict)


class NoData(ProviderBadResponse):
    """The platform has nothing for this request: a deleted post, or one without comments."""


def map_error(response: httpx.Response) -> ProviderError | None:
    """404 RESOURCE_NOT_FOUND means no data, which is an answer and not a failure."""
    if response.status_code != 404:
        return None
    try:
        kind = response.json()["error"]["type"]
    except (ValueError, KeyError, TypeError):
        return None
    return NoData(f"{NAME}: nothing found") if kind == "RESOURCE_NOT_FOUND" else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if value >= 0 else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _text(*parts: Any) -> str:
    seen: list[str] = []
    for part in parts:
        if isinstance(part, str) and part.strip() and part.strip() not in seen:
            seen.append(part.strip())
    return "\n".join(seen)


class SocialCrawlProvider:
    name = NAME

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        base_url: str,
        config: ProviderConfig,
        *,
        fallback_keys: Sequence[str] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        limiter: RateLimiter | None = None,
    ) -> None:
        self._client = client
        self._keys = KeyRing([api_key, *fallback_keys])
        self._credits_seen: int | None = None
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._clock = clock
        self._limiter = limiter or RateLimiter(config.min_interval_s)
        options = config.options
        self._usd_per_credit = float(options.get("usd_per_credit", 0.0))
        self._max_pages = int(options.get("max_pages", DEFAULT_MAX_PAGES))
        self._preflight_min = int(
            options.get("preflight_min_credits", DEFAULT_PREFLIGHT_MIN_CREDITS)
        )
        self._filter_languages = bool(options.get("filter_languages", False))
        self._endpoints = {
            capability: EndpointSpec.model_validate(spec)
            for capability, spec in options.get("endpoints", {}).items()
        }
        self.capabilities = set(self._endpoints)

    async def _get(
        self, path: str, params: dict[str, Any] | None = None, *, tolerate_no_data: bool = False
    ) -> dict[str, Any]:
        data = await request_json(
            self._client,
            "GET",
            f"{self._base_url}{path}",
            provider=NAME,
            params=params,
            headers={"x-api-key": self._keys.current},
            limiter=self._limiter,
            error_mapper=map_error if tolerate_no_data else None,
        )
        if not isinstance(data, dict) or data.get("success") is False:
            raise ProviderBadResponse(f"{NAME}: unexpected response")
        return data

    async def credits_remaining(self) -> int:
        """The account's credit balance. This call is free."""
        data = await self._get("/credits/balance")
        balance = (data.get("data") or {}).get("balance")
        if not isinstance(balance, int | float):
            raise ProviderBadResponse(f"{NAME}: balance missing from response")
        self._credits_seen = int(balance)
        return self._credits_seen

    def health(self) -> dict[str, Any]:
        return {
            "key_in_use": f"{self._keys.position} of {self._keys.size}",
            "credits_last_seen": self._credits_seen,
        }

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        """Serve a capability, moving to a fallback key if the current one is out of credit."""
        return await with_failover(self._keys, NAME, lambda: self._serve(capability, params))

    async def _serve(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        spec = self._endpoints.get(capability)
        if spec is None:
            raise ProviderBadResponse(f"{NAME}: unsupported capability {capability!r}")
        call = CallParams.model_validate(params)
        if spec.kind == "trends":
            return await self._trends(spec, call)
        platform = Platform(capability.partition(":")[2])
        comments = capability.startswith("social_comments")
        value = call.post_url if spec.input == "url" else _query(call, spec)
        if not value:
            raise ProviderBadResponse(
                f"{NAME}: {capability} needs {'post_url' if spec.input == 'url' else 'query'}"
            )
        pages = min(math.ceil(call.wanted / spec.page_size), self._max_pages)
        estimated = pages * spec.credits_per_page
        if estimated >= self._preflight_min:
            remaining = await self.credits_remaining()
            if remaining < estimated:
                raise ProviderQuotaExceeded(
                    f"{NAME}: {remaining} credits left, about {estimated} needed"
                )
        return await self._collect(spec, call, platform, comments, value)

    async def _trends(self, spec: EndpointSpec, call: CallParams) -> ProviderResult:
        """Interest over time for up to five keywords in one call, on one shared 0 to 100 scale."""
        keywords = call.keywords or ([call.query] if call.query else [])
        if not keywords or len(keywords) > MAX_TREND_KEYWORDS:
            raise ProviderBadResponse(f"{NAME}: search_interest needs 1 to 5 keywords")
        timeframe = spec.timeframes.get(call.timeframe)
        if timeframe is None:
            raise ProviderBadResponse(f"{NAME}: timeframe {call.timeframe!r} is not supported")
        request: dict[str, Any] = {"keywords": ",".join(keywords), "timeframe": timeframe}
        if call.geo:
            request["location"] = call.geo
        if spec.credits_per_page >= self._preflight_min:
            remaining = await self.credits_remaining()
            if remaining < spec.credits_per_page:
                raise ProviderQuotaExceeded(
                    f"{NAME}: {remaining} credits left, {spec.credits_per_page} needed"
                )
        body = await self._get(spec.path, request)
        series = [
            built
            for entry in (body.get("data") or {}).get("series") or []
            if (built := _to_series(entry, call, timeframe)) is not None
        ]
        credits = int(body.get("credits_used") or 0)
        return ProviderResult(
            items=series,
            raw_count=len(series),
            cost_estimate=credits * self._usd_per_credit,
            meta={"credits_used": credits},
        )

    async def _collect(
        self, spec: EndpointSpec, call: CallParams, platform: Platform, comments: bool, value: str
    ) -> ProviderResult:
        now = self._clock()
        request: dict[str, Any] = {spec.input: value, **spec.params}
        if spec.sort and (mapped := spec.sort.values.get(call.sort)):
            request[spec.sort.param] = mapped
        items: list[EvidenceItem] = []
        warnings: list[str] = []
        credits = raw = skipped_language = 0
        cursor = call.cursor
        partial = False
        for _ in range(self._max_pages):
            try:
                body = await self._get(
                    spec.path,
                    {**request, **({"cursor": cursor} if cursor else {})},
                    tolerate_no_data=comments,
                )
            except NoData as exc:
                warnings.append(str(exc))
                cursor = None
                break
            rows = (body.get("data") or {}).get("items") or []
            raw += len(rows)
            credits += int(body.get("credits_used") or 0)
            for row in rows:
                item = self._to_evidence(row, call, platform, comments, now)
                if item is None:
                    continue
                if self._filter_languages and not _language_wanted(item.language, call.languages):
                    skipped_language += 1
                elif in_window(item.published_at, call.since, call.until):
                    items.append(item)
            pagination = body.get("pagination") or {}
            cursor = pagination.get("next_cursor") if pagination.get("has_more") else None
            left = body.get("credits_remaining")
            if isinstance(left, int | float):
                self._credits_seen = int(left)
            if len(items) >= call.wanted or not cursor:
                break
            if isinstance(left, int | float) and left < spec.credits_per_page:
                warnings.append(f"stopped early: {int(left)} credits left")
                partial = True
                break
        if skipped_language:
            warnings.append(
                f"dropped {skipped_language} item(s) outside the requested languages "
                f"{call.languages}"
            )
        return ProviderResult(
            items=items[: call.wanted],
            raw_count=raw,
            cost_estimate=credits * self._usd_per_credit,
            next_cursor=cursor if len(items) >= call.wanted else None,
            warnings=warnings,
            partial=partial,
            meta={"credits_used": credits},
        )

    @staticmethod
    def _to_evidence(
        row: Any, call: CallParams, platform: Platform, comments: bool, now: datetime
    ) -> EvidenceItem | None:
        entry = row.get("comment" if comments else "post") if isinstance(row, dict) else None
        if not isinstance(entry, dict):
            return None
        content = entry.get("content") or {}
        ext = entry.get("ext") or {}
        if comments:
            text = _text(entry.get("text"))
        else:
            text = _text(content.get("text"), ext.get("selftext"), ext.get("description"))
        if not text:
            return None
        engagement = entry.get("engagement") or {}
        counts = {
            "likes": _count(engagement.get("likes")),
            "replies": _count(engagement.get("replies" if comments else "comments")),
            "shares": _count(engagement.get("shares")),
            "views": _count(engagement.get("views")),
        }
        if platform is Platform.REDDIT and not comments:
            counts["upvotes"], counts["likes"] = counts["likes"], None
        computed = row.get("computed") or {}
        metadata: dict[str, Any] = {
            "native_id": entry.get("id"),
            "saves": _count(engagement.get("saves")),
            "subreddit": ext.get("subreddit"),
        }
        for key in _METADATA_KEYS:
            value = computed.get(key)
            if value and len(str(value)) <= _MAX_METADATA_CHARS:
                metadata[key] = value
        author = entry.get("author") or {}
        native_id = entry.get("id")
        return new_evidence(
            call,
            provider=NAME,
            source_type=SourceType.SOCIAL_COMMENT if comments else SourceType.SOCIAL_POST,
            platform=platform,
            text=text,
            url=entry.get("url") if isinstance(entry.get("url"), str) else None,
            key=f"comment|{native_id}" if comments and native_id else None,
            author=author.get("username") or author.get("display_name"),
            published_at=_parse_time(entry.get("published_at")),
            engagement={k: v for k, v in counts.items() if v is not None},
            language=_language(computed.get("language")),
            metadata={k: v for k, v in metadata.items() if v is not None},
            collected_at=now,
        )


def _to_series(entry: Any, call: CallParams, timeframe: str) -> TrendSeries | None:
    keyword = entry.get("keyword") if isinstance(entry, dict) else None
    points: list[tuple[date, float]] = []
    for point in (entry.get("points") if isinstance(entry, dict) else None) or []:
        moment = _parse_time(point.get("datetime")) if isinstance(point, dict) else None
        value = point.get("value") if isinstance(point, dict) else None
        if moment is not None and isinstance(value, int | float) and not isinstance(value, bool):
            points.append((moment.date(), float(value)))
    if not isinstance(keyword, str) or not keyword or not points:
        return None
    return TrendSeries(
        keyword=keyword,
        geo=call.geo,
        timeframe=call.timeframe,
        granularity=infer_granularity([day for day, _ in points]),
        points=points,
        normalized=True,
        source=NAME,
        batch_id=PENDING_BATCH,
    )


def _query(call: CallParams, spec: EndpointSpec) -> str:
    terms = " ".join([call.query, *call.hashtags]).strip()
    if spec.date_operators and terms:
        if call.since:
            terms += f" since:{call.since.isoformat()}"
        if call.until:
            terms += f" until:{call.until.isoformat()}"
    return terms


def _language(value: Any) -> str | None:
    return value.lower() if isinstance(value, str) and len(value) == 2 else None


def _language_wanted(language: str | None, wanted: list[str]) -> bool:
    return language is None or not wanted or language in wanted
