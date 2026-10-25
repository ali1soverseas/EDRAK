"""Tavily web search and indexed public X/Reddit pages."""

from datetime import UTC, datetime
from typing import Any, Callable

import httpx

from edrak.agents.customer_trends.providers.base import (
    CallParams,
    ProviderBadResponse,
    ProviderResult,
    in_window,
    new_evidence,
)
from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.http import RateLimiter, request_json
from edrak.agents.customer_trends.schemas.common import Platform, SourceType

NAME = "tavily"
BASE_URL = "https://api.tavily.com/search"
SOCIAL_DOMAINS = {
    Platform.X: ["x.com", "twitter.com"],
    Platform.REDDIT: ["reddit.com"],
}
CAPABILITIES = {
    "web_search",
    *(f"social_search:{platform.value}" for platform in SOCIAL_DOMAINS),
}


class TavilyProvider:
    name = NAME
    capabilities = CAPABILITIES

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        config: ProviderConfig,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._config = config
        self._clock = clock
        self._limiter = RateLimiter(config.min_interval_s)

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        call = CallParams.model_validate(params)
        kind, _, variant = capability.partition(":")
        platform = Platform(variant) if kind == "social_search" else None
        if capability not in self.capabilities:
            raise ProviderBadResponse(f"{NAME}: unsupported capability {capability!r}")

        data = await request_json(
            self._client,
            "POST",
            BASE_URL,
            provider=NAME,
            json={
                "api_key": self._api_key,
                "query": call.query,
                "search_depth": "basic",
                "max_results": min(
                    call.wanted, self._config.max_per_request or call.wanted
                ),
                "include_answer": False,
                "include_raw_content": False,
                **({"include_domains": SOCIAL_DOMAINS[platform]} if platform else {}),
            },
            limiter=self._limiter,
            request_timeout=20,
        )
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ProviderBadResponse(f"{NAME}: unexpected response shape")

        now = self._clock()
        items = []
        for entry in data["results"]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            content = str(entry.get("content") or "").strip()
            text = f"{title}. {content}" if title and content else title or content
            url = entry.get("url")
            if not text or (url is not None and not isinstance(url, str)):
                continue
            published_at = _published_at(entry.get("published_date"))
            if not in_window(published_at, call.since, call.until):
                continue
            items.append(
                new_evidence(
                    call,
                    provider=NAME,
                    source_type=SourceType.SOCIAL_POST if platform else SourceType.WEB,
                    platform=platform,
                    text=text,
                    url=url,
                    published_at=published_at,
                    snippet_only=True,
                    metadata={"title": title, "score": entry.get("score")},
                    collected_at=now,
                )
            )

        if not items:
            raise ProviderBadResponse(f"{NAME}: no usable results")
        return ProviderResult(
            items=items[: call.wanted],
            raw_count=len(data["results"]),
            cost_estimate=self._config.cost_per_call_usd,
            warnings=(
                [
                    "Tavily returns indexed snippets, not native social posts or engagement counts."
                ]
                if platform
                else []
            ),
            meta={"search_depth": "basic"},
        )


def _published_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )
