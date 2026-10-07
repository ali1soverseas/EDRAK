from __future__ import annotations

from langgraph.types import Send

from ..contracts import WorkerType
from ..contracts.verification import VerificationStatus
from ..core.config import settings
from .state import OrchestrationState


class ConcurrentWorkerDispatchError(RuntimeError):
    """A plan assigns more than one task to the same worker.

    One worker instance serves every task of its type, so two concurrent tasks
    for the same worker would run inside that instance at the same time. Workers
    are not required to be re-entrant, so this is rejected up front rather than
    left to corrupt shared state silently.

    Raised before any dispatch happens, so it needs no locking.
    """

    def __init__(self, worker_type: WorkerType) -> None:
        self.worker_type = worker_type
        super().__init__(
            f"plan assigns more than one task to {worker_type.value!r}; "
            "dispatching one worker instance concurrently is not supported"
        )


def route_from_plan(state: OrchestrationState) -> list[Send] | str:
    """Fan out one Send per task so workers run in parallel.

    Also the single place the one-task-per-worker invariant is enforced.
    """
    plan = state.get("plan")
    tasks = plan.tasks if plan else []

    if not tasks:
        return "finalize_failed"

    seen: set[WorkerType] = set()
    for task in tasks:
        if task.worker in seen:
            raise ConcurrentWorkerDispatchError(task.worker)
        seen.add(task.worker)

    return [Send("dispatch", {"task": task}) for task in tasks]


def decide_after_verification(state: OrchestrationState) -> str:
    verification = state.get("verification")
    if verification is None:
        return "finalize"

    status = verification.status

    if status in (VerificationStatus.VERIFIED, VerificationStatus.CANNOT_COMPLETE):
        return "finalize"

    if status is VerificationStatus.RETRY_REQUIRED:
        if state.get("attempts_exceeded") or settings.MAX_TASK_ATTEMPTS < 1:
            return "exhausted"
        return "dispatch"

    if status is VerificationStatus.REPLAN_REQUIRED:
        if (state.get("replan_count") or 0) >= settings.MAX_REPLANS:
            return "exhausted"
        return "replan"

    return "finalize"