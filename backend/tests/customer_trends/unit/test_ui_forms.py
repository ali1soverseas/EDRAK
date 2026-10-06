from datetime import date
from typing import Any

import pytest

from edrak.agents.customer_trends.schemas.common import UseCase
from edrak.agents.customer_trends.ui.components import forms
from edrak.contracts import ResearchTask, WorkerType
from edrak.contracts import UseCase as SharedUseCase
from tests.customer_trends.factories import load_brief


def test_the_presets_are_the_three_sample_briefs_with_the_gitlab_pilot_first() -> None:
    presets = forms.load_presets()
    assert list(presets) == [
        "GitLab pilot: competitive intelligence",
        "Product launch: budgeting app for freelancers in Egypt",
        "Market entry: specialty coffee delivery in Saudi Arabia",
    ]
    first = presets["GitLab pilot: competitive intelligence"]
    assert first.use_case is UseCase.COMPETITIVE_INTELLIGENCE and first.entity == "GitLab"
    assert "GitHub Copilot" in first.competitors and set(first.languages) == {"ar", "en"}
    assert all(preset.languages for preset in presets.values())


def test_presets_that_are_missing_are_left_out(tmp_path: Any) -> None:
    assert forms.load_presets(tmp_path) == {}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a, b,c", ["a", "b", "c"]),
        ("a\nb , a", ["a", "b"]),
        ("  ,, ", []),
        ("", []),
        ("GitHub Copilot, Atlassian", ["GitHub Copilot", "Atlassian"]),
    ],
)
def test_lists_are_split_on_commas_and_new_lines(text: str, expected: list[str]) -> None:
    assert forms.parse_list(text) == expected


@pytest.mark.parametrize("use_case", ["competitive_intelligence", "product_launch", "market_entry"])
def test_a_brief_survives_the_trip_through_the_form(use_case: str) -> None:
    brief = load_brief(use_case)
    rebuilt, problems = forms.brief_from_form(forms.form_values(brief))
    assert problems == [] and rebuilt == brief


def test_the_form_values_of_a_brief_split_standard_and_extra_languages() -> None:
    brief = load_brief().model_copy(update={"languages": ["en", "ar", "pt"]})
    values = forms.form_values(brief)
    assert values["languages"] == ["en", "ar"] and values["extra_languages"] == "pt"
    assert forms.brief_from_form(values)[0] == brief


def valid_values(**overrides: Any) -> dict[str, Any]:
    return {**forms.form_values(load_brief()), **overrides}


def test_blank_optional_fields_become_none_and_codes_are_normalized() -> None:
    brief, problems = forms.brief_from_form(
        valid_values(market="  ", geo="eg", notes="", extra_languages="AR, fr", languages=["en"])
    )
    assert problems == [] and brief is not None
    assert (brief.market, brief.geo, brief.notes) == (None, "EG", None)
    assert brief.languages == ["en", "ar", "fr"]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"entity": "  "}, "entity: String should have at least 1 character"),
        ({"question": ""}, "question: String should have at least 1 character"),
        ({"geo": "Egypt"}, "geo: "),
        ({"languages": [], "extra_languages": ""}, "languages: "),
        ({"since": date(2026, 9, 1), "until": date(2026, 1, 1)}, "since must not be after until"),
        ({"use_case": "world_peace"}, "use_case: "),
        ({"max_tool_calls": 0}, "budget.max_tool_calls: "),
        ({"run_id": "no spaces allowed"}, "run_id: "),
    ],
)
def test_a_form_that_cannot_make_a_brief_says_which_field_and_why(
    overrides: dict[str, Any], expected: str
) -> None:
    brief, problems = forms.brief_from_form(valid_values(**overrides))
    assert brief is None
    assert any(expected in problem for problem in problems), problems


def test_pasted_json_is_checked_with_readable_errors() -> None:
    good = load_brief().model_dump_json()
    assert forms.brief_from_json(good) == (load_brief(), [])
    brief, problems = forms.brief_from_json('{"task_id": "t",')
    assert brief is None and problems[0].startswith("not valid JSON: ") and "line 1" in problems[0]
    brief, problems = forms.brief_from_json('{"task_id": "t", "run_id": "r", "depth": "huge"}')
    assert brief is None
    assert any(p.startswith("depth: ") for p in problems) and any(
        p.startswith("entity: ") for p in problems
    )
    brief, problems = forms.brief_from_json("[1, 2]")
    assert brief is None and problems


def test_a_repeated_run_id_gets_the_next_free_suffix() -> None:
    assert forms.unique_run_id("run-1", set()) == "run-1"
    assert forms.unique_run_id("run-1", {"run-1"}) == "run-1-2"
    assert forms.unique_run_id("run-1", {"run-1", "run-1-2", "run-1-3"}) == "run-1-4"
    assert forms.unique_run_id("run-1", {"run-2"}) == "run-1"


@pytest.mark.parametrize(
    ("use_case", "shared"),
    [
        ("competitive_intelligence", SharedUseCase.COMPETITIVE_INTELLIGENCE),
        ("product_launch", SharedUseCase.PRODUCT_LAUNCH),
        ("market_entry", SharedUseCase.MARKET_ENTRY_EXPANSION),
    ],
)
def test_a_brief_has_a_shared_task_the_worker_result_can_be_built_for(
    use_case: str, shared: SharedUseCase
) -> None:
    brief = load_brief(use_case)
    task = forms.task_from_brief(brief)
    assert isinstance(task, ResearchTask) and task.worker is WorkerType.CUSTOMER_TRENDS
    assert task.task_id == brief.task_id and task.goal == brief.question
    assert task.business_context.use_case is shared and task.company_profile.name == brief.entity
    assert task.business_context.targets == brief.competitors
