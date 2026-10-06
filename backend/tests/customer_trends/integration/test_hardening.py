"""Robustness of the worker: size limits, cost accounting, logs without secrets, re-runs, time
limits and cancellation, and provider health."""

import asyncio
import io
import json
import sqlite3
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from evals.customer_trends.checks import run_checks

from edrak.agents.customer_trends.logging import configure_logging
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.runner import arun_worker, run_task, stream_task
from edrak.agents.customer_trends.schemas.common import Budget, Platform
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools import social_search
from edrak.agents.customer_trends.tools.base import MAX_ITEM_CHARS, cap_item_text, invoke_tool
from tests.customer_trends.contract_helpers import research_task
from tests.customer_trends.factories import load_brief, make_evidence
from tests.customer_trends.graph_helpers import (
    PLAN,
    World,
    WorldProvider,
    demand_turns,
    models,
    reviews_turns,
    scenario_settings,
    social_turns,
    world_registry,
)
from tests.customer_trends.tool_helpers import make_context, serves

FAKE_KEYS = {
    "ollama_api_key": "FAKE-OLLAMA-KEY-5f1a",
    "serper_api_key": "FAKE-SERPER-KEY-7c2b",
    "youtube_api_key": "FAKE-YOUTUBE-KEY-9d3c",
    "apify_token": "FAKE-APIFY-TOKEN-1e4d",
    "apify_fallback_tokens": "FAKE-APIFY-BACKUP-2f5e",
    "socialcrawl_api_key": "FAKE-SOCIALCRAWL-KEY-3a6f",
    "socialcrawl_fallback_api_keys": "FAKE-SOCIALCRAWL-BACKUP-4b7a",
}


