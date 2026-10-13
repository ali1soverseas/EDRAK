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


# Sentinel telling a reducer channel to discard its current value. A plain string, not an object: the
# checkpointer serialises every write with msgpack, and a custom class made the save fail ("Type is not
# msgpack serializable: _Reset"), which surfaced later and failed the run at the review checkpoint.
RESET = "__edrak_reset_channel__"

T = TypeVar("T")


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
    if isinstance(right, str) and right == RESET:
        return []
    merged = {item.task_id: item for item in left}
    for item in right:
        old = merged.get(item.task_id)
        merged[item.task_id] = combine_rounds(old, item) if old is not None else item
    return list(merged.values())


def combine_rounds(old: Any, new: Any) -> Any:
    """A later round of the same task adds to the earlier one instead of replacing it.

    When a person (or verification) sends a worker out again, the new round's attempt is higher. Its
    findings and sources are added to what the first round found, so a weaker second round cannot erase a
    good first one. The same attempt (a stage enriching a result, such as the metrics agent) still replaces.
    """
    if not isinstance(new, WorkerResult) or not isinstance(old, WorkerResult) or new.attempt <= old.attempt:
        return new
    from ..metrics.requirements import merge_results

    merged = merge_results(old, new)
    seen: set[str] = set()
    findings = []
    for f in merged.findings:  # the same statement found twice is kept once (the earlier one)
        key = " ".join(f.statement.lower().split())
        if key not in seen:
            seen.add(key)
            findings.append(f)
    return merged.model_copy(update={"attempt": new.attempt, "findings": findings})


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
    # Why synthesis (cross-signal or decision analysis) produced nothing, shown on the run screen and the brief.
    synthesis_error: str | None
    status: str | None
    error: str | None
    orchestration_result: OrchestrationResult
    attempts_exceeded: bool
    # Checkpoint 2: the person's decision after verification ("continue", "replan" or "stop"),
    # an optional note, and whether the re-plan tasks are the person's own edits.
    review_decision: str | None
    review_note: str | None
    human_tasks: bool
