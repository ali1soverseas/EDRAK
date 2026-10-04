"""GDELT DOC 2.0: news coverage, free and keyless, with about three months of history."""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
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
from edrak.agents.customer_trends.schemas.common import SourceType

NAME = "gdelt"
URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_MAX_RECORDS = 250
DEFAULT_WINDOW_DAYS = 90
MIN_INTERVAL_S = 5.0

CAPABILITIES = {"news:gdelt"}

_SOURCE_LANGUAGES = {
    "ar": "arabic",
    "en": "english",
    "fr": "french",
    "es": "spanish",
    "de": "german",
    "tr": "turkish",
    "ru": "russian",
    "it": "italian",
    "pt": "portuguese",
    "fa": "persian",
    "ur": "urdu",
}
_CODE_BY_LANGUAGE_NAME = {name: code for code, name in _SOURCE_LANGUAGES.items()}
_GDELT_TIME = "%Y%m%d%H%M%S"
_SEEN_DATE = "%Y%m%dT%H%M%SZ"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(_GDELT_TIME)


def _end_of_day(day: Any) -> datetime:
    return datetime.combine(day, time.max, tzinfo=UTC).replace(microsecond=0)


class GdeltProvider:
    name = NAME
    capabilities = CAPABILITIES

    def __init__(
        self,
        client: httpx.AsyncClient,
        config: ProviderConfig,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        limiter: RateLimiter | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._clock = clock
        self._limiter = limiter or RateLimiter(max(config.min_interval_s, MIN_INTERVAL_S))

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        if capability != "news:gdelt":
            raise ProviderBadResponse(f"{NAME}: unsupported capability {capability!r}")
        call = CallParams.model_validate(params)
        now = self._clock()
        warnings: list[str] = []
        window_days = int(self._config.options.get("window_days", DEFAULT_WINDOW_DAYS))
        earliest = now - timedelta(days=window_days)
        start = max(
            earliest, datetime.combine(call.since, time.min, tzinfo=UTC) if call.since else earliest
        )
        end = min(now, _end_of_day(call.until)) if call.until else now
        if call.since and datetime.combine(call.since, time.min, tzinfo=UTC) < earliest:
            warnings.append(
                f"requested window starts before GDELT's coverage of about {window_days} days; "
                f"clamped to {start:%Y-%m-%d}"
            )
        if end <= start:
            warnings.append("requested window is entirely outside GDELT's coverage: no results")
            return ProviderResult(warnings=warnings, meta=_meta(start, end, {}))

        query, language_warnings = _build_query(call)
        warnings.extend(language_warnings)
        cap = self._config.max_per_request or DEFAULT_MAX_RECORDS
        data = await request_json(
            self._client,
            "GET",
            URL,
            provider=NAME,
            params={
                "query": query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": min(call.wanted, cap),
                "sort": "datedesc",
                "startdatetime": _stamp(start),
                "enddatetime": _stamp(end),
            },
            limiter=self._limiter,
            empty_ok=True,
        )
        if not isinstance(data, dict):
            raise ProviderBadResponse(f"{NAME}: unexpected response shape")
        articles = [a for a in data.get("articles") or [] if isinstance(a, dict)]
        items = [item for a in articles if (item := self._to_evidence(a, call, now)) is not None]
        by_day = Counter(
            item.published_at.strftime("%Y-%m-%d") for item in items if item.published_at
        )
        return ProviderResult(
            items=items,
            raw_count=len(articles),
            warnings=warnings,
            meta=_meta(start, end, dict(sorted(by_day.items()))),
        )

    @staticmethod
    def _to_evidence(article: dict[str, Any], call: CallParams, now: datetime) -> Any:
        title = str(article.get("title") or "").strip()
        url = article.get("url")
        if not title:
            return None
        seen = article.get("seendate")
        published = None
        if isinstance(seen, str):
            try:
                published = datetime.strptime(seen, _SEEN_DATE).replace(tzinfo=UTC)
            except ValueError:
                published = None
        language_name = str(article.get("language") or "").lower()
        return new_evidence(
            call,
            provider=NAME,
            source_type=SourceType.NEWS,
            text=title,
            url=url if isinstance(url, str) else None,
            author=article.get("domain"),
            published_at=published,
            snippet_only=True,
            language=_CODE_BY_LANGUAGE_NAME.get(language_name),
            metadata={
                k: article[k] for k in ("domain", "sourcecountry", "language") if article.get(k)
            },
            collected_at=now,
        )


def _build_query(call: CallParams) -> tuple[str, list[str]]:
    mapped = [_SOURCE_LANGUAGES[code] for code in call.languages if code in _SOURCE_LANGUAGES]
    unmapped = [code for code in call.languages if code not in _SOURCE_LANGUAGES]
    warnings = [f"no GDELT source language for {code!r}: not filtered" for code in unmapped]
    terms = call.query.strip()
    if len(mapped) == 1:
        terms = f"{terms} sourcelang:{mapped[0]}"
    elif len(mapped) > 1:
        operators = " OR ".join(f"sourcelang:{name}" for name in mapped)
        terms = f"{terms} ({operators})"
    return terms.strip(), warnings


def _meta(start: datetime, end: datetime, by_day: dict[str, int]) -> dict[str, Any]:
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "volume_by_day": by_day,
        "volume_basis": "returned_articles",
    }