def fake_key_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings holding recognisable fake keys in every key field, in live mode."""
    return Settings(  # type: ignore[call-arg]  # pydantic-settings fills the other fields
        _env_file=None,
        edrak_data_dir=tmp_path / "data",
        artifacts_path=tmp_path / "artifacts",
        edrak_provider_mode="live",
        **FAKE_KEYS,
        **overrides,
    )


# size limits


def test_a_text_over_the_limit_is_cut_and_reported() -> None:
    from edrak.agents.customer_trends.providers.base import ProviderResult

    long_item = make_evidence("x" * 6000, platform=Platform.REDDIT)
    short_item = make_evidence("short", platform=Platform.REDDIT)
    capped, cut = cap_item_text(ProviderResult(items=[long_item, short_item]))
    assert cut == 1 and capped.warnings == [
        f"1 item(s) were longer than {MAX_ITEM_CHARS} characters and were cut"
    ]
    first, second = capped.items
    assert isinstance(first, EvidenceItem) and isinstance(second, EvidenceItem)
    assert len(first.text) == MAX_ITEM_CHARS == 5000 and first.text == "x" * 5000
    assert first.metadata["text_cut_from"] == 6000
    assert first.content_hash == long_item.content_hash, (
        "duplicates are still found by the whole text"
    )
    assert second == short_item
    same, none = cap_item_text(ProviderResult(items=[short_item]))
    assert none == 0 and same.warnings == []


async def test_a_collection_tool_stores_the_cut_text_and_warns(
    loaded_store: EvidenceStore, tmp_path: Path
) -> None:
    from edrak.agents.customer_trends.providers.base import ProviderResult
    from edrak.agents.customer_trends.schemas.common import SourceType

    huge = make_evidence(
        "page " * 4000, platform=Platform.REDDIT, source_type=SourceType.SOCIAL_POST
    )
    ctx, _ = make_context(
        loaded_store,
        "social_search:reddit",
        serves(ProviderResult(items=[huge], raw_count=1)),
    )
    response = await invoke_tool(
        ctx, social_search.SEARCH_SPEC, {"platform": "reddit", "query": "anything"}
    )
    assert response.count == 1
    assert any("longer than 5000 characters" in w for w in response.warnings)
    [stored] = [i for i in loaded_store.query(ctx.run_id, limit=1000).items if i.id == huge.id]
    assert len(stored.text) == 5000 and stored.metadata["text_cut_from"] == 20000
    again = await invoke_tool(
        ctx, social_search.SEARCH_SPEC, {"platform": "reddit", "query": "anything"}
    )
    assert again.count == 0, "the same page is a duplicate"


# cost accounting


async def test_the_provenance_totals_equal_the_budget_tracker(tmp_path: Path) -> None:
    registry = world_registry()
    result = await run_task(
        load_brief(), providers=registry, llm=models().factory, settings=scenario_settings(tmp_path)
    )
    snapshot = registry.budget.snapshot()
    provenance = result.provenance
    assert provenance["provider_calls"] == snapshot.tool_calls == 7
    assert provenance["cost_usd"] == pytest.approx(snapshot.cost_usd)
    assert provenance["budget"] == {
        "tool_calls": snapshot.tool_calls,
        "cost_usd": pytest.approx(snapshot.cost_usd),
    }
    summary = result.control_summary.budget_used
    assert summary["tool_calls"] == snapshot.tool_calls
    assert summary["cost_usd"] == pytest.approx(snapshot.cost_usd)
    per_tool = provenance["tool_calls"]
    collection = [
        "social_search",
        "search_interest",
        "news_coverage",
        "web_search",
        "reviews_fetch",
    ]
    assert sum(per_tool[t]["calls"] for t in collection) == snapshot.tool_calls
    assert sum(per_tool[t]["cost_usd"] for t in per_tool) == pytest.approx(snapshot.cost_usd)
    assert per_tool["analyze_text"]["cost_usd"] == 0.0


async def test_refused_calls_are_not_counted_as_provider_calls(tmp_path: Path) -> None:
    registry = world_registry(budget=Budget(max_tool_calls=2))
    result = await run_task(
        load_brief(),
        providers=registry,
        llm=models(plans=[PLAN, PLAN]).factory,
        settings=scenario_settings(tmp_path),
    )
    errors = sum(v["errors"] for v in result.provenance["tool_calls"].values())
    assert errors >= 1
    assert result.provenance["provider_calls"] == registry.budget.snapshot().tool_calls == 2


# logs


def parse_log(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]


async def test_every_log_line_of_a_run_names_the_run_and_the_task(tmp_path: Path) -> None:
    stream = io.StringIO()
    settings = scenario_settings(tmp_path).model_copy(update={"edrak_env": "production"})
    configure_logging(settings, stream=stream)
    brief = load_brief()
    await run_task(brief, providers=world_registry(), llm=models().factory, settings=settings)
    lines = parse_log(stream)
    assert len(lines) > 10
    assert all(
        line["run_id"] == brief.run_id and line["task_id"] == brief.task_id for line in lines
    )
    calls = [line for line in lines if line["event"] == "provider_call"]
    assert calls and all({"node", "tool", "provider", "capability"} <= set(line) for line in calls)
    assert {line["node"] for line in calls} == {"social", "demand", "reviews"}
    structured = [line for line in lines if line["event"].startswith("structured_call")]
    assert structured and all("node" in line for line in structured)
    assert {line["node"] for line in structured} >= {"plan_queries", "analyze", "write_findings"}


async def test_no_secret_reaches_the_logs_the_result_or_the_databases(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    respx_mock.route().mock(return_value=httpx.Response(503, json={"error": "down"}))
    stream = io.StringIO()
    settings = fake_key_settings(tmp_path, edrak_env="production")
    configure_logging(settings, stream=stream)
    brief = load_brief()
    result = await run_task(brief, llm=models(plans=[PLAN, PLAN]).factory, settings=settings)
    assert result.control_summary.status == "insufficient", "every provider was down"
    haystacks = {
        "logs": stream.getvalue(),
        "result": result.model_dump_json(),
        "evidence database": (settings.data_dir / "evidence.db").read_bytes().decode("latin-1"),
        "checkpoints": (settings.data_dir / "checkpoints.db").read_bytes().decode("latin-1"),
        "settings": json.dumps(settings.redacted(), default=str),
    }
    wal = settings.data_dir / "checkpoints.db-wal"
    if wal.exists():
        haystacks["checkpoint log"] = wal.read_bytes().decode("latin-1")
    assert "provider_failed" in haystacks["logs"], "the failures were logged"
    for secret in FAKE_KEYS.values():
        for where, text in haystacks.items():
            assert secret not in text, f"{secret} leaked into the {where}"


# re-runs


async def test_running_a_run_again_after_its_checkpoints_are_gone_adds_no_evidence(
    tmp_path: Path,
) -> None:
    settings = scenario_settings(tmp_path)
    first = await run_task(
        load_brief(), providers=world_registry(), llm=models().factory, settings=settings
    )
    for leftover in settings.data_dir.glob("checkpoints.db*"):
        leftover.unlink()
    again = await run_task(
        load_brief(), providers=world_registry(), llm=models().factory, settings=settings
    )
    assert again.evidence_ref.count == first.evidence_ref.count
    assert [f.id for f in again.findings] == [f.id for f in first.findings]
    assert again.control_summary.status == first.control_summary.status == "complete"
    assert {a.theme_label: a.count for a in again.theme_aggregates} == {
        a.theme_label: a.count for a in first.theme_aggregates
    }, "the stored evidence was analyzed again although the branches added nothing new"
    with EvidenceStore.from_settings(settings) as store:
        assert len(store.list_runs()) == 1
        assert run_checks(again, store, checkpoints=settings.data_dir / "checkpoints.db") == []


# time limits and cancellation


def tasks_now() -> set[asyncio.Task[Any]]:
    return asyncio.all_tasks()


async def settled(before: set[asyncio.Task[Any]]) -> set[asyncio.Task[Any]]:
    for _ in range(20):
        await asyncio.sleep(0.05)
        left = tasks_now() - before
        if not left:
            break
    return tasks_now() - before


async def test_a_branch_that_takes_too_long_becomes_a_gap_and_nothing_is_left_running(
    tmp_path: Path,
) -> None:
    before = tasks_now()
    settings = scenario_settings(tmp_path).model_copy(update={"branch_timeout_s": 0.3})
    # a branch that times out never reaches its closing summary, so each pass uses one turn
    scripted = models(
        plans=[PLAN, PLAN],
        social=[social_turns("a")[0], social_turns("b")[0]],
        demand=[demand_turns("a")[0], demand_turns("b")[0]],
        reviews=[reviews_turns("a")[0], reviews_turns("b")[0]],
    )
    result = await run_task(
        load_brief(),
        providers=world_registry(World(delay_s=30.0)),
        llm=scripted.factory,
        settings=settings,
    )
    for branch in ("social", "demand", "reviews"):
        assert any(
            f"the {branch} branch reported an error: the branch did not finish in 0.3 s" in g
            for g in result.gaps
        )
    assert result.control_summary.status == "insufficient"
    assert await settled(before) == set()


async def test_a_run_over_its_time_limit_leaves_no_task_behind(tmp_path: Path) -> None:
    before = tasks_now()
    brief = load_brief().model_copy(update={"budget": Budget(max_seconds=0.5)})
    result = await run_task(
        brief,
        providers=world_registry(World(delay_s=30.0)),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    assert any("stopped at its time limit" in gap for gap in result.gaps)
    assert await settled(before) == set()


async def test_closing_the_event_stream_leaves_no_task_behind(tmp_path: Path) -> None:
    before = tasks_now()
    stream = stream_task(
        load_brief(),
        providers=world_registry(World(delay_s=30.0)),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    await anext(stream)
    await stream.aclose()
    assert await settled(before) == set()


async def test_cancelling_the_worker_leaves_no_task_behind_and_closes_its_files(
    tmp_path: Path,
) -> None:
    before = tasks_now()
    pending: Awaitable[Any] = arun_worker(
        research_task(),
        providers=world_registry(World(delay_s=30.0)),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(pending, timeout=0.6)
    assert await settled(before) == set()
    db = scenario_settings(tmp_path).data_dir / "evidence.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ROLLBACK")
    finally:
        connection.close()


async def test_a_normal_run_leaves_no_task_behind(tmp_path: Path) -> None:
    before = tasks_now()
    await run_task(
        load_brief(),
        providers=world_registry(),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    assert await settled(before) == set()


# provider health


async def test_the_registry_reports_open_breakers_and_failures() -> None:
    breaker = CircuitBreaker()
    world = World(failing={"social_search:x"})
    from edrak.agents.customer_trends.providers.config import load_providers_config

    config = load_providers_config()
    registry = ProviderRegistry(
        config,
        budget=BudgetTracker(Budget()),
        breaker=breaker,
        providers=[WorldProvider("apify", {"social_search:x", "search_interest"}, world)],
    )
    healthy = registry.health()["apify"]
    assert healthy == {"capabilities": 2, "breaker_open": False, "failures": 0}
    for _ in range(3):
        with pytest.raises(Exception):  # noqa: B017  # every provider failed
            await registry.call("social_search:x", {"run_id": "r", "task_id": "t"})
    report = registry.health()["apify"]
    assert report["breaker_open"] is True and report["failures"] == 3
    assert breaker.open_providers() == ["apify"]


async def test_real_providers_report_their_keys_and_quota_without_the_keys(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://www.socialcrawl.dev/v1/credits/balance").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"balance": 37}})
    )
    settings = fake_key_settings(tmp_path)
    registry = ProviderRegistry.from_config(settings, BudgetTracker(Budget()), CircuitBreaker())
    try:
        before = registry.health()
        assert before["socialcrawl"]["key_in_use"] == "1 of 2"
        assert before["socialcrawl"]["credits_last_seen"] is None
        assert before["apify"]["key_in_use"] == "1 of 2"
        assert before["youtube_api"]["quota_units"] == "0 of 10000"
        assert before["youtube_api"]["searches"] == "0 of 100"
        await registry.providers["socialcrawl"].credits_remaining()  # type: ignore[attr-defined]  # SocialCrawlProvider
        assert registry.health()["socialcrawl"]["credits_last_seen"] == 37
        assert {"gdelt", "direct_http", "google_trends_api", "serper"} <= set(before)
        flat = json.dumps(registry.health())
        assert all(secret not in flat for secret in FAKE_KEYS.values())
    finally:
        await registry.aclose()
