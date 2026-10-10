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

    Also the single place where the one-task-per-worker invariant is enforced.

    A targeted replan narrows the dispatch to the workers verification found
    contradictory by setting ``replan_tasks``. The plan itself keeps every task,
    so results from the workers that were not re-run still belong to it.
    """
    pending = state.get("replan_tasks")
    if pending:
        tasks = pending
    else:
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

    if status is VerificationStatus.VERIFIED:
        return "cross_signal"
    if status is VerificationStatus.CANNOT_COMPLETE:
        return "finalize"

    if status is VerificationStatus.RETRY_REQUIRED:
        if state.get("attempts_exceeded") or settings.MAX_TASK_ATTEMPTS < 1:
            return "exhausted"
        return "dispatch"

    if status is VerificationStatus.REPLAN_REQUIRED:
        if (state.get("replan_count") or 0) >= settings.MAX_REPLANS:
            # Out of replans, but not out of signal. One contradictory finding
            # used to send the whole run straight to finalize, which threw away
            # cross_signal and decision_analysis even though the remaining
            # findings were verified and usable. Both stages only ever receive
            # VERIFIED findings, so they are safe to run on a partial set; the
            # contract gate accepts REPLAN_REQUIRED when at least one finding
            # passed. CANNOT_COMPLETE keeps going to finalize, because that
            # status means nothing verified at all.
            return "cross_signal"
        return "replan"

    return "finalize"