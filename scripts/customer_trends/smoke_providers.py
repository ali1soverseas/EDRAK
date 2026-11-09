"""Make one tiny real call per configured provider and report PASS, SKIP or FAIL.

Run from backend/: uv run python ../scripts/customer_trends/smoke_providers.py

Each call asks for "coffee" with a limit of 3. The YouTube search uses 101 of the daily quota
units, the Apify check runs a tweet scraper for 3 items (a fraction of a cent), and SocialCrawl
is checked through its free balance endpoint only. Providers that need a key are skipped without
one. Keys are never printed.
"""

import asyncio
import sys
from typing import Any

from edrak.agents.customer_trends.providers.base import ProviderError
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.providers.socialcrawl import SocialCrawlProvider
from edrak.agents.customer_trends.schemas.common import Budget
from edrak.agents.customer_trends.settings import get_settings

PARAMS: dict[str, Any] = {
    "run_id": "smoke",
    "task_id": "smoke",
    "query": "coffee",
    "max_results": 3,
    "languages": ["en"],
}

# (provider, capability, settings key it needs or None)
CHECKS: list[tuple[str, str, str | None]] = [
    ("serper", "web_search", "serper_api_key"),
    ("serper", "news:google_news", "serper_api_key"),
    ("gdelt", "news:gdelt", None),
    ("youtube_api", "social_search:youtube", "youtube_api_key"),
    ("apify", "social_search:x", "apify_token"),
    ("socialcrawl", "credits", "socialcrawl_api_key"),
]


async def run_check(registry: ProviderRegistry, name: str, capability: str) -> str:
    provider = registry.providers[name]
    if isinstance(provider, SocialCrawlProvider) and capability == "credits":
        return f"{await provider.credits_remaining()} credits left"
    result = await provider.call(capability, PARAMS)
    warnings = f", {len(result.warnings)} warning(s)" if result.warnings else ""
    return f"{len(result.items)} item(s){warnings}"


async def main() -> int:
    settings = get_settings()
    registry = ProviderRegistry.from_config(settings, BudgetTracker(Budget()), CircuitBreaker())
    failed = 0
    try:
        for name, capability, key in CHECKS:
            label = f"{name} {capability}"
            if name not in registry.providers:
                print(f"SKIP {label}: {(key or name).upper()} is not set")
                continue
            try:
                print(f"PASS {label}: {await run_check(registry, name, capability)}")
            except ProviderError as exc:
                failed += 1
                print(f"FAIL {label}: {type(exc).__name__}: {exc}")
    finally:
        await registry.aclose()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
