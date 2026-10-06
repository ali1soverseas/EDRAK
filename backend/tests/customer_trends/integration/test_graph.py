"""The whole worker graph over the scripted world: no network, no key, no real model."""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.runner import run_task, stream_task
from edrak.agents.customer_trends.schemas.common import Budget, Confidence
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink
from edrak.agents.customer_trends.tools.submit_findings import (
    cited_metric_ids,
    claim_numbers,
    load_verdict_phrases,
    matches,
    own_numbers,
    verdict_phrases_in,
)
from tests.customer_trends.factories import load_brief
from tests.customer_trends.graph_helpers import (
    PLAN,
    PLAN_SOCIAL_ONLY,
    FunctionChatModel,
    Models,
    World,
    WriterScript,
    demand_turns,
    models,
    reviews_turns,
    scenario_settings,
    social_turns,
    world_registry,
)

USE_CASES = ["competitive_intelligence", "product_launch", "market_entry"]
ALL_CAPABILITIES = set(load_providers_config().routing)
NODE_ORDER = ["intake", "plan_queries", "join", "analyze", "gap_check", "write_findings", "submit"]


async def run(
    tmp_path: Path,
    brief: TaskBrief,
    *,
    world: World | None = None,
    scripted: Models | None = None,
    budget: Budget | None = None,
    settings: Settings | None = None,
    sink: Any = None,
) -> tuple[CustomerTrendsResult, Models, Settings]:
    settings = settings or scenario_settings(tmp_path)
    scripted = scripted or models()
    result = await run_task(
        brief,
        providers=world_registry(world, budget),
        llm=scripted.factory,
        settings=settings,
        sink=sink,
    )
    return result, scripted, settings


