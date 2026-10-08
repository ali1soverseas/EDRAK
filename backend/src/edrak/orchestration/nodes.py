from __future__ import annotations

import contextlib
import io
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
from ..core.action_log import log_action
from .planner import LlmPlanner, PlanningError
from .state import RESET, OrchestrationState


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
        detail = f"verification stage raised {type(exc).__name__}: {exc}"
        print(f"  {detail}")
        return {
            **state,
            "verification": VerificationDecision(
                status=VerificationStatus.CANNOT_COMPLETE,
                summary=detail,
            ),
            "verification_result": None,
            "error": detail,
        }
    finally:
        captured = buffer.getvalue()

    if captured.strip():
        log_action("verification", captured)

    print(
        f"  verification: {result.decision.status.value} "
        f"({len(result.findings)} findings assessed) "
        f"-> artifacts/logs/verification.log"
    )

    return {
        **state,
        "verification": result.decision,
        "verification_result": result,
    }


async def cross_signal_node(
    state: OrchestrationState,
) -> OrchestrationState:

    from ..contracts.CrossSignal import build_cross_signal_input
    from ..CrossSignal.graph import run_cross_signal
    from ..contracts.verification import (
        FindingCheckStatus,
        VerificationStatus,
    )

    verification_result = state.get("verification_result")

    if verification_result is None:
        raise ValueError(
            "Cross-Signal requires the full verification result."
        )

    # Hard gate: Cross-Signal only runs after successful verification.
    if verification_result.decision.status is not VerificationStatus.VERIFIED:
        raise ValueError(
            "Cross-Signal can run only after successful verification."
        )

    # Only verified findings are allowed into Cross-Signal.
    verified_findings = [
        finding
        for finding in verification_result.findings
        if finding.verification_status is FindingCheckStatus.VERIFIED
    ]

    if not verified_findings:
        raise ValueError(
            "Verification completed but no verified findings "
            "are available for Cross-Signal."
        )

    # Build Cross-Signal input from the verified findings.
    cross_signal_input = build_cross_signal_input(
        verification_result,
        state["request"],
    )

    try:
        output = await run_cross_signal(cross_signal_input)

    except Exception as exc:  # noqa: BLE001
        detail = (
            f"Cross-Signal stage raised "
            f"{type(exc).__name__}: {exc}"
        )

        print(f"  {detail}")

        return {
            **state,
            "error": detail,
        }

    print(
        f"  cross-signal: {output.status} "
        f"({len(output.signals)} signals)"
    )

    return {
        **state,
        "cross_signal_output": output.model_dump(mode="json"),
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
        cross_signal=state.get("cross_signal_output"),
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
        "results": RESET,
        "outcomes": RESET,
    }


__all__ = [
    "PlanningError",
    "exhausted_node",
    "cross_signal_node",
    "finalize_node",
    "make_dispatch_worker",
    "plan_node",
    "replan_node",
    "run_task",
    "verification_gate_node",
]