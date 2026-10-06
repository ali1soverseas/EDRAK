import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from edrak.agents.customer_trends.llm import demo_script
from edrak.agents.customer_trends.llm.demo_script import (
    DEMO_THEMES,
    analyst_answer,
    branch_turns,
    demo_llm,
    demo_plan,
    findings_from_context,
    headline_from_context,
    writer_answer,
)
from edrak.agents.customer_trends.providers.base import ProviderResult
from edrak.agents.customer_trends.runner import DEMO_FIXTURES_RELATIVE, make_deps
from edrak.agents.customer_trends.schemas.findings import Finding, FindingsDraft, HeadlineDraft
from edrak.agents.customer_trends.schemas.task import PlanDraft
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools.submit_findings import claim_numbers, matches, own_numbers
from edrak.agents.customer_trends.utils.text import detect_language
from tests.customer_trends.factories import BRIEFS, load_brief
from tests.customer_trends.graph_helpers import scenario_settings

REPO = BRIEFS.parents[3]
FIXTURES = REPO / "backend" / DEMO_FIXTURES_RELATIVE.relative_to("backend")
BUILDER = REPO / "scripts" / "customer_trends" / "build_demo_fixtures.py"


def test_the_demo_phrases_are_distinct_and_in_their_language() -> None:
    labels = [t.label for t in DEMO_THEMES]
    assert len(set(labels)) == len(labels) == 8
    phrases = [p for t in DEMO_THEMES for p in (t.english, t.arabic)]
    assert len(set(phrases)) == len(phrases)
    assert all(detect_language(t.english) == "en" for t in DEMO_THEMES)
    assert all(detect_language(t.arabic) == "ar" for t in DEMO_THEMES)
    assert all(t.sentiment in {"positive", "neutral", "negative", "mixed"} for t in DEMO_THEMES)
    assert chr(0x2014) not in json.dumps(DEMO_THEMES, ensure_ascii=False)


@pytest.mark.parametrize("use_case", ["competitive_intelligence", "product_launch", "market_entry"])
def test_the_demo_plan_is_a_valid_plan_built_from_the_brief(use_case: str) -> None:
    brief = load_brief(use_case)
    plan = PlanDraft.model_validate(demo_plan(brief)).to_plan()
    assert brief.entity in plan.social_queries["reddit"][0]
    assert set(plan.social_queries) == {"reddit", "x", "youtube"}
    assert plan.trend_keywords[0] == brief.entity and len(plan.trend_keywords) <= 5
    assert bool(plan.review_targets) is bool(brief.competitors)


def test_the_analyst_labels_items_by_the_phrase_they_contain() -> None:
    theme = DEMO_THEMES[1]
    items = [
        {"id": "a" * 16, "platform": "reddit", "text": f"{theme.english} (reddit 3)"},
        {"id": "b" * 16, "platform": "x", "text": f"{theme.arabic} (x 4)"},
        {"id": "c" * 16, "platform": "x", "text": "Nothing the demo knows"},
    ]
    human = "Tasks wanted: themes\nTaxonomy: none\nItems:\n" + json.dumps(items, ensure_ascii=False)
    answer = analyst_answer([SystemMessage(content="s"), HumanMessage(content=human)])
    assert isinstance(answer, dict)
    assert [i["themes"] for i in answer["items"]] == [[theme.label], [theme.label], []]
    assert answer["items"][0]["sentiment"] == theme.sentiment
    assert answer["items"][2]["sentiment"] == "neutral"


def a_context() -> dict[str, Any]:
    ids = [f"{n:016x}" for n in range(14)]
    return {
        "coverage": {"evidence_items": 98, "by_platform": {"reddit": 30, "x": 25, "unknown": 40}},
        "themes": [
            {
                "label": "slow customer support",
                "count": 14,
                "share": 0.156,
                "sentiment_mix": {"negative": 1.0},
                "evidence_ids": ids[:9],
            },
            {
                "label": "easy to use",
                "count": 11,
                "share": 0.1222,
                "sentiment_mix": {"positive": 1.0},
                "evidence_ids": ids[5:],
            },
        ],
        "metrics": [
            {
                "name": "platform_mix",
                "metric_id": "m_abc",
                "values": {"percent": {"reddit": 31.2, "x": 25.5}},
            }
        ],
        "trends": [
            {"keyword": "pricing", "first": 40.0, "last": 41.0, "evidence_id": ids[0]},
            {"keyword": "no id", "first": 1.0, "last": 2.0, "evidence_id": None},
        ],
    }


def test_the_demo_findings_state_only_numbers_that_are_in_their_metrics() -> None:
    findings = findings_from_context(a_context())
    assert [f["id"] for f in findings] == ["f1", "f2", "f3", "f4"]
    parsed = FindingsDraft.model_validate({"findings": findings}).findings
    assert [f.type.value for f in parsed] == ["pain_point", "sentiment", "sentiment", "trend"]
    for finding in parsed:
        assert isinstance(finding, Finding) and finding.evidence_ids
        known = own_numbers(finding.metrics)
        for number in claim_numbers(finding.claim):
            assert any(matches(number, value) for value in known), finding.claim
    assert parsed[0].metrics == {"count": 14, "share_pct": 15.6}
    assert parsed[2].metrics["metric_id"] == "m_abc"
    assert parsed[3].evidence_ids == [f"{0:016x}"]


