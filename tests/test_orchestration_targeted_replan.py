import asyncio
from typing import ClassVar

import pytest
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    EvidenceQuality,
    FindingCheckStatus,
    ResearchPlan,
    ResearchTask,
    UseCase,
    VerificationDecision,
    VerificationResult,
    VerificationStatus,
    WorkerRegistry,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.contracts.verification import FindingVerdict, TargetedAction
from edrak.core.config import Settings
from edrak.orchestration import graph as orchestration_graph
from edrak.orchestration.nodes import replan_node
from edrak.orchestration.state import RESET, merge_by_task_id


class _CrossSignalOutput:
    status = "completed"
    signals: ClassVar[list] = []

    def model_dump(self, *, mode):
        return {"status": self.status, "signals": self.signals}


async def _noop_cross_signal(_payload):
    return _CrossSignalOutput()


def _request() -> BusinessRequest:
    return BusinessRequest(
        request_id="targeted-replan",
        goal="Evaluate GitHub Copilot capabilities.",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub"],
        ),
    )


def _task(request: BusinessRequest, worker: WorkerType, task_id: str) -> ResearchTask:
    return ResearchTask(
        task_id=task_id,
        parent_request_id=request.request_id,
        worker=worker,
        goal=f"Research {worker.value}.",
        focus="AI development capabilities",
        company_profile=request.company_profile,
        business_context=request.business_context,
    )


def _result(task: ResearchTask) -> WorkerResult:
    return WorkerResult(
        task_id=task.task_id,
        worker=task.worker,
        status=WorkerStatus.COMPLETED,
    )


def _plan(request, tasks) -> ResearchPlan:
    return ResearchPlan(request_id=request.request_id, tasks=list(tasks))


def _state(request, tasks, *, contradictions_for=None, status=VerificationStatus.REPLAN_REQUIRED):
    """State as it looks when verification has just asked for a replan.

    ``contradictions_for`` maps a worker to the contradiction strings reported
    against its findings. Omit it to model a verification stage that raised
    before producing any findings.
    """
    findings = []
    actions = []
    if contradictions_for:
        for worker, items in contradictions_for.items():
            findings.append(
                FindingVerdict(
                    finding_id=f"finding-{worker.value}",
                    worker=worker,
                    statement="A claim two sources disagree on.",
                    verification_status=FindingCheckStatus.VERIFIED,
                    evidence_quality=EvidenceQuality.HIGH,
                    contradictions=list(items),
                    confidence=0.8,
                )
            )
            for item in items:
                actions.append(TargetedAction(worker=worker, reason=item))
    if status is VerificationStatus.REPLAN_REQUIRED and not actions:
        # The contract requires at least one action for this status.
        actions.append(
            TargetedAction(worker=WorkerType.COMPETITOR_INTELLIGENCE, reason="Re-check.")
        )
    decision = VerificationDecision(status=status, summary="replan", targeted_actions=actions)
    return {
        "request": request,
        "plan": _plan(request, tasks),
        "results": [_result(t) for t in tasks],
        "verification": decision,
        "verification_result": VerificationResult(
            research_run_id=request.request_id,
            decision=decision,
            findings=findings,
        ),
        "replan_count": 0,
    }


def test_replan_redispatches_only_the_contradicting_worker():
    request = _request()
    tasks = [
        _task(request, WorkerType.COMPETITOR_INTELLIGENCE, "competitor-task"),
        _task(request, WorkerType.MARKET_INTELLIGENCE, "market-task"),
        _task(request, WorkerType.INTERNAL_INTELLIGENCE, "internal-task"),
    ]
    state = _state(
        request,
        tasks,
        contradictions_for={
            WorkerType.COMPETITOR_INTELLIGENCE: [
                "Sources report different pricing or packaging for the same claim."
            ]
        },
    )

    new_state = replan_node(state)
    dispatched = new_state["replan_tasks"]

    assert [t.worker for t in dispatched] == [WorkerType.COMPETITOR_INTELLIGENCE]
    # Same task id, so merge_by_task_id replaces only this worker's result and
    # the other two findings survive.
    assert [t.task_id for t in dispatched] == ["competitor-task"]
    merged = merge_by_task_id(
        state["results"],
        [_result(dispatched[0])],
    )
    assert {r.task_id for r in merged} == {
        "competitor-task",
        "market-task",
        "internal-task",
    }
    assert len(merged) == 3


