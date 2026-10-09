from __future__ import annotations

from typing import Annotated, Any, TypedDict

from ..contracts import (
    BusinessRequest,
    OrchestrationResult,
    ResearchPlan,
    ResearchTask,
    WorkerOutcome,
    WorkerResult,
)
from ..contracts.verification import VerificationDecision, VerificationResult


class _Reset:
    """Sentinel telling a reducer channel to discard its current value."""


RESET = _Reset()


def merge_by_task_id[T](left: list[T], right: list[T] | _Reset) -> list[T]:
    """Reducer for parallel dispatch.

    LangGraph merges the simultaneous writes from every dispatched worker
    through this function. Keying on ``task_id`` means a retried task replaces
    its earlier attempt instead of accumulating beside it, so downstream stages
    always see exactly one result per task.

    Ordering is not guaranteed and must not be relied on.

    Because writes are merged, returning ``[]`` appends nothing and leaves the
    previous value intact. A node that genuinely needs to clear the channel (a
    replan, whose new plan carries new task ids) must write :data:`RESET`.
    """
    if isinstance(right, _Reset):
        return []
    merged = {item.task_id: item for item in left}
    merged.update({item.task_id: item for item in right})
    return list(merged.values())


class OrchestrationState(TypedDict, total=False):
    request: BusinessRequest
    plan: ResearchPlan | None
    # The subset of ``plan.tasks`` a targeted replan wants re-dispatched. Kept
    # apart from ``plan`` because the plan must keep listing every task whose
    # result is in the channel: OrchestrationResult rejects a result whose
    # task_id is absent from the plan.
    replan_tasks: list[ResearchTask] | None
    results: Annotated[list[WorkerResult], merge_by_task_id]
    outcomes: Annotated[list[WorkerOutcome], merge_by_task_id]
    verification: VerificationDecision | None
    verification_result: VerificationResult | None
    cross_signal: dict[str, Any] | None
    replan_count: int
    status: str | None
    error: str | None
    orchestration_result: OrchestrationResult
    attempts_exceeded: bool