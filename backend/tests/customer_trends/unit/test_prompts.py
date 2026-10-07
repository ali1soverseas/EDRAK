import string
from datetime import date

import pytest

from edrak.agents.customer_trends import prompts
from edrak.agents.customer_trends.prompts import (
    ANALYZE_THEMES,
    BRANCH_DEMAND,
    BRANCH_REVIEWS,
    BRANCH_SOCIAL,
    PLAN_QUERIES,
    REPAIR_FINDINGS,
    WRITE_FINDINGS,
    WRITE_HEADLINE,
    render,
)

KNOWN_PLACEHOLDERS = {
    "entity", "question", "market", "geo", "languages", "competitors", "focus", "since",
    "until", "depth", "emphasis", "query_hints", "max_steps", "target_items", "replan",
}  # fmt: skip
ALL = {
    "PLAN_QUERIES": PLAN_QUERIES,
    "BRANCH_SOCIAL": BRANCH_SOCIAL,
    "BRANCH_DEMAND": BRANCH_DEMAND,
    "BRANCH_REVIEWS": BRANCH_REVIEWS,
    "WRITE_FINDINGS": WRITE_FINDINGS,
    "REPAIR_FINDINGS": REPAIR_FINDINGS,
    "WRITE_HEADLINE": WRITE_HEADLINE,
}
BRANCHES = {
    "BRANCH_SOCIAL": BRANCH_SOCIAL,
    "BRANCH_DEMAND": BRANCH_DEMAND,
    "BRANCH_REVIEWS": BRANCH_REVIEWS,
}


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_render_fills_known_names_and_leaves_missing_ones_empty() -> None:
    assert render("{a} and {b} and {c}", {"a": "x", "b": "y"}) == "x and y and "


def test_render_shows_unset_values_and_formats_lists_dates_and_enums() -> None:
    from edrak.agents.customer_trends.schemas.common import Depth

    text = render(
        "{geo}|{languages}|{since}|{depth}|{empty}|{blank}",
        {
            "geo": None,
            "languages": ["ar", "en"],
            "since": date(2026, 4, 1),
            "depth": Depth.DEEP,
            "empty": [],
            "blank": "",
        },
    )
    assert text == "not set|ar, en|2026-04-01|deep|not set|not set"


def test_render_does_not_read_braces_inside_values() -> None:
    assert (
        render("{entity}", {"entity": "{question} {0} {x.__class__}"})
        == "{question} {0} {x.__class__}"
    )


def test_doubled_braces_in_a_template_become_single_braces() -> None:
    assert '{"findings": [...]}' in render(WRITE_FINDINGS, {})
    assert '{"headline": "..."}' in render(WRITE_HEADLINE, {})


@pytest.mark.parametrize("name", list(ALL))
def test_a_prompt_only_uses_known_placeholders_and_renders_without_error(name: str) -> None:
    template = ALL[name]
    assert placeholders(template) <= KNOWN_PLACEHOLDERS
    rendered = render(template, {"entity": "GitLab", "question": "Q?", "languages": ["en", "ar"]})
    assert "{entity}" not in rendered and "{question}" not in rendered
    assert "GitLab" in rendered or name in {"REPAIR_FINDINGS", "WRITE_HEADLINE"}


@pytest.mark.parametrize("name", list(ALL))
def test_every_prompt_states_the_decision_support_stance(name: str) -> None:
    text = ALL[name].lower()
    assert "you support a human decision" in text
    assert "never recommend" in text and "verdict" in text


@pytest.mark.parametrize("name", list(BRANCHES))
def test_a_branch_prompt_states_the_brief_the_tools_the_step_budget_and_the_stop_rule(
    name: str,
) -> None:
    text = render(BRANCHES[name], {"max_steps": 8, "target_items": 30})
    for field in (
        "Entity under study",
        "Business question",
        "Country",
        "Languages",
        "Competitors",
        "Focus",
        "Period",
        "Depth",
        "Use case emphasis",
    ):
        assert field in text, field
    assert "at most 8 model turns" in text
    assert "pointers and short previews only" in text
    assert "Arabic and an English variant" in text and "local-dialect" in text
    assert "Stop when coverage is adequate" in text and "step cap" in text
    assert "about 30 items" in text
    assert "Never invent a URL, an id or a number" in text
    assert "no verdicts" in text


def test_each_branch_names_only_its_own_tools() -> None:
    assert all(t in BRANCH_SOCIAL for t in ("social_search", "social_comments", "web_search"))
    assert "reviews_fetch" not in BRANCH_SOCIAL and "search_interest" not in BRANCH_SOCIAL
    assert all(t in BRANCH_DEMAND for t in ("search_interest", "news_coverage", "web_search"))
    assert "social_search" not in BRANCH_DEMAND
    assert all(t in BRANCH_REVIEWS for t in ("reviews_fetch", "web_search", "fetch_page"))
    assert "social_search" not in BRANCH_REVIEWS


def test_the_planner_prompt_asks_for_dialect_five_keywords_and_no_guessed_ids() -> None:
    text = PLAN_QUERIES
    assert "at most 5" in text and "local-dialect" in text
    assert "never guess one" in text
    assert "{replan}" in text
    assert "second pass" in prompts.REPLAN_NOTE and "{gaps}" in prompts.REPLAN_NOTE
    assert "{done}" in prompts.REPLAN_NOTE and "2 to 4 words" in text


def test_the_writer_prompt_says_what_growth_and_share_of_voice_measure() -> None:
    text = WRITE_FINDINGS
    assert "describes this sample only" in text and "not search interest" in text
    assert "not market share" in text
    assert "related_gaps is at most" in text.replace("\n  ", " ")


def test_the_writer_prompt_sets_the_citation_number_and_confidence_rules() -> None:
    text = WRITE_FINDINGS
    for rule in (
        "one or two sentences",
        "only ids that appear in CONTEXT",
        "Never invent, shorten or change an id",
        "every number written in the claim",
        "At least 2 platforms or source types".lower(),
        "10 or",
        "caveats",
        "related_gaps",
        "should enter",
    ):
        assert rule.lower() in text.lower(), rule


def test_the_repair_prompt_covers_each_rejection_reason() -> None:
    text = REPAIR_FINDINGS.lower()
    for reason in ("evidence id", "number that is not in metrics", "verdict language", "metric_id"):
        assert reason in text, reason
    assert "do not add new findings" in text


def test_no_prompt_contains_an_em_dash() -> None:
    for name, text in {
        **ALL,
        "ANALYZE_THEMES": ANALYZE_THEMES,
        "REPLAN_NOTE": prompts.REPLAN_NOTE,
    }.items():
        assert chr(0x2014) not in text, name