def test_replan_keeps_the_plan_intact_so_results_stay_valid():
    """OrchestrationResult rejects a result whose task_id is not in the plan.

    Narrowing plan.tasks would orphan the results of the workers that were not
    re-run, so the plan keeps every task and only the dispatch set narrows.
    """
    request = _request()
    tasks = [
        _task(request, WorkerType.COMPETITOR_INTELLIGENCE, "competitor-task"),
        _task(request, WorkerType.MARKET_INTELLIGENCE, "market-task"),
    ]
    state = _state(
        request,
        tasks,
        contradictions_for={WorkerType.MARKET_INTELLIGENCE: ["Two sources disagree."]},
    )

    new_state = replan_node(state)

    assert [t.task_id for t in new_state["plan"].tasks] == ["competitor-task", "market-task"]
    plan_ids = {t.task_id for t in new_state["plan"].tasks}
    assert {r.task_id for r in new_state["results"]} <= plan_ids


def test_replan_keeps_previous_results_and_does_not_reset():
    request = _request()
    tasks = [
        _task(request, WorkerType.COMPETITOR_INTELLIGENCE, "competitor-task"),
        _task(request, WorkerType.MARKET_INTELLIGENCE, "market-task"),
    ]
    state = _state(
        request,
        tasks,
        contradictions_for={
            WorkerType.MARKET_INTELLIGENCE: ["Two sources give different figures."]
        },
    )

    new_state = replan_node(state)

    # The competitor's findings are still there: a replan must not discard work
    # that verification did not object to.
    assert [r.task_id for r in new_state["results"]] == ["competitor-task", "market-task"]
    assert new_state["replan_count"] == 1


def test_replan_amends_the_task_with_the_contradiction():
    request = _request()
    task = _task(request, WorkerType.COMPETITOR_INTELLIGENCE, "competitor-task")
    contradiction = "Sources report different pricing or packaging for the same claim."
    state = _state(
        request, [task], contradictions_for={WorkerType.COMPETITOR_INTELLIGENCE: [contradiction]}
    )

    amended = replan_node(state)["replan_tasks"][0]

    assert amended.goal.startswith(task.goal)
    assert contradiction in amended.goal
    assert contradiction in amended.focus
    assert amended.attempt == task.attempt + 1


def test_replan_counts_contradictions_per_worker():
    request = _request()
    tasks = [
        _task(request, WorkerType.COMPETITOR_INTELLIGENCE, "competitor-task"),
        _task(request, WorkerType.MARKET_INTELLIGENCE, "market-task"),
    ]
    state = _state(
        request,
        tasks,
        contradictions_for={
            WorkerType.COMPETITOR_INTELLIGENCE: ["First conflict.", "Second conflict."],
            WorkerType.MARKET_INTELLIGENCE: ["Market conflict."],
        },
    )

    amended = {t.worker: t for t in replan_node(state)["replan_tasks"]}

    assert set(amended) == {WorkerType.COMPETITOR_INTELLIGENCE, WorkerType.MARKET_INTELLIGENCE}
    assert "First conflict." in amended[WorkerType.COMPETITOR_INTELLIGENCE].goal
    assert "Second conflict." in amended[WorkerType.COMPETITOR_INTELLIGENCE].goal
    assert "Market conflict." not in amended[WorkerType.COMPETITOR_INTELLIGENCE].goal


def test_replan_falls_back_to_full_replan_when_no_worker_can_be_targeted(monkeypatch):
    """Verification can raise and set REPLAN_REQUIRED with no findings at all.

    There is then nothing to target, so the whole request is re-planned as
    before. Otherwise the graph would route to dispatch with zero tasks.
    """
    request = _request()
    tasks = [_task(request, WorkerType.COMPETITOR_INTELLIGENCE, "competitor-task")]
    state = _state(request, tasks, contradictions_for=None)

    calls = []

    def fake_plan(_self, _request):
        calls.append(1)
        return _plan(request, [_task(request, WorkerType.COMPETITOR_INTELLIGENCE, "fresh-task")])

    monkeypatch.setattr("edrak.orchestration.nodes.LlmPlanner.plan", fake_plan)

    new_state = replan_node(state)

    assert calls, "the fallback must re-plan through the LLM planner"
    assert [t.task_id for t in new_state["plan"].tasks] == ["fresh-task"]
    # Nothing to target, so no narrowed dispatch set is left behind.
    assert new_state["replan_tasks"] is None
    # The fallback writes RESET, which the reducer turns into an empty channel,
    # because the new plan carries new task ids that cannot match the old ones.
    assert new_state["results"] is RESET
    assert merge_by_task_id(state["results"], new_state["results"]) == []