def metric_numbers(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [float(value)]
    if isinstance(value, dict):
        return [n for inner in value.values() for n in metric_numbers(inner)]
    if isinstance(value, list):
        return [n for inner in value for n in metric_numbers(inner)]
    return []


def assert_grounded(result: CustomerTrendsResult, store: EvidenceStore) -> None:
    """Every finding cites stored evidence and every number in a claim is in its metrics."""
    assert result.findings
    for finding in result.findings:
        assert finding.evidence_ids
        assert store.existing_ids(result.run_id, finding.evidence_ids) == set(finding.evidence_ids)
        known = own_numbers(finding.metrics)
        for metric_id in cited_metric_ids(finding.metrics):
            record = store.get_metric(result.run_id, metric_id)
            assert record is not None, metric_id
            known += metric_numbers(record.values)
        for number in claim_numbers(finding.claim):
            assert any(matches(number, value) for value in known), (finding.claim, number)
        assert not verdict_phrases_in(finding.claim, load_verdict_phrases())


@pytest.mark.parametrize("use_case", USE_CASES)
async def test_the_happy_path_of_each_use_case_writes_a_readable_grounded_result(
    tmp_path: Path, use_case: str
) -> None:
    brief = load_brief(use_case)
    plan = {**PLAN, "review_targets": []} if use_case == "market_entry" else PLAN
    result, scripted, settings = await run(tmp_path, brief, scripted=models(plans=[plan]))

    summary = result.control_summary
    assert summary.status == "complete" and result.gaps == []
    assert summary.findings_count == len(result.findings) == 3
    assert summary.evidence_count == result.evidence_ref.count > 60
    assert summary.headline and len(summary.headline.split()) <= 60
    assert result.worker == "customer_trends" and result.task_id == brief.task_id
    assert result.theme_aggregates and result.trend_series
    assert summary.coverage["by_platform"]["reddit"] == 30
    assert summary.budget_used["tool_calls"] > 0

    store = EvidenceStore.from_settings(settings)
    with store:
        assert_grounded(result, store)
        assert LocalSink(store, settings.artifacts_dir).read_result(brief.run_id) == result
        assert store.result_location(brief.run_id) is not None
        assert store.get_findings(brief.run_id) == result.findings
    assert scripted.writer_script.seen == ["write", "headline"]


async def test_the_competitive_intelligence_run_uses_every_branch_and_the_emphasis(
    tmp_path: Path,
) -> None:
    brief = load_brief("competitive_intelligence")
    result, scripted, _ = await run(tmp_path, brief)
    assert scripted.branch_calls == {"social": 2, "demand": 2, "reviews": 2}
    assert len(scripted.planner.calls) == 1
    planner_prompt = str(scripted.planner.calls[0][0].content)
    assert "GitHub Copilot" in planner_prompt and "share of voice" in planner_prompt
    assert result.provenance["providers_used"] and result.provenance["replans"] == 0
    assert set(result.provenance["tool_calls"]) >= {
        "social_search",
        "search_interest",
        "reviews_fetch",
        "analyze_text",
        "compute_metrics",
        "evidence_query",
        "submit_findings",
    }
    assert result.provenance["models"]["branch"] == "branch-router"
    assert "node_timings_ms" in result.provenance and result.provenance["fallbacks"] == []


async def test_the_product_launch_run_cites_a_trend_and_the_market_entry_run_skips_reviews(
    tmp_path: Path,
) -> None:
    pl, _, _ = await run(tmp_path / "pl", load_brief("product_launch"))
    assert any(f.type.value == "trend" for f in pl.findings)
    plan = {**PLAN, "review_targets": []}
    me, scripted, _ = await run(
        tmp_path / "me", load_brief("market_entry"), scripted=models(plans=[plan])
    )
    assert scripted.branch_calls["reviews"] == 0
    assert "review" not in me.control_summary.coverage["by_source_type"]


async def test_the_use_case_fills_the_focus_when_the_brief_has_none(tmp_path: Path) -> None:
    brief = load_brief("product_launch").model_copy(update={"focus": []})
    result, scripted, _ = await run(tmp_path, brief)
    assert result.brief.focus == ["pain_points", "demand", "competitor_gaps"]
    assert "Focus: pain_points, demand, competitor_gaps" in str(
        scripted.planner.calls[0][0].content
    )


async def test_a_critical_gap_triggers_exactly_one_replan_that_closes_it(tmp_path: Path) -> None:
    social = ("social_search:reddit", "social_search:x", "social_search:youtube")
    world = World(failing=set(social), fail_calls=1)
    scripted = models(
        plans=[PLAN, PLAN_SOCIAL_ONLY], social=[*social_turns("a"), *social_turns("b")]
    )
    result, scripted, _ = await run(tmp_path, load_brief(), world=world, scripted=scripted)

    assert result.control_summary.status == "complete" and result.gaps == []
    assert result.provenance["replans"] == 1
    assert len(scripted.planner.calls) == 2
    assert scripted.branch_calls == {"social": 4, "demand": 2, "reviews": 2}
    second_plan = str(scripted.planner.calls[1][0].content)
    assert "This is a second pass" in second_plan and "branch_social" not in second_plan
    assert "reported an error" in second_plan and "collect at least 20 items" in second_plan
    assert result.control_summary.coverage["by_platform"]["reddit"] == 30


async def test_the_replan_is_bounded_even_when_the_gap_stays(tmp_path: Path) -> None:
    social = {"social_search:reddit", "social_search:x", "social_search:youtube"}
    scripted = models(
        plans=[PLAN, PLAN_SOCIAL_ONLY, PLAN_SOCIAL_ONLY],
        social=[*social_turns("a"), *social_turns("b"), *social_turns("c")],
    )
    result, scripted, _ = await run(
        tmp_path, load_brief(), world=World(failing=social), scripted=scripted
    )
    assert result.provenance["replans"] == 1
    assert len(scripted.planner.calls) == 2 and scripted.branch_calls["social"] == 4
    assert result.control_summary.status == "partial"
    assert any("social branch reported an error" in gap for gap in result.gaps)
    assert any("platform(s) have at least 20 items" in gap for gap in result.gaps)
    assert result.control_summary.coverage["by_source_type"]["review"] == 25


async def test_when_every_provider_fails_the_result_is_insufficient_and_explains_why(
    tmp_path: Path,
) -> None:
    turns = {
        "social": [*social_turns("a"), *social_turns("b")],
        "demand": [*demand_turns("a"), *demand_turns("b")],
        "reviews": [*reviews_turns("a"), *reviews_turns("b")],
    }
    scripted = models(plans=[PLAN, PLAN], writer=WriterScript(headline=None), **turns)
    result, scripted, settings = await run(
        tmp_path, load_brief(), world=World(failing=ALL_CAPABILITIES), scripted=scripted
    )
    summary = result.control_summary
    assert summary.status == "insufficient" and result.findings == []
    assert summary.evidence_count == 0 and summary.findings_count == 0
    assert summary.overall_confidence is Confidence.LOW
    assert summary.headline.startswith("0 evidence items were collected")
    assert any("only 0 evidence items" in gap for gap in result.gaps)
    for branch in ("social", "demand", "reviews"):
        assert any(f"the {branch} branch reported an error" in gap for gap in result.gaps)
    assert any("theme analysis skipped" in warning for warning in summary.warnings)
    assert scripted.analyst.calls == []
    assert result.provenance["replans"] == 1
    with EvidenceStore.from_settings(settings) as store:
        assert LocalSink(store, settings.artifacts_dir).read_result(result.run_id) == result


async def test_an_exhausted_budget_ends_in_a_partial_result_without_a_replan(
    tmp_path: Path,
) -> None:
    result, scripted, _ = await run(
        tmp_path, load_brief(), budget=Budget(max_tool_calls=2), scripted=models(plans=[PLAN, PLAN])
    )
    assert result.control_summary.status in {"partial", "insufficient"}
    assert result.provenance["replans"] == 0 and len(scripted.planner.calls) == 1
    errors = [e for e in result.provenance["tool_calls"].values() if e["errors"]]
    assert errors, "some tool call was refused for lack of budget"
    assert result.gaps and result.control_summary.budget_used["tool_calls"] == 2.0


async def test_a_crashing_branch_becomes_a_gap_and_the_others_carry_on(tmp_path: Path) -> None:
    def explode(messages: Any) -> Any:
        raise RuntimeError("model exploded")

    scripted = models(plans=[PLAN, PLAN_SOCIAL_ONLY])
    scripted.router.scripts["social"] = FunctionChatModel(responses=[], responder=explode)
    events: list[dict[str, Any]] = []
    async for event in stream_task(
        load_brief(),
        providers=world_registry(),
        llm=scripted.factory,
        settings=scenario_settings(tmp_path),
    ):
        events.append(event)

    assert events[-1]["type"] == "run_finished" and events[-1]["status"] == "partial"
    assert not any(e["type"] == "run_failed" for e in events)
    with EvidenceStore.from_settings(scenario_settings(tmp_path)) as store:
        result = LocalSink(store, scenario_settings(tmp_path).artifacts_dir).read_result(
            load_brief().run_id
        )
    assert any(
        "the social branch reported an error: RuntimeError: model exploded" in g
        for g in result.gaps
    )
    assert result.control_summary.coverage["by_source_type"]["review"] == 25
    assert result.control_summary.coverage["by_source_type"]["news"] == 12
    assert result.provenance["replans"] == 1


async def test_a_run_over_its_time_limit_still_writes_a_partial_result(tmp_path: Path) -> None:
    brief = load_brief().model_copy(update={"budget": Budget(max_seconds=0.6)})
    started = time.monotonic()
    result, _, settings = await run(tmp_path, brief, world=World(delay_s=30.0))
    assert time.monotonic() - started < 10
    assert result.control_summary.status == "insufficient"
    assert any("stopped at its time limit of 0.6 s" in gap for gap in result.gaps)
    assert any("time limit" in warning for warning in result.control_summary.warnings)
    with EvidenceStore.from_settings(settings) as store:
        assert LocalSink(store, settings.artifacts_dir).read_result(brief.run_id) == result


async def test_a_finding_that_fails_the_checks_is_repaired_once_or_dropped(tmp_path: Path) -> None:
    scripted = models(writer=WriterScript(bad_first=True))
    events: list[dict[str, Any]] = []
    async for event in stream_task(
        load_brief(),
        providers=world_registry(),
        llm=scripted.factory,
        settings=scenario_settings(tmp_path),
    ):
        events.append(event)
    assert scripted.writer_script.seen == ["write", "repair", "headline"]
    repair_call = scripted.writer.calls[1]
    assert "Some of your findings were rejected" in str(repair_call[0].content)
    rejected = str(repair_call[1].content).split("REJECTED:\n", 1)[1]
    assert "evidence ids not found" in rejected and "verdict language" in rejected
    assert "not in metrics: 40" in rejected

    with EvidenceStore.from_settings(scenario_settings(tmp_path)) as store:
        result = LocalSink(store, scenario_settings(tmp_path).artifacts_dir).read_result(
            load_brief().run_id
        )
        assert_grounded(result, store)
    assert [f.id for f in result.findings] == ["f1", "f2"]
    assert any(
        "finding verdict was dropped: the claim contains verdict language" in w
        for w in result.control_summary.warnings
    )
    kinds = [(e["type"], e.get("finding_id")) for e in events if e["type"].startswith("finding_")]
    assert ("finding_rejected", "verdict") in kinds
    assert [k for k in kinds if k[0] == "finding_accepted"] == [
        ("finding_accepted", "f1"),
        ("finding_accepted", "f2"),
    ]


async def test_a_writer_that_fails_leaves_a_result_without_findings(tmp_path: Path) -> None:
    result, scripted, _ = await run(
        tmp_path, load_brief(), scripted=models(writer=WriterScript(fail=True))
    )
    assert result.findings == []
    assert any("the writer failed (ValueError)" in w for w in result.control_summary.warnings)
    assert result.control_summary.status == "complete"
    assert result.control_summary.overall_confidence is Confidence.LOW


async def test_a_headline_with_a_verdict_is_replaced_by_the_counted_one(tmp_path: Path) -> None:
    script = WriterScript(headline="GitLab should enter this market.")
    result, _, _ = await run(tmp_path, load_brief(), scripted=models(writer=script))
    assert result.control_summary.headline.startswith(f"{result.evidence_ref.count} evidence items")


async def test_a_planner_that_fails_falls_back_to_a_default_plan(tmp_path: Path) -> None:
    result, scripted, _ = await run(
        tmp_path, load_brief(), scripted=models(plans=[{"social_queries": 5}, {"x": 1}])
    )
    assert any(
        "the planner failed" in w and "default plan" in w for w in result.control_summary.warnings
    )
    assert len(scripted.planner.calls) == 2  # the answer and its one repair
    assert scripted.branch_calls["social"] == 2 and scripted.branch_calls["reviews"] == 0
    assert result.control_summary.status in {"complete", "partial"}


async def test_the_events_come_in_a_sensible_order(tmp_path: Path) -> None:
    events = [
        event
        async for event in stream_task(
            load_brief(),
            providers=world_registry(),
            llm=models().factory,
            settings=scenario_settings(tmp_path),
        )
    ]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert all(e["run_id"] == load_brief().run_id for e in events)
    assert {e["type"] for e in events} == {
        "node_started",
        "node_finished",
        "tool_called",
        "finding_accepted",
        "run_finished",
    }
    assert events[0] == {**events[0], "type": "node_started", "node": "intake"}
    assert events[-1]["type"] == "run_finished" and events[-1]["status"] == "complete"
    positions = {}
    for index, event in enumerate(events):
        if event["type"] in {"node_started", "node_finished"}:
            positions[(event["type"], event["node"])] = index
    for node in [*NODE_ORDER, "social", "demand", "reviews", "submit"]:
        assert positions[("node_started", node)] < positions[("node_finished", node)], node
    sequence = [positions[("node_started", node)] for node in NODE_ORDER]
    assert sequence == sorted(sequence)
    for branch in ("social", "demand", "reviews"):
        assert positions[("node_started", "plan_queries")] < positions[("node_started", branch)]
        assert positions[("node_finished", branch)] < positions[("node_started", "join")]
    tool_events = [
        i for i, e in enumerate(events) if e["type"] == "tool_called" and e.get("branch")
    ]
    assert tool_events
    for index in tool_events:
        branch = events[index]["branch"]
        assert positions[("node_started", branch)] < index < positions[("node_finished", branch)]
    collection = [
        i for i, e in enumerate(events) if e.get("tool") in {"analyze_text", "compute_metrics"}
    ]
    assert positions[("node_started", "analyze")] < min(collection)
    accepted = [i for i, e in enumerate(events) if e["type"] == "finding_accepted"]
    assert min(accepted) > positions[("node_finished", "write_findings")]
    assert all(e["type"] != "tool_called" or e["tool"] for e in events)


async def test_gap_and_replan_events_are_reported(tmp_path: Path) -> None:
    world = World(
        failing={"social_search:reddit", "social_search:x", "social_search:youtube"}, fail_calls=1
    )
    scripted = models(
        plans=[PLAN, PLAN_SOCIAL_ONLY], social=[*social_turns("a"), *social_turns("b")]
    )
    events = [
        event
        async for event in stream_task(
            load_brief(),
            providers=world_registry(world),
            llm=scripted.factory,
            settings=scenario_settings(tmp_path),
        )
    ]
    types = [e["type"] for e in events]
    gaps = [e for e in events if e["type"] == "gap_found"]
    assert {g["gap_id"]: g["severity"] for g in gaps} == {
        "branch_social": "critical",
        "platforms": "critical",
        "language_ar": "minor",
    }
    assert all(g["suggested_action"] and g["description"] for g in gaps)
    assert len(gaps) == 3, "a gap is announced once, where it is first found"
    [replan] = [e for e in events if e["type"] == "replan"]
    assert replan["replan_count"] == 1 and set(replan["gap_ids"]) == {"branch_social", "platforms"}
    first_check = types.index("gap_found")
    assert first_check < types.index("replan") < len(types) - 1
    assert types.count("replan") == 1


async def test_the_state_holds_pointers_not_evidence(tmp_path: Path) -> None:
    result, _, settings = await run(tmp_path, load_brief())
    with EvidenceStore.from_settings(settings) as store:
        texts = [
            item.text for item in store.query(result.run_id, limit=1000, sample="recent").items
        ]
    assert len(texts) > 100
    quotes = {q for a in result.theme_aggregates for q in a.representative_quotes}
    checkpoints = 0
    async with AsyncSqliteSaver.from_conn_string(
        str(settings.data_dir / "checkpoints.db")
    ) as saver:
        async for saved in saver.alist({"configurable": {"thread_id": result.run_id}}):
            checkpoints += 1
            values = saved.checkpoint["channel_values"]
            blob = json.dumps(values, ensure_ascii=False, default=str)
            assert len(blob) < 150_000
            present = [t for t in texts if t in blob]
            if values.get("result") is None:
                assert present == [], "item text in the state before the result exists"
            else:
                assert set(present) <= quotes and len(present) <= len(quotes)
    assert checkpoints >= 8


async def test_a_finished_run_is_not_run_again(tmp_path: Path) -> None:
    world = World()
    first, _, settings = await run(tmp_path, load_brief(), world=world)
    calls = dict(world.calls)
    scripted = models()
    second = await run_task(
        load_brief(), providers=world_registry(world), llm=scripted.factory, settings=settings
    )
    assert second == first
    assert world.calls == calls and scripted.planner.calls == []


class BrokenSink:
    def write_result(self, result: CustomerTrendsResult) -> str:
        raise OSError("disk full")

    def read_result(self, run_id: str) -> CustomerTrendsResult:
        raise OSError("disk full")


async def test_a_crash_is_a_failed_run_with_a_result_and_the_run_resumes(tmp_path: Path) -> None:
    world = World()
    settings = scenario_settings(tmp_path)
    brief = load_brief()
    events: list[dict[str, Any]] = []
    async for event in stream_task(
        brief,
        providers=world_registry(world),
        llm=models().factory,
        settings=settings,
        sink=BrokenSink(),
    ):
        events.append(event)
    failed = [e for e in events if e["type"] == "run_failed"]
    assert len(failed) == 1 and failed[0]["error"] == "OSError"
    assert events[-1]["type"] == "run_failed"
    collected = dict(world.calls)
    assert collected

    scripted = models()
    result = await run_task(
        brief, providers=world_registry(world), llm=scripted.factory, settings=settings
    )
    assert result.control_summary.status == "complete" and result.findings
    assert world.calls == collected, "the collection was not repeated"
    assert scripted.planner.calls == [] and scripted.analyst.calls == []
    with EvidenceStore.from_settings(settings) as store:
        assert LocalSink(store, settings.artifacts_dir).read_result(brief.run_id) == result


async def test_a_crash_before_any_result_gives_a_failure_result(tmp_path: Path) -> None:
    result, _, _ = await run(tmp_path, load_brief(), sink=BrokenSink())
    assert result.control_summary.status == "partial"
    assert any("the run failed with OSError: disk full" in gap for gap in result.gaps)
    assert any("disk full" in warning for warning in result.control_summary.warnings)
    assert result.findings, "the findings were stored before the result could be written"


async def test_stopping_the_stream_early_cancels_the_run(tmp_path: Path) -> None:
    world = World(delay_s=30.0)
    stream = stream_task(
        load_brief(),
        providers=world_registry(world),
        llm=models().factory,
        settings=scenario_settings(tmp_path),
    )
    first = await anext(stream)
    assert first["type"] == "node_started"
    await stream.aclose()
