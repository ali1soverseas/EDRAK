"""Web search for the third-party step, through the Serper provider the customer trends worker already uses."""

from __future__ import annotations

import uuid

from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.http import make_client
from edrak.agents.customer_trends.providers.serper import SerperProvider
from edrak.company_research.agent.state import SearchHit
from edrak.core.llm import provider_key

SERPER_INTERVAL_S = 0.25


class SearchUnavailable(Exception):
    """Search cannot run, for example because no key is set."""


async def serper_search(query: str, num: int) -> list[SearchHit]:
    """The top `num` web results for `query`. Raises SearchUnavailable without a key; provider errors propagate."""
    key, variable = provider_key("SERPER_API_KEY", "customer_trends")
    if not key:
        raise SearchUnavailable(f"{variable} is not set.")
    config = ProviderConfig(min_interval_s=SERPER_INTERVAL_S, max_per_request=100)
    async with make_client(30.0) as client:
        provider = SerperProvider(client, key, config)
        result = await provider.call(
            "web_search", {"run_id": "company-research", "task_id": uuid.uuid4().hex, "query": query, "max_results": num}
        )
    return [SearchHit(url=item.url, title=item.metadata.get("title", "") if item.metadata else "") for item in result.items if item.url]
