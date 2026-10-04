"""Serper (Google results API): web search, Google News and the snippet-level social fallback."""

import math
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from edrak.agents.customer_trends.providers.base import (
    CallParams,
    ProviderBadResponse,
    ProviderResult,
    new_evidence,
)
from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.http import RateLimiter, request_json
from edrak.agents.customer_trends.schemas.common import Platform, SourceType

NAME = "serper"
BASE_URL = "https://google.serper.dev"
DEFAULT_PER_REQUEST_CAP = 100
MAX_PAGES = 10

SOCIAL_DOMAINS: dict[Platform, tuple[str, ...]] = {
    Platform.X: ("x.com", "twitter.com"),
    Platform.REDDIT: ("reddit.com",),
    Platform.TIKTOK: ("tiktok.com",),
    Platform.INSTAGRAM: ("instagram.com",),
    Platform.FACEBOOK: ("facebook.com",),
}

CAPABILITIES = {
    "web_search",
    "news:google_news",
    *(f"social_search:{platform.value}" for platform in SOCIAL_DOMAINS),
}

_RELATIVE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE)
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}
_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y")


def parse_serper_date(value: str | None, now: datetime) -> datetime | None:
    """Serper dates are free text: 'Sep 3, 2026', '2026-09-03' or '3 days ago'."""
    if not value:
        return None
    relative = _RELATIVE.search(value)
    if relative:
        days = int(relative.group(1)) * _UNIT_DAYS[relative.group(2).lower()]
        return now - timedelta(days=days)
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _google_date(day: date) -> str:
    return f"{day.month}/{day.day}/{day.year}"


def _date_filter(params: CallParams) -> str | None:
    parts = []
    if params.since:
        parts.append(f"cd_min:{_google_date(params.since)}")
    if params.until:
        parts.append(f"cd_max:{_google_date(params.until)}")
    return f"cdr:1,{','.join(parts)}" if parts else None


def social_query(platform: Platform, params: CallParams) -> str:
    domains = " OR ".join(f"site:{domain}" for domain in SOCIAL_DOMAINS[platform])
    scope = f"({domains})" if len(SOCIAL_DOMAINS[platform]) > 1 else domains
    terms = " ".join([params.query, *params.hashtags]).strip()
    return f"{scope} {terms}".strip()


class SerperProvider:
    name = NAME
    capabilities = CAPABILITIES

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        config: ProviderConfig,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        limiter: RateLimiter | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._config = config
        self._clock = clock
        self._limiter = limiter or RateLimiter(config.min_interval_s)

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        call = CallParams.model_validate(params)
        now = self._clock()
        if capability == "web_search":
            return await self._run("search", "organic", call, call.query, SourceType.WEB, None, now)
        if capability == "news:google_news":
            return await self._run("news", "news", call, call.query, SourceType.NEWS, None, now)
        kind, _, variant = capability.partition(":")
        if kind == "social_search" and variant in {p.value for p in SOCIAL_DOMAINS}:
            platform = Platform(variant)
            result = await self._run(
                "search",
                "organic",
                call,
                social_query(platform, call),
                SourceType.SOCIAL_POST,
                platform,
                now,
            )
            result.warnings.append(
                f"{capability} served from a Google site: search: snippet-level data only, "
                "no engagement counts"
            )
            return result
        raise ProviderBadResponse(f"{NAME}: unsupported capability {capability!r}")

    async def _run(
        self,
        endpoint: str,
        key: str,
        call: CallParams,
        query: str,
        source_type: SourceType,
        platform: Platform | None,
        now: datetime,
    ) -> ProviderResult:
        wanted = call.wanted
        cap = self._config.max_per_request or DEFAULT_PER_REQUEST_CAP
        per_page = min(wanted, cap)
        body: dict[str, Any] = {"q": query, "num": per_page}
        if call.geo:
            body["gl"] = call.geo.lower()
        if call.languages:
            body["hl"] = call.languages[0]
        if tbs := _date_filter(call):
            body["tbs"] = tbs
        entries: list[dict[str, Any]] = []
        requests = 0
        for page in range(1, min(math.ceil(wanted / per_page), MAX_PAGES) + 1):
            data = await request_json(
                self._client,
                "POST",
                f"{BASE_URL}/{endpoint}",
                provider=NAME,
                json={**body, "page": page},
                headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
                limiter=self._limiter,
            )
            requests += 1
            if not isinstance(data, dict):
                raise ProviderBadResponse(f"{NAME}: unexpected response shape")
            page_entries = data.get(key) or []
            entries.extend(e for e in page_entries if isinstance(e, dict))
            if len(page_entries) < per_page:
                break
        items = [
            item
            for entry in entries[:wanted]
            if (item := _to_evidence(entry, call, source_type, platform, now)) is not None
        ]
        return ProviderResult(
            items=items,
            raw_count=len(entries),
            cost_estimate=requests * self._config.cost_per_call_usd,
            meta={"requests": requests},
        )


def _to_evidence(
    entry: dict[str, Any],
    call: CallParams,
    source_type: SourceType,
    platform: Platform | None,
    now: datetime,
) -> Any:
    link = entry.get("link")
    title = str(entry.get("title") or "").strip()
    snippet = str(entry.get("snippet") or "").strip()
    text = f"{title}. {snippet}" if title and snippet else title or snippet
    if not text:
        return None
    metadata = {
        k: entry[k] for k in ("title", "position", "source", "date") if entry.get(k) is not None
    }
    return new_evidence(
        call,
        provider=NAME,
        source_type=source_type,
        platform=platform,
        text=text,
        url=link if isinstance(link, str) else None,
        published_at=parse_serper_date(entry.get("date"), now),
        snippet_only=True,
        metadata=metadata,
        collected_at=now,
    )
