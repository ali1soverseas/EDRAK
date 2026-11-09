"""What each provider costs to deliver 100 usable items, from the numbers in providers.yaml.

The routing order in providers.yaml is meant to follow this table, cheapest first among results
of the same quality. `print_costs.py` shows it and a test keeps the order honest.
"""

import math

from edrak.agents.customer_trends.providers import gdelt, serper, youtube
from edrak.agents.customer_trends.providers.apify.provider import ActorSpec
from edrak.agents.customer_trends.providers.config import ProvidersConfig
from edrak.agents.customer_trends.providers.socialcrawl import EndpointSpec

ITEMS = 100
FREE_PROVIDERS = {"direct_http"}


def cost_per_100(name: str, capability: str, config: ProvidersConfig) -> float | None:
    """USD for 100 usable items (search interest: 100 keywords), or None if not offered.

    Apify: the run fee plus the per-item price. SocialCrawl: pages needed, given how many rows
    of a page survive its relevance and language filters, times credits per page times the
    price of a credit. Serper: calls of up to 100 results. YouTube's API, GDELT and the page
    fetcher have no price (YouTube has a daily quota instead).
    """
    provider = config.providers.get(name)
    if provider is None:
        return None
    if name == "apify":
        raw = provider.options.get("actors", {}).get(capability)
        spec = ActorSpec.model_validate(raw) if raw else None
        if spec is None or not spec.enabled or not spec.actor:
            return None
        return spec.cost_per_run_usd + ITEMS * spec.cost_per_item_usd
    if name == "socialcrawl":
        raw = provider.options.get("endpoints", {}).get(capability)
        if raw is None:
            return None
        endpoint = EndpointSpec.model_validate(raw)
        pages = math.ceil(ITEMS / (endpoint.page_size * endpoint.expected_yield))
        return pages * endpoint.credits_per_page * float(provider.options.get("usd_per_credit", 0))
    if name == "serper":
        if capability not in serper.CAPABILITIES:
            return None
        per_call = provider.max_per_request or serper.DEFAULT_PER_REQUEST_CAP
        return math.ceil(ITEMS / per_call) * provider.cost_per_call_usd
    if name == "youtube_api":
        return 0.0 if capability in youtube.CAPABILITIES else None
    if name == "gdelt":
        return 0.0 if capability in gdelt.CAPABILITIES else None
    if name in FREE_PROVIDERS:
        return 0.0
    return None


def cost_table(config: ProvidersConfig) -> dict[str, list[tuple[str, float | None]]]:
    """Per capability, the routed providers in configured order with their cost per 100 items."""
    return {
        capability: [(name, cost_per_100(name, capability, config)) for name in names]
        for capability, names in sorted(config.routing.items())
    }
