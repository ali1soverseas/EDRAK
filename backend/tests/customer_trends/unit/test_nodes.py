from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from langgraph.types import Overwrite

from edrak.agents.customer_trends import nodes
from edrak.agents.customer_trends.graph import budget_remains, route_after_gap_check
from edrak.agents.customer_trends.nodes import BranchCapture, default_plan, plan_slice
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.schemas.common import Budget
from edrak.agents.customer_trends.schemas.task import QueryPlan, ReviewTarget, TaskBrief
from edrak.agents.customer_trends.state import WorkerState, initial_state
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from tests.customer_trends.factories import (
    RUN_ID,
    TASK_ID,
    FakeClock,
    load_brief,
    synthetic_evidence,
)
from tests.customer_trends.graph_helpers import (
    PLAN,
    FunctionChatModel,
    Models,
    World,
    models,
    scenario_settings,
    tool_calls,
    worker_deps,
)


def brief_for(use_case: str = "competitive_intelligence") -> TaskBrief:
    """A sample brief on the run id the shared store fixtures use."""
    return load_brief(use_case).model_copy(update={"run_id": RUN_ID, "task_id": TASK_ID})


def state_for(use_case: str = "competitive_intelligence", **extra: Any) -> WorkerState:
    return {**initial_state(brief_for(use_case)), **extra}  # type: ignore[typeddict-item]  # test state


def gap_record(gap_id: str, severity: str = "critical") -> dict[str, str]:
    return {"id": gap_id, "severity": severity, "description": "d", "suggested_action": "a"}


# intake


async def test_intake_opens_the_run_and_takes_the_focus_from_the_use_case(
    tmp_path: Path, store: EvidenceStore
) -> None:
    brief = load_brief("market_entry").model_copy(update={"focus": []})
    deps = worker_deps(brief, tmp_path, store)
    update = await nodes.intake(
        state_for("market_entry", brief=brief.model_dump(mode="json")), deps
    )
    assert update["brief"]["focus"] == ["demand", "sentiment", "competitor_gaps"]
    assert (update["replan_count"], update["analysis_done"]) == (0, False)
    assert update["budget"]["limits"]["max_tool_calls"] == 60
    assert store.run_summary(brief.run_id).task_id == brief.task_id
    assert [e["type"] for e in update["events"]] == ["node_started", "node_finished"]


async def test_intake_keeps_the_focus_the_brief_gives(tmp_path: Path, store: EvidenceStore) -> None:
    brief = load_brief("competitive_intelligence")
    update = await nodes.intake(state_for(), worker_deps(brief, tmp_path, store))
    assert update["brief"]["focus"] == ["pain_points", "sentiment", "competitor_gaps"]


# the traced wrapper


