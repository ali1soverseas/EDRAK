import json

from edrak.agents.customer_trends.gaps import Gap
from edrak.agents.customer_trends.schemas.task import QueryPlan
from edrak.agents.customer_trends.state import (
    brief_of,
    gaps_of,
    initial_state,
    merge_batches,
    merge_by_key,
    plan_of,
    union_gaps,
    union_strings,
)
from tests.customer_trends.factories import load_brief


def gap(gap_id: str, description: str = "d", severity: str = "critical") -> dict[str, str]:
    return {"id": gap_id, "severity": severity, "description": description, "suggested_action": "a"}


def test_batches_merge_by_branch_and_a_branch_that_runs_again_keeps_both_passes() -> None:
    left = {"social": ["b1", "b2"], "demand": ["b3"]}
    right = {"social": ["b2", "b4"], "reviews": []}
    assert merge_batches(left, right) == {
        "social": ["b1", "b2", "b4"],
        "demand": ["b3"],
        "reviews": [],
    }
    assert left == {"social": ["b1", "b2"], "demand": ["b3"]}, "the inputs are not changed"


def test_notes_and_errors_take_the_newest_entry_of_a_branch() -> None:
    assert merge_by_key({"social": "old", "demand": "x"}, {"social": ""}) == {
        "social": "",
        "demand": "x",
    }


def test_gaps_merge_by_id_keeping_position_and_taking_the_newer_record() -> None:
    merged = union_gaps([gap("a", "first"), gap("b")], [gap("c"), gap("a", "second")])
    assert [g["id"] for g in merged] == ["a", "b", "c"]
    assert merged[0]["description"] == "second"


def test_strings_merge_without_repeats_in_order() -> None:
    assert union_strings(["a", "b"], ["b", "c", "a"]) == ["a", "b", "c"]


def test_the_state_is_plain_json_that_turns_back_into_models() -> None:
    brief = load_brief()
    state = initial_state(brief)
    json.dumps(state)
    assert brief_of(state) == brief
    assert plan_of(state) is None
    state["plan"] = QueryPlan(rationale="r").model_dump(mode="json")
    state["gaps"] = [gap("x")]
    json.dumps(state)
    assert plan_of(state) == QueryPlan(rationale="r")
    assert gaps_of(state) == [Gap.model_validate(gap("x"))]
    assert gaps_of({}) == []
