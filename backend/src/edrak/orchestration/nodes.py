from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from ..contracts import (
    BusinessRequest,
    OrchestrationResult,
    ResearchTask,
    RunStatus,
    WorkerRegistry,
    WorkerResult,
    WorkerStatus,
)
from ..contracts.verification import VerificationDecision, VerificationStatus
from ..contracts.worker import WorkerNotRegisteredError
from .planner import LlmPlanner, PlanningError
from .state import OrchestrationState


def plan_node(state: OrchestrationState) -> OrchestrationState:
    request: BusinessRequest = state["request"]
    plan = LlmPlanner().plan(request)
    return {**state, "plan": plan}


def run_task(task: ResearchTask, registry: WorkerRegistry) -> WorkerResult:
    """Run one task and always come back with a valid WorkerResult.

    Never raises. Every failure mode becomes a FAILED result for this task
    alone, so one broken worker cannot discard the other workers' findings.
    """
    try:
        worker = registry.get(task.worker)
    except WorkerNotRegisteredError as exc:
        return _failed(task, str(exc))

    try:
        return WorkerResult.model_validate(worker.run(task))
    except ValidationError as exc:
        return _failed(task, f"worker returned an invalid WorkerResult: {exc}")
    # A worker is third-party code. Any failure is contained, not propagated.
    except Exception as exc:  # noqa: BLE001
        return _failed(task, f"worker raised {type(exc).__name__}: {exc}")


def make_dispatch_worker(
    registry: WorkerRegistry,
) -> Callable[[dict], dict]:
    """Build the ``Send`` target that runs one task.

    Workers run concurrently, so this function must stay pure: it reads only its
    own payload and returns a partial state update. It deliberately does not
    receive the full state, which keeps one task from reading another's data.
    """

    def dispatch_worker(payload: dict) -> dict:
        result = run_task(payload["task"], registry)
        return {"results": [result], "outcomes": [result.to_outcome()]}

    return dispatch_worker


def _failed(task: ResearchTask, detail: str) -> WorkerResult:
    return WorkerResult(
        task_id=task.task_id,
        worker=task.worker,
        status=WorkerStatus.FAILED,
        attempt=task.attempt,
        error=detail,
    )


def verification_gate_node(state: OrchestrationState) -> OrchestrationState:
    return {
        **state,
        "verification": VerificationDecision(
            status=VerificationStatus.VERIFIED,
            summary="stub verification gate; always reports VERIFIED until the "
            "verification stage is implemented",
        ),
    }


def exhausted_node(state: OrchestrationState) -> OrchestrationState:
    return {
        **state,
        "error": "orchestration retry/replan limits exhausted; "
        "finalizing with partial results",
    }


def finalize_node(state: OrchestrationState) -> OrchestrationState:
    request: BusinessRequest = state["request"]
    plan = state.get("plan")
    results = state.get("results") or []
    error = state.get("error")

    any_failed = any(r.status is WorkerStatus.FAILED for r in results)
    overall_status = RunStatus.PARTIAL if (any_failed or error) else RunStatus.COMPLETED

    result = OrchestrationResult(
        request_id=request.request_id,
        status=overall_status,
        plan=plan,
        results=results,
        error=error,
    )
    return {
        **state,
        "status": overall_status.value,
        "orchestration_result": result,
    }


def replan_node(state: OrchestrationState) -> OrchestrationState:
    request: BusinessRequest = state["request"]
    replan_count = (state.get("replan_count") or 0) + 1
    plan = LlmPlanner().plan(request)
    return {
        **state,
        "plan": plan,
        "replan_count": replan_count,
        "results": [],
        "outcomes": [],
    }


__all__ = [
    "PlanningError",
    "exhausted_node",
    "finalize_node",
    "make_dispatch_worker",
    "plan_node",
    "replan_node",
    "run_task",
    "verification_gate_node",
]