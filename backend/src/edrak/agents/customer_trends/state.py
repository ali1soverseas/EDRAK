"""Graph state: pointers and small summaries only, never evidence records (SPEC section 10).

Every value is plain JSON data (dicts, lists, strings, numbers) so the SQLite checkpointer can
store it without registering classes. The helpers at the bottom turn a value back into its model.
"""

import operator
from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

from edrak.agents.customer_trends.gaps import Gap
from edrak.agents.customer_trends.schemas.task import QueryPlan, TaskBrief

BRANCHES = ("social", "demand", "reviews")


def merge_batches(left: dict[str, list[str]], right: dict[str, list[str]]) -> dict[str, list[str]]:
    """Batch ids per branch; a branch that runs twice (a replan) keeps both passes."""
    merged = {name: list(ids) for name, ids in left.items()}
    for name, ids in right.items():
        known = merged.setdefault(name, [])
        known.extend(batch_id for batch_id in ids if batch_id not in known)
    return merged


def merge_by_key(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """Branch notes and errors: the newest entry of a branch wins."""
    return {**left, **right}


def union_gaps(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gap records by id; a repeated id keeps its place and takes the newer record."""
    merged = {gap["id"]: gap for gap in left}
    for gap in right:
        merged[gap["id"]] = gap
    return list(merged.values())


def union_strings(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


class WorkerState(TypedDict, total=False):
    brief: dict[str, Any]
    plan: dict[str, Any] | None
    batches: Annotated[dict[str, list[str]], merge_batches]
    branch_notes: Annotated[dict[str, str], merge_by_key]
    branch_errors: Annotated[dict[str, str], merge_by_key]
    gaps: Annotated[list[dict[str, Any]], union_gaps]
    warnings: Annotated[list[str], union_strings]
    replan_count: int
    analysis_done: bool
    metric_ids: dict[str, str]
    findings: list[dict[str, Any]]
    result: dict[str, Any] | None
    budget: dict[str, Any]
    events: Annotated[list[dict[str, Any]], operator.add]


def initial_state(brief: TaskBrief) -> WorkerState:
    return {"brief": brief.model_dump(mode="json")}


def brief_of(state: Mapping[str, Any]) -> TaskBrief:
    return TaskBrief.model_validate(state["brief"])


def plan_of(state: Mapping[str, Any]) -> QueryPlan | None:
    plan = state.get("plan")
    return QueryPlan.model_validate(plan) if plan else None


def gaps_of(state: Mapping[str, Any]) -> list[Gap]:
    return [Gap.model_validate(gap) for gap in state.get("gaps", [])]
