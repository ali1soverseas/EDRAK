from __future__ import annotations

import contextlib
import io
import json
import logging
from collections.abc import Callable
from typing import Any

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
from ..contracts.CrossSignal import CrossSignalOutput
from ..contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
)
from ..contracts.verification import VerificationDecision, VerificationStatus
from ..contracts.worker import WorkerNotRegisteredError
from ..core.action_log import log_action
from ..DecisionAnalysis.service import run_decision_analysis
from .planner import LlmPlanner, PlanningError
from .state import RESET, OrchestrationState

logger = logging.getLogger(__name__)


def make_decision_analysis_node():
    """
    Create the orchestrator's Decision Analysis node.

    Expected input:
        state["request"]
        state["cross_signal"]

    Output:
        state["decision_analysis"]
    """
    print("\n[orchestrator] NODE: decision_analysis")
    async def decision_analysis_node(
        state: OrchestrationState,
    ) -> dict[str, Any]:
        raw_cross_signal = state.get("cross_signal")
        request = state.get("request")
    
        if not raw_cross_signal:
            message = (
                "Decision Analysis skipped: Cross-Signal output "
                "is missing."
            )

            logger.warning(message)

            return {
                "decision_analysis": None,
                "error": message,
            }

        if request is None:
            message = (
                "Decision Analysis skipped: business request "
                "is missing."
            )

            logger.error(message)

            return {
                "decision_analysis": None,
                "error": message,
            }

        try:
            if isinstance(raw_cross_signal, CrossSignalOutput):
                cross_signal = raw_cross_signal
            else:
                cross_signal = CrossSignalOutput.model_validate(
                    raw_cross_signal
                )

            if cross_signal.status.lower() != "completed":
                raise ValueError(
                    "Cross-Signal did not complete successfully. "
                    f"Received status: {cross_signal.status!r}"
                )

            decision_input = DecisionAnalysisInput(
                research_run_id=cross_signal.research_run_id,
                business_request=request,
                cross_signal=cross_signal,
                metadata={
                    "source_stage": "cross_signal",
                    "orchestrator_stage": "decision_analysis",
                },
            )
            print(
                f"  [decision_analysis] running for run_id={decision_input.research_run_id}"
            )
            result = await run_decision_analysis(
                decision_input=decision_input
            )

            dumped_result = result.model_dump(mode="json")

            logger.info(
                "Decision Analysis finished for run %s with status %s",
                result.research_run_id,
                result.status.value,
            )

            return {
                "decision_analysis": dumped_result,
            }

        except Exception as exc:
            logger.exception(
                "Decision Analysis failed."
            )

            return {
                "decision_analysis": None,
                "error": (
                    "Decision Analysis failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            }

    return decision_analysis_node


def plan_node(state: OrchestrationState) -> OrchestrationState:
    print("\n[orchestrator] NODE: plan")
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
        task = payload["task"]
        print(f"\n[{task.worker.value}] NODE: dispatch")
        result = run_task(task, registry)
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
    print("\n[verification] NODE: verify")
    from ..contracts.verification import VerificationInput
    from ..verification.graph import run as run_verification

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            result = run_verification(
                VerificationInput(
                    request=state["request"],
                    agent_outputs=state.get("results") or [],
                )
            )
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline here
        return {
            **state,
            "verification": VerificationDecision(
                status=VerificationStatus.REPLAN_REQUIRED,
                summary=f"verification stage raised {type(exc).__name__}: {exc}",
            ),
        }
    finally:
        captured = buffer.getvalue()

    if captured.strip():
        log_action("verification", captured)

    print(
        f"  [verification] {result.decision.status.value} "
        f"({len(result.findings)} findings assessed) "
        f"-> artifacts/logs/verification.log"
    )

    return {
        **state,
        "verification": result.decision,
        "verification_result": result,
    }


async def cross_signal_node(state: OrchestrationState) -> OrchestrationState:
    print("\n[cross_signal] NODE: cross_signal")
    from ..contracts.CrossSignal import build_cross_signal_input
    from ..contracts.verification import FindingCheckStatus, VerificationResult
    from ..CrossSignal.graph import run_cross_signal

    raw = state.get("verification_result")
    if raw is None:
        print("  [cross_signal] skipped: no verification result")
        return {**state, "cross_signal": None}

    try:
        payload = (
            raw
            if isinstance(raw, VerificationResult)
            else VerificationResult.model_validate(raw)
        )
        # The contract builder rejects a payload that still contains
        # insufficient findings. Keep the verified run, drop the rest.
        verified_only = payload.model_copy(
            update={
                "findings": [
                    item
                    for item in payload.findings
                    if item.verification_status is FindingCheckStatus.VERIFIED
                ]
            }
        )
        cs_input = build_cross_signal_input(verified_only, state["request"])
        output = await run_cross_signal(cs_input)
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline here
        print(f"  [cross_signal] failed: {type(exc).__name__}: {exc}")
        return {**state, "cross_signal": None}

    dumped = output.model_dump(mode="json")
    print("\n" + "=" * 72)
    print("[cross_signal] OUTPUT")
    print("=" * 72)
    print(json.dumps(dumped, indent=2, default=str))
    print(
        f"  [cross_signal] {dumped.get('status', 'unknown')} "
        f"({len(dumped.get('signals') or [])} signals)"
    )
    return {**state, "cross_signal": dumped}


def exhausted_node(state: OrchestrationState) -> OrchestrationState:
    print("\n[orchestrator] NODE: exhausted")
    return {
        **state,
        "error": "orchestration retry/replan limits exhausted; "
        "finalizing with partial results",
    }


def finalize_node(state: OrchestrationState) -> OrchestrationState:
    print("\n[orchestrator] NODE: finalize")
    request: BusinessRequest = state["request"]
    plan = state.get("plan")
    results = state.get("results") or []
    error = state.get("error")
    decision_analysis = state.get("decision_analysis")

    any_failed = any(
        r.status is WorkerStatus.FAILED
        for r in results
    )

    decision_analysis_failed = (
        decision_analysis is None
        and bool(state.get("cross_signal"))
    )

    overall_status = (
        RunStatus.PARTIAL
        if any_failed or error or decision_analysis_failed
        else RunStatus.COMPLETED
    )

    result = OrchestrationResult(
        request_id=request.request_id,
        status=overall_status,
        plan=plan,
        results=results,
        cross_signal=state.get("cross_signal"),
        decision_analysis=decision_analysis,
        error=(
            error
            or (
                state.get("error")
                if decision_analysis_failed
                else None
            )
        ),
    )

    return {
        **state,
        "status": overall_status.value,
        "orchestration_result": result,
    }

def _targeted_tasks(
    state: OrchestrationState,
) -> tuple[list[ResearchTask], dict[str, list[str]]] | None:
    """Re-task only the workers whose findings verification found contradictory.

    Returns the amended tasks and the reasons per worker, or None when no worker
    can be identified, which sends the caller back to a full replan.

    The task keeps its original ``task_id`` on purpose. ``merge_by_task_id``
    replaces a result when the same task id comes back, so re-dispatching these
    tasks overwrites exactly these workers' results and leaves every other
    worker's findings in place.
    """
    raw = state.get("verification_result")
    if raw is None:
        return None

    findings = getattr(raw, "findings", None)
    if findings is None and isinstance(raw, dict):
        findings = raw.get("findings")
    if not findings:
        return None

    reasons: dict[str, list[str]] = {}
    for verdict in findings:
        contradictions = (
            verdict.get("contradictions")
            if isinstance(verdict, dict)
            else getattr(verdict, "contradictions", None)
        )
        worker = (
            verdict.get("worker")
            if isinstance(verdict, dict)
            else getattr(verdict, "worker", None)
        )
        if not contradictions or worker is None:
            continue
        key = getattr(worker, "value", worker)
        bucket = reasons.setdefault(str(key), [])
        for item in contradictions:
            text = str(item).strip()
            if text and text not in bucket:
                bucket.append(text)

    if not reasons:
        return None

    plan = state.get("plan")
    if plan is None:
        return None

    amended: list[ResearchTask] = []
    for task in plan.tasks:
        worker_key = getattr(task.worker, "value", task.worker)
        wanted = reasons.get(str(worker_key))
        if not wanted:
            continue
        amended.append(
            task.model_copy(
                update={
                    "goal": _amended_goal(task, wanted),
                    "focus": _amended_focus(task, wanted),
                    "attempt": task.attempt + 1,
                }
            )
        )

    return (amended, reasons) if amended else None


def _amended_goal(task: ResearchTask, reasons: list[str]) -> str:
    detail = "; ".join(reasons)
    return (
        f"{task.goal} Re-check the conflicting evidence for this assignment: {detail}."
    )


def _amended_focus(task: ResearchTask, reasons: list[str]) -> str:
    detail = "; ".join(reasons)
    return f"{task.focus} Resolve the contradiction first: {detail}."


def replan_node(state: OrchestrationState) -> OrchestrationState:
    print("\n[orchestrator] NODE: replan")
    request: BusinessRequest = state["request"]
    replan_count = (state.get("replan_count") or 0) + 1

    targeted = _targeted_tasks(state)
    if targeted is not None:
        tasks, reasons = targeted
        plan = state["plan"]
        print(
            f"  targeted replan {replan_count}: re-dispatching "
            f"{len(tasks)} of {len(plan.tasks)} worker(s): "
            + ", ".join(str(getattr(t.worker, "value", t.worker)) for t in tasks)
        )
        for worker, items in reasons.items():
            print(f"    {worker}: {len(items)} contradiction(s) to resolve")
        # The plan is left intact on purpose. Its tasks keep their original ids,
        # so merge_by_task_id swaps in these results and the results of the
        # workers that were not re-run stay valid against the plan.
        return {
            **state,
            "replan_tasks": tasks,
            "replan_count": replan_count,
        }

    # No worker could be identified, which happens when the verification stage
    # itself raised and set REPLAN_REQUIRED without any findings. Re-plan from
    # the request as before, otherwise there would be nothing to dispatch.
    print("  no conflicting worker identified: replanning the whole request")
    plan = LlmPlanner().plan(request)
    return {
        **state,
        "plan": plan,
        "replan_tasks": None,
        "replan_count": replan_count,
        "results": RESET,
        "outcomes": RESET,
    }


__all__ = [
    "PlanningError",
    "cross_signal_node",
    "exhausted_node",
    "finalize_node",
    "make_dispatch_worker",
    "plan_node",
    "replan_node",
    "run_task",
    "verification_gate_node",
]