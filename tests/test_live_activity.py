"""Live run activity: worker log lines reach the run's cards while the run is going."""

from __future__ import annotations

import asyncio
import threading

import pytest

from edrak.agents.customer_trends.narration import narrate
from edrak.api.run_manager import ActiveRun, RunManager
from edrak.api.schemas import RunTaskView, RunViewOut
from edrak.api.streaming import EventStream
from edrak.contracts import UseCase, WorkerType
from edrak.core import action_log


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path, monkeypatch):
    # log_action writes a file. Keep the test's lines out of the repository's artifacts/logs.
    monkeypatch.setattr(action_log, "_ROOT", tmp_path)
    monkeypatch.delenv("EDRAK_LOG_DIR", raising=False)
    yield
    action_log._listeners.clear()


def test_a_subscribed_run_receives_the_line_and_the_file_still_gets_it(tmp_path):
    seen: list[tuple[str, str]] = []
    token = action_log.subscribe("run-1", lambda agent, message: seen.append((agent, message)))
    try:
        action_log.log_action("market_intelligence", "searching sources")
    finally:
        action_log.unsubscribe("run-1", token)
    assert seen == [("market_intelligence", "searching sources")]
    assert "searching sources" in (tmp_path / "artifacts/logs/market_intelligence.log").read_text()


def test_nothing_is_delivered_after_unsubscribing():
    seen: list[str] = []
    token = action_log.subscribe("run-1", lambda agent, message: seen.append(message))
    action_log.unsubscribe("run-1", token)
    action_log.log_action("market_intelligence", "too late")
    assert seen == []


def test_a_thread_that_lost_the_run_context_still_reaches_a_single_run():
    # The customer worker runs its own event loop in a fresh thread, which does not inherit the context.
    seen: list[str] = []
    token = action_log.subscribe("run-1", lambda agent, message: seen.append(message))
    try:
        worker = threading.Thread(target=lambda: action_log.log_action("customer_trends", "from a bare thread"))
        worker.start()
        worker.join()
    finally:
        action_log.unsubscribe("run-1", token)
    assert seen == ["from a bare thread"]


def test_with_two_runs_a_line_without_context_is_not_guessed_at():
    first: list[str] = []
    second: list[str] = []
    action_log.subscribe("run-1", lambda agent, message: first.append(message))
    action_log.subscribe("run-2", lambda agent, message: second.append(message))
    worker = threading.Thread(target=lambda: action_log.log_action("customer_trends", "whose?"))
    worker.start()
    worker.join()
    assert first == [] and second == []


def test_a_failing_listener_never_breaks_the_worker():
    def boom(agent: str, message: str) -> None:
        raise RuntimeError("listener bug")

    token = action_log.subscribe("run-1", boom)
    try:
        action_log.log_action("market_intelligence", "still fine")
    finally:
        action_log.unsubscribe("run-1", token)


def test_customer_events_read_as_plain_lines():
    assert narrate({"type": "node_started", "node": "plan_queries"}) == "Planning searches"
    assert narrate({"type": "node_started", "node": "demand"}) == "Collecting from the demand branch"
    assert narrate({"type": "tool_called", "tool": "search", "provider": "apify", "status": "ok", "count": 12}) == "search via apify: 12 item(s)"
    failed = narrate({"type": "tool_called", "tool": "reviews", "provider": "apify", "status": "error", "error_code": "provider_exhausted", "fallback_used": True})
    assert failed == "reviews via apify failed (provider_exhausted), then tried a fallback"
    assert narrate({"type": "run_finished", "findings": 6, "evidence": 5}) == "Finished: 6 findings from 5 sources"
    # Bookkeeping events are not shown.
    assert narrate({"type": "node_finished", "status": "ok"}) is None


def _active() -> ActiveRun:
    tasks = [
        RunTaskView(task_id="t1", worker=WorkerType.MARKET_INTELLIGENCE, state="running", progress=25, log=[{"at": "x", "text": "Worker dispatched"}]),
        RunTaskView(task_id="t2", worker=WorkerType.CUSTOMER_TRENDS, state="done", progress=100),
    ]
    view = RunViewOut(
        analysis_id="a1", title="T", use_case=UseCase.COMPETITIVE_INTELLIGENCE, state="running", stage="workers",
        approved_at="now", approved_by="me", started_at="now", finished_at=None, tasks=tasks, events=[],
        verification="waiting", brief_id=None,
    )
    return ActiveRun(run_view=view, stream=EventStream())


def test_a_worker_line_updates_its_card():
    async def go() -> None:
        active = _active()
        RunManager()._apply_activity(active, "market_intelligence", "searching sources\nsecond line is ignored")
        card = active.run_view.tasks[0]
        assert card.activity == "searching sources"
        assert card.log[-1]["text"] == "searching sources"
        assert 25 < card.progress <= 92
        # And the stream was told, for clients that listen instead of poll.
        assert not active.stream.queue.empty()

    asyncio.run(go())


