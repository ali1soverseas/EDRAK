import pytest

from edrak.agents.customer_trends.providers.base import ProviderNotConfigured, ProviderUnavailable
from edrak.agents.customer_trends.providers.google_trends_api import GoogleTrendsApiProvider
from tests.customer_trends.factories import call_params


async def test_without_a_key_it_is_not_configured() -> None:
    with pytest.raises(ProviderNotConfigured, match="GOOGLE_TRENDS_API_KEY"):
        await GoogleTrendsApiProvider().call("search_interest", call_params())
    with pytest.raises(ProviderNotConfigured):
        await GoogleTrendsApiProvider("").call("search_interest", call_params())


async def test_with_a_key_it_reports_the_alpha_api_is_not_implemented() -> None:
    with pytest.raises(ProviderUnavailable, match="alpha api not implemented"):
        await GoogleTrendsApiProvider("key").call("search_interest", call_params())


def test_it_serves_only_search_interest() -> None:
    provider = GoogleTrendsApiProvider()
    assert provider.name == "google_trends_api"
    assert provider.capabilities == {"search_interest"}