def test_the_demo_findings_need_nothing_that_is_missing() -> None:
    assert findings_from_context({"themes": [], "metrics": [], "trends": []}) == []
    only_trend = {"themes": [], "metrics": [], "trends": [a_context()["trends"][0]]}
    assert [f["type"] for f in findings_from_context(only_trend)] == ["trend"]


def test_the_demo_headline_is_short_and_uses_context_numbers() -> None:
    headline = headline_from_context(a_context())
    assert headline.startswith("98 public items from 2 platform(s)")
    assert "slow customer support, easy to use" in headline and len(headline.split()) < 30


def test_the_demo_writer_answers_both_of_its_prompts() -> None:
    context = "CONTEXT:\n" + json.dumps(a_context())
    findings = writer_answer(
        [SystemMessage(content="You write the findings of a run"), HumanMessage(content=context)]
    )
    headline = writer_answer(
        [SystemMessage(content="You write the headline of a result"), HumanMessage(content=context)]
    )
    assert isinstance(findings, dict) and isinstance(headline, dict)
    assert len(FindingsDraft.model_validate(findings).findings) == 4
    assert HeadlineDraft.model_validate(headline).headline.startswith("98 public items")


def test_each_branch_script_has_enough_turns_for_a_replan() -> None:
    brief = load_brief()
    plan = demo_plan(brief)
    for branch in ("social", "demand", "reviews"):
        turns = branch_turns(branch, brief, plan)
        assert len(turns) == 2 * demo_script.DEMO_PASSES
    social = branch_turns("social", brief, plan)[0]
    assert [c["args"]["platform"] for c in social.tool_calls] == ["reddit", "x", "youtube"]  # type: ignore[union-attr]  # an AIMessage turn
    ids = [c["id"] for c in social.tool_calls]  # type: ignore[union-attr]  # an AIMessage turn
    assert len(set(ids)) == 3


def test_the_demo_factory_has_one_model_per_role_built_once() -> None:
    build = demo_llm(load_brief())
    assert {role: build(role)._llm_type for role in ("planner", "analyst", "writer", "branch")} == {
        "planner": "scripted-chat",
        "analyst": "scripted-chat",
        "writer": "scripted-chat",
        "branch": "branch-router",
    }
    assert build("writer") is build("writer")


# the fixtures the demo reads


def test_the_demo_fixtures_are_what_the_builder_writes() -> None:
    spec = importlib.util.spec_from_file_location("build_demo_fixtures", BUILDER)
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    expected = builder.results()
    assert {p.stem for p in FIXTURES.glob("*.json")} == set(expected)
    for name, result in expected.items():
        stored = ProviderResult.model_validate_json((FIXTURES / f"{name}.json").read_text("utf-8"))
        assert stored == result, f"{name}.json is stale: run build_demo_fixtures.py"


def test_the_demo_fixtures_serve_the_capabilities_the_demo_branches_call() -> None:
    names = {p.stem for p in FIXTURES.glob("*.json")}
    assert names == {
        "social_search.reddit",
        "social_search.x",
        "social_search.youtube",
        "search_interest",
        "reviews.app_store",
        "news.gdelt",
        "web_search",
    }
    counts = {
        name: len(
            ProviderResult.model_validate_json((FIXTURES / f"{name}.json").read_text("utf-8")).items
        )
        for name in names
    }
    assert counts["social_search.reddit"] == 30 and counts["social_search.x"] == 25
    assert counts["reviews.app_store"] == 25 and counts["search_interest"] == 2


def test_every_demo_post_carries_a_demo_phrase_in_its_language() -> None:
    phrases = {t.english: "en" for t in DEMO_THEMES} | {t.arabic: "ar" for t in DEMO_THEMES}
    for name in (
        "social_search.reddit",
        "social_search.x",
        "social_search.youtube",
        "reviews.app_store",
    ):
        result = ProviderResult.model_validate_json((FIXTURES / f"{name}.json").read_text("utf-8"))
        for item in result.items:
            language = next(lang for phrase, lang in phrases.items() if phrase in item.text)  # type: ignore[union-attr]  # evidence items
            assert item.language == language, (name, item.text)  # type: ignore[union-attr]  # evidence items
    x = ProviderResult.model_validate_json((FIXTURES / "social_search.x.json").read_text("utf-8"))
    assert sum(1 for i in x.items if i.language == "ar") == 15  # type: ignore[union-attr]  # evidence items


def test_fixture_mode_reads_the_demo_fixtures_unless_told_otherwise(
    tmp_path: Path, store: EvidenceStore
) -> None:
    brief = load_brief()
    settings = scenario_settings(tmp_path).model_copy(update={"edrak_provider_mode": "fixture"})
    deps = make_deps(brief, store=store, settings=settings, sink=None, providers=None, llm=None)
    assert deps.providers._fixtures_dir == settings.repo_root / DEMO_FIXTURES_RELATIVE
    other = tmp_path / "mine"
    custom = settings.model_copy(update={"edrak_fixtures_dir": other})
    deps = make_deps(brief, store=store, settings=custom, sink=None, providers=None, llm=None)
    assert deps.providers._fixtures_dir == other


def test_the_fake_llm_setting_selects_the_demo_models(tmp_path: Path, store: EvidenceStore) -> None:
    brief = load_brief()
    settings = scenario_settings(tmp_path).model_copy(update={"edrak_fake_llm": True})
    deps = make_deps(brief, store=store, settings=settings, sink=None, providers=None, llm=None)
    assert deps.model("branch")._llm_type == "branch-router"
    assert deps.model("planner")._llm_type == "scripted-chat"