def test_progress_only_rises_and_never_reaches_100_from_logging_alone():
    async def go() -> None:
        active = _active()
        manager = RunManager()
        previous = 0
        for step in range(400):
            manager._apply_activity(active, "market_intelligence", f"step {step}")
            current = active.run_view.tasks[0].progress
            assert current >= previous
            previous = current
        assert previous <= 92
        assert len(active.run_view.tasks[0].log) <= RunManager._MAX_LOG_LINES

    asyncio.run(go())


def test_a_finished_card_and_an_unknown_agent_are_left_alone():
    async def go() -> None:
        active = _active()
        manager = RunManager()
        manager._apply_activity(active, "customer_trends", "late line")      # that card is done
        manager._apply_activity(active, "verification", "not a worker")      # not a card
        assert active.run_view.tasks[1].log == []
        assert active.run_view.tasks[1].progress == 100
        assert active.run_view.tasks[0].log[-1]["text"] == "Worker dispatched"

    asyncio.run(go())


def test_a_run_interrupted_by_a_restart_is_marked_failed(tmp_path):
    import aiosqlite

    from edrak.db.connection import schema_sql
    from edrak.db.repositories.runs import fail_orphaned_runs

    async def go() -> None:
        async with aiosqlite.connect(tmp_path / "t.db") as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(schema_sql())
            await db.execute("PRAGMA foreign_keys = OFF")
            await db.execute("DROP TRIGGER runs_require_approved_plan")
            for rid, status in (("a1", "running"), ("a2", "completed"), ("a3", "awaiting_review")):
                await db.execute(
                    "INSERT INTO business_requests (request_id, workspace_id, title, goal, company_profile, use_case, status) "
                    "VALUES (?, 'w', 'T', 'g', '{\"name\": \"A\"}', 'competitive_intelligence', 'running')", (rid,))
                await db.execute("INSERT INTO runs (request_id, plan_id, status) VALUES (?, 'p', ?)", (rid, status))
            await db.commit()
            assert await fail_orphaned_runs(db) == ["a1"]
            assert (await (await db.execute("select status from runs where request_id='a1'")).fetchone())[0] == "failed"
            assert (await (await db.execute("select status from business_requests where request_id='a1'")).fetchone())[0] == "failed"
            assert (await (await db.execute("select status from runs where request_id='a2'")).fetchone())[0] == "completed"
            # A run paused for review keeps waiting: its graph state is checkpointed.
            assert (await (await db.execute("select status from runs where request_id='a3'")).fetchone())[0] == "awaiting_review"
            assert await fail_orphaned_runs(db) == []

    asyncio.run(go())


def test_a_second_pass_counts_its_progress_from_the_second_pass():
    async def go() -> None:
        active = _active()
        manager = RunManager()
        card = active.run_view.tasks[0]
        for step in range(60):
            manager._apply_activity(active, "market_intelligence", f"first pass {step}")
        assert card.progress > 80
        manager._restart_card(active, card, "Going again: a contradiction", "now")
        assert card.state == "running" and card.progress == 30
        manager._apply_activity(active, "market_intelligence", "second pass step")
        # One step into the new pass, not 60 steps in.
        assert 30 <= card.progress < 45

    asyncio.run(go())


def test_verification_steps_show_progress_only_while_it_is_running():
    async def go() -> None:
        active = _active()
        manager = RunManager()
        view = active.run_view

        manager._apply_activity(active, "verification", "Checking finding 1 of 10: early")
        assert view.verification_log == []                      # not running yet

        view.verification = "running"
        manager._apply_activity(active, "verification", "Checking finding 6 of 10: a claim")
        assert view.verification_activity == "Checking finding 6 of 10: a claim"
        assert view.verification_progress == 50                  # five of ten are done
        manager._apply_activity(active, "verification", "Checking finding 3 of 10: stale")
        assert view.verification_progress == 50                  # never goes backwards
        manager._apply_activity(active, "verification", "[verification] NODE: decide\nprintout follows")
        assert len(view.verification_log) == 2                   # the captured printout is not a step

    asyncio.run(go())


def test_the_graph_runs_the_approved_plan_and_does_not_plan_again(monkeypatch):
    from edrak.contracts import BusinessContext, BusinessRequest, CompanyProfile, ResearchPlan, ResearchTask
    from edrak.orchestration import nodes

    request = BusinessRequest(
        goal="g", company_profile=CompanyProfile(name="Acme"),
        business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
    )
    approved = ResearchPlan(
        request_id=request.request_id,
        tasks=[ResearchTask(
            parent_request_id=request.request_id, worker=WorkerType.INTERNAL_INTELLIGENCE, goal="g", focus="f",
            company_profile=request.company_profile, business_context=request.business_context,
        )],
    )

    class Boom:
        def plan(self, _request):
            raise AssertionError("planned again")

    monkeypatch.setattr(nodes, "LlmPlanner", Boom)
    state = {"request": request, "plan": approved}
    assert nodes.plan_node(state)["plan"] is approved
