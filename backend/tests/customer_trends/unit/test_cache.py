from pathlib import Path

from edrak.agents.customer_trends.providers.base import ProviderResult
from edrak.agents.customer_trends.providers.cache import DiskCache, cache_key
from edrak.agents.customer_trends.settings import Settings
from tests.customer_trends.factories import FakeClock, make_evidence

PARAMS = {"run_id": "run-a", "task_id": "t", "query": "قهوة coffee", "max_results": 5}


def result() -> ProviderResult:
    return ProviderResult(items=[make_evidence("نتيجة")], raw_count=1, cost_estimate=0.01)


def test_hit_returns_the_stored_result(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, 100, clock=FakeClock())
    assert cache.get("serper", "web_search", PARAMS) is None
    cache.put("serper", "web_search", PARAMS, result())
    assert cache.get("serper", "web_search", PARAMS) == result()


def test_entries_expire_after_the_ttl(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = DiskCache(tmp_path, 100, clock=clock)
    cache.put("serper", "web_search", PARAMS, result())
    clock.now += 100
    assert cache.get("serper", "web_search", PARAMS) is not None
    clock.now += 0.5
    assert cache.get("serper", "web_search", PARAMS) is None
    assert list(tmp_path.glob("*.json")) == []


def test_key_depends_on_provider_capability_and_params_but_not_run_ids() -> None:
    base = cache_key("serper", "web_search", PARAMS)
    assert base == cache_key("serper", "web_search", {**PARAMS, "run_id": "run-b", "task_id": "u"})
    assert base == cache_key("serper", "web_search", dict(reversed(list(PARAMS.items()))))
    assert base != cache_key("gdelt", "web_search", PARAMS)
    assert base != cache_key("serper", "news:google_news", PARAMS)
    assert base != cache_key("serper", "web_search", {**PARAMS, "max_results": 6})
    assert len(base) == 64


def test_partial_results_and_disabled_ttl_are_not_cached(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, 100)
    cache.put("serper", "web_search", PARAMS, result().model_copy(update={"partial": True}))
    assert cache.get("serper", "web_search", PARAMS) is None
    off = DiskCache(tmp_path, 0)
    off.put("serper", "web_search", PARAMS, result())
    assert off.get("serper", "web_search", PARAMS) is None
    assert list(tmp_path.glob("*.json")) == []


def test_a_corrupt_entry_is_a_miss(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, 100)
    cache.put("serper", "web_search", PARAMS, result())
    [entry] = tmp_path.glob("*.json")
    entry.write_text("{not json", encoding="utf-8")
    assert cache.get("serper", "web_search", PARAMS) is None
    entry.write_text('{"stored_at": 1, "result": {"items": "bad"}}', encoding="utf-8")
    assert cache.get("serper", "web_search", PARAMS) is None


def test_from_settings_uses_data_dir_and_ttl(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, edrak_data_dir=tmp_path, edrak_cache_ttl_s=50)
    cache = DiskCache.from_settings(settings)
    cache.put("serper", "web_search", PARAMS, result())
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1