def test_replan_falls_back_when_contradicting_worker_has_no_task(monkeypatch):
    """A contradiction from a worker absent from the plan must not dispatch nothing."""
    request = _request()
    task = _task(request, WorkerType.MARKET_INTELLIGENCE, "market-task")
    state = _state(
        request,
        [task],
        contradictions_for={WorkerType.COMPETITOR_INTELLIGENCE: ["Conflict."]},
    )
    monkeypatch.setattr(
        "edrak.orchestration.nodes.LlmPlanner.plan",
        lambda _self, _r: _plan(request, [task]),
    )

    new_state = replan_node(state)

    # Falls back rather than narrowing the dispatch to nothing.
    assert new_state["replan_tasks"] is None
    assert [t.task_id for t in new_state["plan"].tasks] == ["market-task"]
    assert new_state["results"] is RESET


def test_max_replans_is_one():
    assert Settings.model_fields["MAX_REPLANS"].default == 1


def test_graph_redispatches_only_the_conflicting_worker(monkeypatch):
    """End to end through the compiled graph, not just the node.

    Four workers run, verification reports a contradiction against one of them,
    and the replan must re-dispatch that worker alone. Counting dispatches per
    worker is the whole point: before this change every worker ran three times.
    """
    request = _request()
    tasks = [
        _task(request, WorkerType.COMPETITOR_INTELLIGENCE, "competitor-task"),
        _task(request, WorkerType.MARKET_INTELLIGENCE, "market-task"),
        _task(request, WorkerType.INTERNAL_INTELLIGENCE, "internal-task"),
    ]
    plan = _plan(request, tasks)
    contradiction = "Sources report different pricing or packaging for the same claim."

    verdict = FindingVerdict(
        finding_id="finding-competitor",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        statement="A claim two sources disagree on.",
        verification_status=FindingCheckStatus.VERIFIED,
        evidence_quality=EvidenceQuality.HIGH,
        contradictions=[contradiction],
        confidence=0.8,
    )
    decision = VerificationDecision(
        status=VerificationStatus.REPLAN_REQUIRED,
        summary="conflicting sources",
        targeted_actions=[
            TargetedAction(
                worker=WorkerType.COMPETITOR_INTELLIGENCE, reason=contradiction
            )
        ],
    )

    def fake_run_verification(_payload):
        return VerificationResult(
            research_run_id=request.request_id,
            decision=decision,
            findings=[verdict],
        )

    monkeypatch.setattr("edrak.verification.graph.run", fake_run_verification)

    dispatched: list[WorkerType] = []

    class _Worker:
        def __init__(self, worker_type):
            self.worker_type = worker_type

        def run(self, task):
            dispatched.append(task.worker)
            return WorkerResult(
                task_id=task.task_id,
                worker=task.worker,
                status=WorkerStatus.COMPLETED,
            )

    registry = WorkerRegistry({t.worker: _Worker(t.worker) for t in tasks})
    monkeypatch.setattr(
        orchestration_graph, "plan_node", lambda state: {**state, "plan": plan}
    )
    # The fallback path must not be reachable in this scenario.
    monkeypatch.setattr(
        "edrak.orchestration.nodes.LlmPlanner.plan",
        lambda _self, _r: pytest.fail("a targeted replan must not re-plan the request"),
    )
    monkeypatch.setattr(
        "edrak.CrossSignal.graph.run_cross_signal",
        _noop_cross_signal,
    )

    state = asyncio.run(
        orchestration_graph.build_graph(registry).ainvoke({"request": request})
    )

    assert dispatched.count(WorkerType.COMPETITOR_INTELLIGENCE) == 2
    assert dispatched.count(WorkerType.MARKET_INTELLIGENCE) == 1
    assert dispatched.count(WorkerType.INTERNAL_INTELLIGENCE) == 1
    assert len(dispatched) == 4
    # Every worker's findings survive: three results, not one.
    assert len(state["orchestration_result"].results) == 3
