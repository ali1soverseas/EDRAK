from pathlib import Path

import httpx
import pytest
import respx

from edrak.agents.customer_trends.providers.base import (
    ProviderExhausted,
    ProviderResult,
    parse_capability,
)
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.config import (
    ProvidersConfig,
    load_providers_config,
)
from edrak.agents.customer_trends.providers.registry import (
    ProviderRegistry,
    fixture_filename,
)
from edrak.agents.customer_trends.schemas.common import Budget
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.settings import Settings
from tests.customer_trends.factories import FIXTURES, NOW, call_params

FIXTURE_DIR = FIXTURES / "providers"


def settings(tmp_path: Path, **kwargs: object) -> Settings:
    return Settings(_env_file=None, edrak_data_dir=tmp_path / "data", **kwargs)  # type: ignore[arg-type]  # test overrides


def build(tmp_path: Path, **kwargs: object) -> ProviderRegistry:
    return ProviderRegistry.from_config(
        settings(tmp_path, **kwargs),
        BudgetTracker(Budget()),
        CircuitBreaker(),
        clock=lambda: NOW,
    )


async def test_without_keys_only_keyless_providers_are_registered(tmp_path: Path) -> None:
    registry = build(tmp_path)
    try:
        assert sorted(registry.providers) == ["gdelt", "google_trends_api"]
    finally:
        await registry.aclose()


async def test_keys_register_their_providers(tmp_path: Path) -> None:
    registry = build(tmp_path, serper_api_key="s", youtube_api_key="y")
    try:
        assert sorted(registry.providers) == ["gdelt", "google_trends_api", "serper", "youtube_api"]
        assert "social_search:x" in registry.providers["serper"].capabilities
        assert registry.providers["youtube_api"].capabilities == {
            "social_search:youtube",
            "social_comments:youtube",
        }
    finally:
        await registry.aclose()


async def test_live_calls_route_through_the_registered_provider(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(
            200, json={"organic": [{"title": "T", "link": "https://e.test/1", "snippet": "S"}]}
        )
    )
    registry = build(tmp_path, serper_api_key="s")
    try:
        result = await registry.call("web_search", call_params(query="q", max_results=3))
    finally:
        await registry.aclose()
    assert result.provider == "serper"
    assert result.fallback_used is False
    assert result.cost_estimate == pytest.approx(0.001)
    assert len(result.items) == 1


async def test_a_social_search_without_aggregators_falls_back_to_serper(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(
            200,
            json={"organic": [{"title": "T", "link": "https://x.com/a/status/1", "snippet": "S"}]},
        )
    )
    registry = build(tmp_path, serper_api_key="s")
    try:
        result = await registry.call("social_search:x", call_params(query="copilot"))
    finally:
        await registry.aclose()
    assert result.provider == "serper"
    assert result.fallback_used is True
    assert result.warnings


def test_the_shipped_routing_table_is_valid_and_complete() -> None:
    config = load_providers_config()
    assert isinstance(config, ProvidersConfig)
    expected = {
        *(
            f"social_search:{p}"
            for p in ("x", "tiktok", "instagram", "facebook", "reddit", "youtube")
        ),
        *(
            f"social_comments:{p}"
            for p in ("x", "tiktok", "instagram", "facebook", "reddit", "youtube")
        ),
        "search_interest",
        "reviews:app_store",
        "reviews:google_play",
        "reviews:amazon",
        "news:gdelt",
        "news:google_news",
        "web_search",
        "fetch_page",
    }
    assert set(config.routing) == expected
    for capability, names in config.routing.items():
        parse_capability(capability)
        assert names, capability
    assert config.routing["social_search:youtube"][0] == "youtube_api"
    assert config.routing["news:google_news"] == ["serper"]


def test_only_the_trends_stub_is_flagged_unverified_in_the_config() -> None:
    providers = load_providers_config().providers
    assert [name for name, config in providers.items() if config.verify] == ["google_trends_api"]


async def test_fixture_mode_serves_recorded_payloads_without_opening_a_client(
    tmp_path: Path,
) -> None:
    registry = build(tmp_path, edrak_provider_mode="fixture")
    assert registry.providers == {}
    result = await registry.call(
        "web_search",
        call_params(run_id="run-fx", task_id="task-fx", query="anything", max_results=2),
    )
    assert result.provider == "serper"
    assert result.fallback_used is False
    assert result.cost_estimate == 0.0
    assert len(result.items) == 2
    assert {i.run_id for i in result.items if isinstance(i, EvidenceItem)} == {"run-fx"}


async def test_fixture_mode_uses_the_capability_file_name(tmp_path: Path) -> None:
    registry = build(tmp_path, edrak_provider_mode="fixture")
    news = await registry.call("news:gdelt", call_params(query="q"))
    reddit = await registry.call("social_search:reddit", call_params(query="q"))
    assert news.provider == "gdelt" and news.meta["volume_by_day"]
    assert reddit.provider == "apify"
    assert fixture_filename("news:gdelt") == "news.gdelt.json"


async def test_fixture_mode_counts_on_the_budget(tmp_path: Path) -> None:
    budget = BudgetTracker(Budget())
    registry = ProviderRegistry.from_config(
        settings(tmp_path, edrak_provider_mode="fixture"), budget, CircuitBreaker()
    )
    await registry.call("web_search", call_params(query="q"))
    assert budget.snapshot().tool_calls == 1


async def test_a_missing_fixture_is_exhausted_not_a_crash(tmp_path: Path) -> None:
    registry = ProviderRegistry.from_config(
        settings(tmp_path, edrak_provider_mode="fixture"),
        BudgetTracker(Budget()),
        CircuitBreaker(),
        fixtures_dir=tmp_path / "empty",
    )
    with pytest.raises(ProviderExhausted) as caught:
        await registry.call("web_search", call_params(query="q"))
    assert caught.value.failures[0].provider == "fixture"
    assert "web_search.json" in caught.value.failures[0].message


async def test_a_broken_fixture_is_exhausted_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "web_search.json").write_text('{"items": "bad"}', encoding="utf-8")
    registry = ProviderRegistry.from_config(
        settings(tmp_path, edrak_provider_mode="fixture"),
        BudgetTracker(Budget()),
        CircuitBreaker(),
        fixtures_dir=tmp_path,
    )
    with pytest.raises(ProviderExhausted):
        await registry.call("web_search", call_params(query="q"))


@pytest.mark.parametrize("path", sorted(FIXTURE_DIR.glob("*.json")), ids=lambda p: p.name)
def test_committed_provider_fixtures_are_valid(path: Path) -> None:
    result = ProviderResult.model_validate_json(path.read_text(encoding="utf-8"))
    capability = path.stem.replace(".", ":", 1)
    parse_capability(capability)
    assert result.items
    assert result.provider
