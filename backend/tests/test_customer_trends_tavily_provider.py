import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderExhausted,
    ProviderUnavailable,
)
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.config import (
    ProviderConfig,
    ProvidersConfig,
    load_providers_config,
)
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.providers.tavily import TavilyProvider
from edrak.agents.customer_trends.schemas.common import Budget, Platform, SourceType


@pytest.mark.parametrize(
    ("capability", "expected_platform", "expected_domains"),
    [
        ("web_search", None, None),
        ("social_search:x", Platform.X, ["x.com", "twitter.com"]),
        ("social_search:reddit", Platform.REDDIT, ["reddit.com"]),
    ],
)
def test_tavily_search_returns_provenance_and_platform(
    capability, expected_platform, expected_domains
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "NVIDIA rack-scale inference discussion",
                        "url": "https://reddit.com/r/ai/comments/example",
                        "content": "Developers discuss inference deployment.",
                        "published_date": "2026-10-23T10:00:00Z",
                        "score": 0.92,
                    }
                ]
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = TavilyProvider(
                client,
                "test-key",
                ProviderConfig(max_per_request=20),
                clock=lambda: datetime(2026, 10, 24, tzinfo=UTC),
            )
            return await provider.call(
                capability,
                {
                    "run_id": "run",
                    "task_id": "task",
                    "query": "NVIDIA inference",
                    "max_results": 5,
                    "since": "2026-10-20",
                    "until": "2026-10-24",
                },
            )

    result = asyncio.run(run())
    request_body = json.loads(requests[0].content)
    assert request_body["api_key"] == "test-key"
    assert request_body["query"] == "NVIDIA inference"
    assert request_body["max_results"] == 5
    assert request_body.get("include_domains") == expected_domains
    assert result.items[0].provider == "tavily"
    assert result.items[0].platform == expected_platform
    assert result.items[0].source_type == (
        SourceType.SOCIAL_POST if expected_platform else SourceType.WEB
    )
    assert result.items[0].snippet_only is True
    if expected_platform:
        assert result.warnings == [
            "Tavily returns indexed snippets, not native social posts or engagement counts."
        ]
    assert result.items[0].published_at == datetime(2026, 10, 23, 10, tzinfo=UTC)


def test_tavily_returns_site_scoped_search_capabilities_only() -> None:
    assert TavilyProvider.capabilities == {
        "web_search",
        "social_search:x",
        "social_search:reddit",
    }


def test_tavily_empty_results_are_a_successful_empty_result() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = TavilyProvider(client, "test-key", ProviderConfig())
            return await provider.call(
                "web_search",
                {"run_id": "run", "task_id": "task", "query": "no matching pages"},
            )

    result = asyncio.run(run())
    assert result.items == []
    assert result.raw_count == 0


def test_provider_routing_uses_tavily_as_last_resort() -> None:
    routing = load_providers_config().routing
    assert routing["social_search:x"][-1] == "tavily"
    assert routing["social_search:reddit"][-1] == "tavily"
    assert routing["web_search"][-1] == "tavily"
    assert "tavily" not in routing["search_interest"]
    assert "apify" not in routing["search_interest"]
    assert routing["search_interest"] == ["socialcrawl", "google_trends_api"]


def test_registry_uses_configured_tavily_key_for_web_search(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY_CUSTOMER_TRENDS", raising=False)
    monkeypatch.delenv("SERPER_API_KEY_CUSTOMER_TRENDS_BACKUP", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY_CUSTOMER_TRENDS", "test-key")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "NVIDIA inference",
                        "url": "https://example.com/inference",
                        "content": "Public web result.",
                    }
                ]
            },
        )

    settings = SimpleNamespace(
        edrak_provider_mode="live",
        repo_root=Path(tmp_path),
        data_dir=Path(tmp_path),
        socialcrawl_base_url="https://example.com",
        google_trends_api_key=None,
        youtube_api_key=None,
        key_list=lambda *_args: [],
        has_key=lambda _name: False,
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            registry = ProviderRegistry.from_config(
                settings,
                BudgetTracker(Budget()),
                CircuitBreaker(),
                config=load_providers_config(),
                client=client,
            )
            return await registry.call(
                "web_search",
                {"run_id": "run", "task_id": "task", "query": "NVIDIA inference"},
            )

    result = asyncio.run(run())
    assert json.loads(requests[0].content)["api_key"] == "test-key"
    assert result.provider == "tavily"
    assert result.fallback_used is True


@pytest.mark.parametrize(
    ("error", "expected_open"),
    [
        (ProviderBadResponse("query was rejected"), False),
        (ProviderUnavailable("provider timed out"), True),
    ],
)
def test_registry_only_opens_breaker_for_provider_health_failures(
    error, expected_open
) -> None:
    class FailingProvider:
        name = "test"
        capabilities: ClassVar[set[str]] = {"web_search"}

        async def call(self, _capability, _params):
            raise error

    config = ProvidersConfig(
        providers={"test": ProviderConfig()},
        routing={"web_search": ["test"]},
    )
    breaker = CircuitBreaker(threshold=1)
    registry = ProviderRegistry(
        config,
        budget=BudgetTracker(Budget()),
        breaker=breaker,
        providers=[FailingProvider()],
    )

    with pytest.raises(ProviderExhausted, match=str(error)):
        asyncio.run(
            registry.call(
                "web_search",
                {"run_id": "run", "task_id": "task", "query": "example"},
            )
        )

    assert breaker.is_open("test") is expected_open