async def test_a_traced_node_reports_its_start_and_end_in_the_state(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps = worker_deps(brief_for(), tmp_path, store)

    @nodes.traced("probe")
    async def probe(state: WorkerState, deps: Any) -> dict[str, Any]:
        return {"events": [{"type": "custom"}], "replan_count": 3}

    update = await probe({}, deps)
    assert [e["type"] for e in update["events"]] == ["node_started", "node_finished", "custom"]
    assert update["events"][1]["status"] == "ok" and update["events"][1]["node"] == "probe"
    assert update["replan_count"] == 3


async def test_a_traced_node_reports_a_failure_and_lets_it_through(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps = worker_deps(brief_for(), tmp_path, store)

    @nodes.traced("probe")
    async def probe(state: WorkerState, deps: Any) -> dict[str, Any]:
        raise KeyError("missing")

    with pytest.raises(KeyError):
        await probe({}, deps)
    finished = deps.bus.events[-1]
    assert (finished["type"], finished["status"], finished["error"]) == (
        "node_finished",
        "error",
        "KeyError",
    )


# planning


def test_the_default_plan_comes_from_the_brief_and_keeps_to_five_keywords() -> None:
    brief = load_brief().model_copy(update={"competitors": [f"Rival {n}" for n in range(6)]})
    plan = default_plan(brief)
    assert plan.trend_keywords == ["GitLab", "Rival 0", "Rival 1", "Rival 2", "Rival 3"]
    assert set(plan.social_queries) == {"reddit", "x", "youtube"}
    assert plan.social_queries["reddit"][:3] == ["GitLab", "GitLab vs Rival 0", "GitLab vs Rival 1"]
    assert plan.news_queries == ["GitLab", "Rival 0", "Rival 1"]
    assert plan.review_targets == []


async def test_a_sixth_trend_keyword_is_trimmed_not_refused(
    tmp_path: Path, store: EvidenceStore
) -> None:
    answer = {"trend_keywords": ["a", "b", "c", "d", "e", "f", "g"], "rationale": "r"}
    scripted = models(plans=[answer])
    deps = worker_deps(brief_for(), tmp_path, store, scripted=scripted)
    update = await nodes.plan_queries(state_for(), deps)
    assert update["plan"]["trend_keywords"] == ["a", "b", "c", "d", "e"]
    assert update["warnings"] == [] and update["replan_count"] == 0
    assert len(scripted.planner.calls) == 1


async def test_the_planner_prompt_carries_the_brief_and_the_use_case_emphasis(
    tmp_path: Path, store: EvidenceStore
) -> None:
    scripted = models(plans=[PLAN])
    deps = worker_deps(brief_for("product_launch"), tmp_path, store, scripted=scripted)
    await nodes.plan_queries(state_for("product_launch"), deps)
    prompt = str(scripted.planner.calls[0][0].content)
    assert "Entity under study: Budgeting app for freelancers" in prompt
    assert "Country (ISO 3166-1 alpha-2): EG" in prompt
    assert "Competitors: YNAB, Wallet" in prompt
    assert "demand is rising" in prompt and "{" not in prompt.split("Return a query plan")[0]
    assert "second pass" not in prompt


async def test_a_second_pass_asks_only_for_what_closes_the_gaps(
    tmp_path: Path, store: EvidenceStore
) -> None:
    scripted = models(plans=[PLAN])
    deps = worker_deps(brief_for(), tmp_path, store, scripted=scripted)
    gaps = [
        {
            **gap_record("platforms"),
            "description": "1 platform(s) have 20 items",
            "suggested_action": "add x",
        },
        gap_record("language_ar", "minor"),
    ]
    update = await nodes.plan_queries(
        state_for(analysis_done=True, replan_count=0, gaps=gaps), deps
    )
    prompt = str(scripted.planner.calls[0][0].content)
    assert "This is a second pass" in prompt
    assert "- 1 platform(s) have 20 items: add x" in prompt and "language_ar" not in prompt
    assert update["replan_count"] == 1
    [event] = [e for e in update["events"] if e["type"] == "replan"]
    assert event["gap_ids"] == ["platforms"] and event["replan_count"] == 1


async def test_a_planner_that_cannot_answer_gives_the_default_plan_and_a_warning(
    tmp_path: Path, store: EvidenceStore
) -> None:
    def refuse(messages: Any) -> Any:
        raise ValueError("planner down")

    scripted = models()
    scripted.planner = FunctionChatModel(responses=[], responder=refuse)
    deps = worker_deps(brief_for(), tmp_path, store, scripted=scripted)
    update = await nodes.plan_queries(state_for(), deps)
    assert update["warnings"] == ["the planner failed (ValueError); a default plan was used"]
    assert update["plan"]["rationale"] == "default plan built from the brief"


# branches


def test_each_branch_works_from_its_own_part_of_the_plan() -> None:
    plan = QueryPlan.model_validate(PLAN)
    assert set(plan_slice("social", plan)) == {"social_queries", "hashtags", "competitor_angles"}
    assert set(plan_slice("demand", plan)) == {
        "trend_keywords",
        "news_queries",
        "competitor_angles",
    }
    assert set(plan_slice("reviews", plan)) == {"review_targets"}
    assert plan_slice("social", plan)["social_queries"]["x"][0] == "GitLab Duo"


def test_a_branch_with_nothing_planned_has_no_work() -> None:
    empty = QueryPlan()
    assert [plan_slice(b, empty) for b in ("social", "demand", "reviews")] == [{}, {}, {}]
    only_news = QueryPlan(news_queries=["q"])
    assert plan_slice("demand", only_news)["news_queries"] == ["q"]
    target = ReviewTarget(store="google_play", target="com.x", country="US")
    assert (
        plan_slice("reviews", QueryPlan(review_targets=[target]))["review_targets"][0]["store"]
        == "google_play"
    )


def test_a_branch_capture_remembers_batches_and_tells_why_nothing_came_back(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps = worker_deps(brief_for(), tmp_path, store)
    capture = BranchCapture(deps)
    assert capture.failure() == "no tool was called" and capture.batch_ids == []
    capture({"type": "tool_called", "status": "error", "error_code": "provider_exhausted"})
    capture({"type": "tool_called", "status": "error", "error_code": "budget_exceeded"})
    assert capture.failure() == "all 2 tool call(s) failed (budget_exceeded, provider_exhausted)"
    capture({"type": "tool_called", "status": "ok", "batch_id": "b1"})
    capture({"type": "tool_called", "status": "partial", "batch_id": "b2"})
    capture({"type": "tool_called", "status": "ok", "batch_id": "b1"})
    assert capture.failure() == "" and capture.batch_ids == ["b1", "b2"]
    assert len(deps.bus.events) == 5, "every event is passed on to the run"


async def test_a_branch_with_nothing_planned_makes_no_model_call(
    tmp_path: Path, store: EvidenceStore
) -> None:
    scripted = models()
    deps = worker_deps(brief_for(), tmp_path, store, scripted=scripted)
    state = state_for(plan=QueryPlan(news_queries=["q"]).model_dump(mode="json"))
    update = await nodes.run_branch("reviews", state, deps)
    assert update == {
        "batches": {"reviews": []},
        "branch_notes": {"reviews": "nothing was planned for this branch"},
        "branch_errors": {"reviews": ""},
    }
    assert scripted.branch_calls["reviews"] == 0


async def test_a_branch_stops_at_the_step_cap(tmp_path: Path, store: EvidenceStore) -> None:
    turns = [
        tool_calls(("social_search", {"platform": "reddit", "query": f"q{n}"}), tag=f"t{n}")
        for n in range(10)
    ]
    store.create_run(RUN_ID, TASK_ID)
    scripted = models(social=turns)
    deps = worker_deps(brief_for(), tmp_path, store, scripted=scripted, world=World())
    deps = replace(deps, settings=deps.settings.model_copy(update={"branch_max_steps": 3}))
    state = state_for(plan=QueryPlan.model_validate(PLAN).model_dump(mode="json"))
    update = await nodes.run_branch("social", state, deps)
    assert len(scripted.router.scripts["social"].calls) == 3
    assert len(update["batches"]["social"]) == 3 and update["branch_errors"] == {"social": ""}
    assert "run limit (3/3)" in update["branch_notes"]["social"]


async def test_the_step_cap_comes_from_the_settings(tmp_path: Path) -> None:
    assert scenario_settings(tmp_path).branch_max_steps == 8


async def test_a_branch_that_raises_reports_a_gap_instead(
    tmp_path: Path, store: EvidenceStore
) -> None:
    def explode(messages: Any) -> Any:
        raise RuntimeError("boom")

    scripted = models()
    scripted.router.scripts["social"] = FunctionChatModel(responses=[], responder=explode)
    deps = worker_deps(brief_for(), tmp_path, store, scripted=scripted)
    state = state_for(plan=QueryPlan.model_validate(PLAN).model_dump(mode="json"))
    update = await nodes.run_branch("social", state, deps)
    assert update["branch_errors"] == {"social": "RuntimeError: boom"}
    assert update["batches"] == {"social": []}
    assert (
        update["gaps"][0]["id"] == "branch_social" and update["gaps"][0]["severity"] == "critical"
    )
    assert (
        update["events"][0]["type"] == "gap_found"
        and update["events"][0]["gap_id"] == "branch_social"
    )


async def test_a_branch_that_never_calls_a_tool_has_failed(
    tmp_path: Path, store: EvidenceStore
) -> None:
    from langchain_core.messages import AIMessage

    scripted = models(social=[AIMessage(content="I would rather not.")])
    deps = worker_deps(brief_for(), tmp_path, store, scripted=scripted)
    state = state_for(plan=QueryPlan.model_validate(PLAN).model_dump(mode="json"))
    update = await nodes.run_branch("social", state, deps)
    assert update["branch_errors"] == {"social": "no tool was called"}
    assert update["branch_notes"]["social"] == "I would rather not."


# analyze, gap_check


async def test_analysis_without_text_batches_says_so_and_still_computes_nothing_silently(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps = worker_deps(brief_for(), tmp_path, store)
    store.create_run(RUN_ID, TASK_ID)
    update = await nodes.analyze(state_for(), deps)
    assert update["warnings"] == ["theme analysis skipped: no social or review batch was collected"]
    assert update["analysis_done"] is True and update["metric_ids"] == {}


async def test_analysis_runs_the_themes_and_the_metrics_through_the_tools(
    tmp_path: Path, store: EvidenceStore
) -> None:
    brief = brief_for()
    store.create_run(RUN_ID, TASK_ID)
    batch_id, _, _ = store.add_batch(RUN_ID, TASK_ID, "synthetic", synthetic_evidence())
    deps = worker_deps(brief, tmp_path, store)
    update = await nodes.analyze(state_for(batches={"social": [batch_id]}), deps)
    assert set(update["metric_ids"]) == {
        "volume_over_time",
        "platform_mix",
        "language_mix",
        "engagement_stats",
        "share_of_voice",
    }
    assert update["warnings"] == []
    assert store.get_aggregates(RUN_ID)
    for metric_id in update["metric_ids"].values():
        assert store.get_metric(RUN_ID, metric_id) is not None


async def test_a_failed_theme_analysis_is_a_warning_and_the_metrics_still_run(
    tmp_path: Path, store: EvidenceStore
) -> None:
    store.create_run(RUN_ID, TASK_ID)
    batch_id, _, _ = store.add_batch(RUN_ID, TASK_ID, "synthetic", synthetic_evidence())
    scripted: Models = models()
    scripted.analyst = FunctionChatModel(responses=[], responder=lambda m: {"items": 1})
    deps = worker_deps(brief_for(), tmp_path, store, scripted=scripted)
    update = await nodes.analyze(state_for(batches={"social": [batch_id]}), deps)
    assert update["warnings"][0].startswith(
        "theme analysis failed: the model could not label any chunk"
    )
    assert "platform_mix" in update["metric_ids"]


async def test_a_trend_series_gets_its_own_growth_metric(
    tmp_path: Path, store: EvidenceStore
) -> None:
    from tests.customer_trends.tool_helpers import trend_series

    store.create_run(RUN_ID, TASK_ID)
    batch_id, _, _ = store.add_batch(RUN_ID, TASK_ID, "search_interest", synthetic_evidence()[:3])
    store.save_trend_series(
        RUN_ID,
        [
            trend_series("gitlab duo", list(range(0, 80, 3))).model_copy(
                update={"batch_id": batch_id}
            ),
            trend_series("github copilot", list(range(0, 80, 2))).model_copy(
                update={"batch_id": batch_id}
            ),
        ],
    )
    update = await nodes.analyze(state_for(), worker_deps(brief_for(), tmp_path, store))
    assert {"trend_growth:gitlab duo", "trend_growth:github copilot"} <= set(update["metric_ids"])


async def test_gap_check_rebuilds_the_gap_list_and_announces_each_new_gap_once(
    tmp_path: Path, loaded_store: EvidenceStore
) -> None:
    deps = worker_deps(brief_for(), tmp_path, loaded_store)
    first = await nodes.gap_check(state_for(), deps)
    assert isinstance(first["gaps"], Overwrite)
    ids = [g["id"] for g in first["gaps"].value]
    assert ids == ["platforms"]
    announced = [e for e in first["events"] if e["type"] == "gap_found"]
    assert [e["gap_id"] for e in announced] == ["platforms"]
    again = await nodes.gap_check(state_for(gaps=first["gaps"].value), deps)
    assert [e for e in again["events"] if e["type"] == "gap_found"] == []
    assert [g["id"] for g in again["gaps"].value] == ["platforms"]


async def test_a_gap_that_is_closed_leaves_the_list(
    tmp_path: Path, loaded_store: EvidenceStore
) -> None:
    deps = worker_deps(brief_for(), tmp_path, loaded_store)
    stale = [gap_record("platforms"), gap_record("branch_social"), gap_record("evidence_total")]
    update = await nodes.gap_check(
        state_for(gaps=stale, branch_errors={"social": "", "demand": ""}), deps
    )
    assert [g["id"] for g in update["gaps"].value] == ["platforms"]


# routing


def exhausted_clock_budget(**limits: Any) -> tuple[BudgetTracker, FakeClock]:
    clock = FakeClock(0.0)
    return BudgetTracker(Budget(**limits), clock=clock), clock


@pytest.mark.parametrize(
    ("gaps", "replans", "expected"),
    [
        ([gap_record("platforms")], 0, "plan_queries"),
        ([gap_record("platforms"), gap_record("language_ar", "minor")], 0, "plan_queries"),
        ([gap_record("language_ar", "minor")], 0, "write_findings"),
        ([], 0, "write_findings"),
        ([gap_record("platforms")], 1, "write_findings"),
    ],
)
def test_the_replan_edge_needs_a_critical_gap_and_no_replan_yet(
    gaps: list[dict[str, str]], replans: int, expected: str
) -> None:
    state: WorkerState = {"gaps": gaps, "replan_count": replans}
    assert route_after_gap_check(state, BudgetTracker(Budget())) == expected


def test_no_replan_without_budget_left() -> None:
    state: WorkerState = {"gaps": [gap_record("platforms")], "replan_count": 0}
    spent = BudgetTracker(Budget(max_tool_calls=1))
    spent.record()
    assert route_after_gap_check(state, spent) == "write_findings"


def test_the_budget_is_spent_when_any_limit_is_reached() -> None:
    assert budget_remains(BudgetTracker(Budget()))
    calls = BudgetTracker(Budget(max_tool_calls=2))
    calls.record()
    assert budget_remains(calls)
    calls.record()
    assert not budget_remains(calls)
    money = BudgetTracker(Budget(max_cost_usd=0.5))
    money.record(0.5)
    assert not budget_remains(money)
    timed, clock = exhausted_clock_budget(max_seconds=10)
    clock.now = 9.9
    assert budget_remains(timed)
    clock.now = 10.0
    assert not budget_remains(timed)
