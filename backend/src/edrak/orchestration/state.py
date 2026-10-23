from __future__ import annotations

from typing import Annotated, Any, TypeVar, TypedDict

from ..contracts import (
    BusinessRequest,
    OrchestrationResult,
    ResearchPlan,
    ResearchTask,
    WorkerOutcome,
    WorkerResult,
)
from ..contracts.verification import VerificationDecision, VerificationResult


RESET = "__edrak_reset__"
"""Sentinel telling a reducer channel to discard its current value.

It is a plain string, not a custom object, because LangGraph's checkpointer
serializes pending channel writes with msgpack and rejects unknown types.
"""

T = TypeVar("T")


def _is_reset(value: object) -> bool:
    return isinstance(value, str) and value == RESET


def merge_by_task_id(left: list[T], right: list[T] | str) -> list[T]:
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
    if _is_reset(right):
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
    decision_analysis: dict[str, Any] | None
    replan_count: int
    status: str | None
    error: str | None
    orchestration_result: OrchestrationResult
    attempts_exceeded: bool
    # Checkpoint 2: the person's decision after verification ("continue", "replan" or "stop"),
    # an optional note, and whether the re-plan tasks are the person's own edits.
    review_decision: str | None
    review_note: str | None
    human_tasks: bool
