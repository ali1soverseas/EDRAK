"""Google Trends API (alpha). A stub until access is approved: no endpoints are guessed."""

from typing import Any

from edrak.agents.customer_trends.providers.base import (
    ProviderNotConfigured,
    ProviderResult,
    ProviderUnavailable,
)

NAME = "google_trends_api"
KEY_NAME = "GOOGLE_TRENDS_API_KEY"


class GoogleTrendsApiProvider:
    name = NAME
    capabilities = {"search_interest"}

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        if not self._api_key:
            raise ProviderNotConfigured(f"{NAME}: {KEY_NAME} is not set (alpha access needed)")
        raise ProviderUnavailable(f"{NAME}: alpha api not implemented")
