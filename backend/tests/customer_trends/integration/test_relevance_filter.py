"""The relevance_filter node over a real store and the scripted world: no network, no key."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from edrak.agents.customer_trends import nodes
from edrak.agents.customer_trends.deps import WorkerDeps
from edrak.agents.customer_trends.gaps import coverage_of, find_gaps
from edrak.agents.customer_trends.llm.demo_script import tool_calls
from edrak.agents.customer_trends.llm.fake import FunctionChatModel, Scripted, ScriptedChatModel
from edrak.agents.customer_trends.relevance.pipeline import classify_budget_s, run_stats
from edrak.agents.customer_trends.runner import run_task
from edrak.agents.customer_trends.schemas.common import Budget, Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.state import WorkerState
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools.registry import build_tools
from edrak.agents.customer_trends.tools.selection import load_items
from tests.customer_trends.factories import load_brief, make_evidence
from tests.customer_trends.graph_helpers import (
    Models,
    World,
    models,
    scenario_settings,
    worker_deps,
    world_registry,
)

COMMENT = SourceType.SOCIAL_COMMENT
POST_URL = "https://www.instagram.com/p/AAAA/"
POST_TEXT = "Meet the new GitLab Duo card. Preorders are open."

SPAM = "ههههههههههه"
DUP = "the price is way too high"
DUP2 = (
    "the priiice is way too high"  # the store keeps it (another hash); the filter folds it into DUP
)
SHORT = "price?"
SHORT_TERM = "GitLab"
TERM_SENTENCE = "GitHub Copilot is cheaper than this"
LLM_YES = "كام سعر الاشتراك الشهري"
LLM_NO = "congratulations to everyone involved today"
EMOJI = "😂😂"


def sees_price(messages: list[BaseMessage]) -> Scripted:
    """The relevance model of these tests: a comment about a price is relevant, the rest is not."""
    shown = str(messages[1].content)
    ids = re.findall(r"^(c\d+): (.*)$", shown, flags=re.MULTILINE)
    return {"relevant": [i for i, text in ids if "price" in text or "سعر" in text]}


def price_model() -> FunctionChatModel:
    return FunctionChatModel(responses=[], responder=sees_price)


def comment(text: str, platform: Platform = Platform.INSTAGRAM, **kw: Any) -> EvidenceItem:
    meta = {"post_url": POST_URL} if platform is Platform.INSTAGRAM else {}
    return make_evidence(text, platform=platform, source_type=COMMENT, metadata=meta, **kw)


def seed(store: EvidenceStore, brief: TaskBrief, texts: list[str]) -> dict[str, EvidenceItem]:
    post = make_evidence(POST_TEXT, platform=Platform.INSTAGRAM, url=POST_URL)
    items = {text: comment(text) for text in texts}
    store.create_run(brief.run_id, brief.task_id, brief)
    store.add_batch(brief.run_id, brief.task_id, "social", [post, *items.values()])
    return items


def make(
    tmp_path: Path, store: EvidenceStore, scripted: Models | None = None, **settings: Any
) -> tuple[WorkerDeps, TaskBrief]:
    brief = load_brief().model_copy(update={"aliases": ["GitLab Inc."], "products": ["Duo"]})
    deps = worker_deps(brief, tmp_path, store, scripted=scripted or models(relevance=price_model()))
    if settings:
        deps.settings = deps.settings.model_copy(update=settings)
    return deps, brief


def state_of(brief: TaskBrief) -> WorkerState:
    return {"brief": brief.model_dump(mode="json")}


def stored(
    store: EvidenceStore, brief: TaskBrief, items: dict[str, EvidenceItem]
) -> dict[str, EvidenceItem]:
    found = store.get_items(brief.run_id, [i.id for i in items.values()])
    by_id = {i.id: i for i in found}
    return {text: by_id[item.id] for text, item in items.items()}


ALL = [SPAM, DUP, DUP2, SHORT, SHORT_TERM, TERM_SENTENCE, LLM_YES, LLM_NO, EMOJI]


# ------------------------------------------------------------------ the three stages


async def test_each_stage_marks_its_comments_and_nothing_is_deleted(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, brief = make(tmp_path, store)
    items = seed(store, brief, ALL)
    update = await nodes.relevance_filter(state_of(brief), deps)

    got = stored(store, brief, items)
    verdicts = {text: (i.relevance, i.relevance_reason, i.filter_stage) for text, i in got.items()}
    assert verdicts == {
        SPAM: ("irrelevant", "spam", "a"),
        DUP: ("relevant", "llm_relevant", "c"),
        DUP2: ("irrelevant", "duplicate", "a"),
        SHORT: ("irrelevant", "too_short", "a"),
        SHORT_TERM: ("relevant", "term_match", "b"),
        TERM_SENTENCE: ("relevant", "term_match", "b"),
        LLM_YES: ("relevant", "llm_relevant", "c"),
        LLM_NO: ("irrelevant", "llm_irrelevant", "c"),
        EMOJI: ("irrelevant", "empty", "a"),
    }
    assert got[DUP].metadata["duplicate_count"] == 1
    assert got[DUP2].metadata["duplicate_of"] == items[DUP].id
    assert all(got[text].text == text for text in ALL)  # the stored text is untouched
    assert update["warnings"] == []


async def test_the_model_sees_only_what_the_free_stages_left_and_the_post_once(
    tmp_path: Path, store: EvidenceStore
) -> None:
    scripted = models(relevance=price_model())
    deps, brief = make(tmp_path, store, scripted)
    seed(store, brief, ALL)
    await nodes.relevance_filter(state_of(brief), deps)

    assert len(scripted.relevance.calls) == 1  # type: ignore[attr-defined]  # a function chat model
    shown = str(scripted.relevance.calls[0][1].content)  # type: ignore[attr-defined]
    assert shown == f"POST P1: {POST_TEXT}\nc1: {DUP}\nc2: {LLM_YES}\nc3: {LLM_NO}"
    system = str(scripted.relevance.calls[0][0].content)  # type: ignore[attr-defined]
    assert (
        brief.question in system
        and "GitLab Inc." in system
        and "Duo" in system
        and "GitHub Copilot" in system
    )


async def test_nothing_reaches_the_model_when_the_free_stages_decide_everything(
    tmp_path: Path, store: EvidenceStore
) -> None:
    scripted = models(relevance=price_model())
    deps, brief = make(tmp_path, store, scripted)
    seed(store, brief, [SPAM, SHORT, SHORT_TERM, TERM_SENTENCE])
    await nodes.relevance_filter(state_of(brief), deps)
    assert scripted.relevance.calls == []  # type: ignore[attr-defined]


# ------------------------------------------------------------------ what downstream reads


async def test_downstream_reads_only_relevant_comments_and_the_store_keeps_all(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, brief = make(tmp_path, store)
    items = seed(store, brief, ALL)
    await nodes.relevance_filter(state_of(brief), deps)
    relevant = {DUP, SHORT_TERM, TERM_SENTENCE, LLM_YES}

    found, total = load_items(store, brief.run_id, EvidenceFilters(source_type=COMMENT))
    assert {i.text for i in found} == relevant and total == 4
    tools = build_tools(deps.tool_context(brief))
    answer = json.loads(
        await tools["evidence_query"].ainvoke(
            {"filters": {"source_type": "social_comment"}, "limit": 50}
        )
    )
    assert {i["text"] for i in answer["items"]} == relevant
    assert store.count_by(brief.run_id, "source_type") == {"social_comment": 4, "social_post": 1}
    everything = store.query(
        brief.run_id, EvidenceFilters(source_type=COMMENT), limit=50, include_irrelevant=True
    )
    assert everything.total == len(ALL) and {i.text for i in everything.items} == set(ALL)
    assert store.run_summary(brief.run_id).evidence_count == 5
    assert len(store.get_items(brief.run_id, [i.id for i in items.values()])) == len(
        ALL
    )  # an id lookup finds all


async def test_the_analysis_model_is_never_shown_a_filtered_comment(
    tmp_path: Path, store: EvidenceStore
) -> None:
    scripted = models(relevance=price_model())
    deps, brief = make(tmp_path, store, scripted)
    seed(store, brief, ALL)
    state = state_of(brief)
    update = await nodes.relevance_filter(state, deps)
    state = {
        **state,
        "batches": {"social": store.run_summary(brief.run_id).batch_ids},
        "warnings": update["warnings"],
    }
    await nodes.analyze(state, deps)

    shown = " ".join(str(m.content) for call in scripted.analyst.calls for m in call)
    for noise in (SPAM, SHORT, LLM_NO, DUP2, EMOJI):
        assert noise not in shown, noise
    for kept in (LLM_YES, TERM_SENTENCE):
        assert kept in shown, kept


async def test_a_finding_cannot_cite_a_filtered_comment(
    tmp_path: Path, store: EvidenceStore
) -> None:
    from edrak.agents.customer_trends.schemas.common import Confidence, FindingType
    from edrak.agents.customer_trends.schemas.findings import Finding
    from edrak.agents.customer_trends.tools.submit_findings import check_finding

    deps, brief = make(tmp_path, store)
    items = seed(store, brief, ALL)
    await nodes.relevance_filter(state_of(brief), deps)

    def finding(evidence_id: str) -> Finding:
        return Finding(
            id="f1",
            type=FindingType.PAIN_POINT,
            claim="People mention a price.",
            confidence=Confidence.LOW,
            evidence_ids=[evidence_id],
        )

    assert check_finding(finding(items[LLM_YES].id), store, brief.run_id, []) == []
    assert any(
        "not found" in r for r in check_finding(finding(items[LLM_NO].id), store, brief.run_id, [])
    )


# ------------------------------------------------------------------ switched off, and failing open


async def test_with_the_filter_switched_off_everything_passes_through_unchanged(
    tmp_path: Path, store: EvidenceStore
) -> None:
    scripted = models(relevance=price_model())
    deps, brief = make(tmp_path, store, scripted, relevance_filter_enabled=False)
    items = seed(store, brief, ALL)
    update = await nodes.relevance_filter(state_of(brief), deps)

    assert set(update) == {"events"} and not any(
        e["type"] == "relevance_filtered" for e in update["events"]
    )
    assert scripted.relevance.calls == []  # type: ignore[attr-defined]
    assert all(i.relevance is None for i in stored(store, brief, items).values())
    assert store.query(brief.run_id, EvidenceFilters(source_type=COMMENT), limit=50).total == len(
        ALL
    )
    assert run_stats(deps, brief.run_id) == {"enabled": False, "item_types": ["social_comment"]}


async def test_the_item_types_come_from_the_configuration(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, brief = make(tmp_path, store, relevance_item_types=[SourceType.REVIEW])
    items = seed(store, brief, [SPAM, LLM_NO])
    update = await nodes.relevance_filter(state_of(brief), deps)
    assert not any(
        e["type"] == "relevance_filtered" for e in update["events"]
    )  # comments are not on the list
    assert all(i.relevance is None for i in stored(store, brief, items).values())


async def test_reviews_can_be_added_to_the_item_types(tmp_path: Path, store: EvidenceStore) -> None:
    deps, brief = make(
        tmp_path, store, relevance_item_types=[SourceType.SOCIAL_COMMENT, SourceType.REVIEW]
    )
    review = make_evidence("😂😂", platform=None, source_type=SourceType.REVIEW)
    store.create_run(brief.run_id, brief.task_id, brief)
    store.add_batch(brief.run_id, brief.task_id, "reviews", [review])
    await nodes.relevance_filter(state_of(brief), deps)
    assert store.get_items(brief.run_id, [review.id])[0].relevance_reason == "empty"


async def test_a_model_that_answers_invalidly_keeps_the_comments_and_warns(
    tmp_path: Path, store: EvidenceStore
) -> None:
    broken = ScriptedChatModel(responses=[{"relevant": 1}, {"relevant": 2}])
    deps, brief = make(tmp_path, store, models(relevance=broken))
    items = seed(store, brief, ALL)
    update = await nodes.relevance_filter(state_of(brief), deps)

    got = stored(store, brief, items)
    for text in (DUP, LLM_YES, LLM_NO):
        assert (got[text].relevance, got[text].relevance_reason, got[text].filter_stage) == (
            "relevant",
            "kept_on_error",
            "c",
        )
    assert got[SPAM].relevance_reason == "spam"  # the free stages still did their work
    assert len(update["warnings"]) == 1 and "3 comment(s)" in update["warnings"][0]


async def test_a_model_that_cannot_be_built_keeps_the_comments(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, brief = make(tmp_path, store)

    def broken(role: str) -> Any:
        raise RuntimeError("no key")

    deps.llm = broken  # type: ignore[assignment]  # a factory that fails
    items = seed(store, brief, ALL)
    update = await nodes.relevance_filter(state_of(brief), deps)
    assert stored(store, brief, items)[LLM_NO].relevance_reason == "kept_on_error"
    assert "could not build its model" in update["warnings"][0]


async def test_a_failure_of_the_whole_filter_changes_nothing_and_warns(
    tmp_path: Path, store: EvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps, brief = make(tmp_path, store)
    items = seed(store, brief, ALL)

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("store exploded")

    monkeypatch.setattr(nodes, "filter_run", boom)
    update = await nodes.relevance_filter(state_of(brief), deps)
    assert update["warnings"] == [
        "the relevance filter failed (RuntimeError); comments were not filtered"
    ]
    assert all(i.relevance is None for i in stored(store, brief, items).values())


# ------------------------------------------------------------------ passes, events and counts


async def test_a_second_pass_judges_only_the_comments_it_collected(
    tmp_path: Path, store: EvidenceStore
) -> None:
    scripted = models(relevance=price_model())
    deps, brief = make(tmp_path, store, scripted)
    first = seed(store, brief, [DUP, LLM_NO])
    await nodes.relevance_filter(state_of(brief), deps)
    assert len(scripted.relevance.calls) == 1  # type: ignore[attr-defined]

    later = {text: comment(text) for text in (DUP2, "the price of the premium plan is fair")}
    store.add_batch(brief.run_id, brief.task_id, "social-2", list(later.values()))
    await nodes.relevance_filter(state_of(brief), deps)

    assert len(scripted.relevance.calls) == 2  # type: ignore[attr-defined]
    shown = str(scripted.relevance.calls[1][1].content)  # type: ignore[attr-defined]
    assert LLM_NO not in shown and DUP not in shown and "premium plan" in shown
    got = stored(store, brief, {**first, **later})
    assert (
        got[DUP2].relevance_reason == "duplicate"
        and got[DUP2].metadata["duplicate_of"] == first[DUP].id
    )
    assert got[DUP].metadata["duplicate_count"] == 1
    assert got[LLM_NO].relevance_reason == "llm_irrelevant"  # the earlier verdict stands


async def test_a_pass_reports_one_event_with_its_counts(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, brief = make(tmp_path, store)
    seed(store, brief, ALL)
    update = await nodes.relevance_filter(state_of(brief), deps)
    event = next(e for e in deps.bus.events if e["type"] == "relevance_filtered")
    assert event in update["events"]
    assert {k: event[k] for k in ("comments_in", "dropped", "sent_to_llm", "kept")} == {
        "comments_in": 9,
        "dropped": {"spam": 1, "duplicate": 1, "too_short": 1, "llm_irrelevant": 1, "empty": 1},
        "sent_to_llm": 3,
        "kept": 4,
    }
    again = await nodes.relevance_filter(state_of(brief), deps)
    assert not any(
        e["type"] == "relevance_filtered" for e in again["events"]
    )  # nothing new to judge


async def test_the_counts_per_run_and_platform_with_an_estimated_token_count(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, brief = make(tmp_path, store)
    items = seed(store, brief, [SPAM, SHORT_TERM, LLM_NO])
    on_x = comment("this is a price complaint about the card", Platform.X)
    store.add_batch(brief.run_id, brief.task_id, "x", [on_x])
    await nodes.relevance_filter(state_of(brief), deps)

    stats = run_stats(deps, brief.run_id)
    chars_all = len(SPAM) + len(SHORT_TERM) + len(LLM_NO) + len(on_x.text)
    chars_kept = len(SHORT_TERM) + len(on_x.text)
    assert stats["enabled"] is True and "estimate" in stats["token_estimate"]
    assert (stats["comments_in"], stats["kept"], stats["sent_to_llm"]) == (4, 2, 2)
    assert stats["dropped"] == {"spam": 1, "llm_irrelevant": 1}
    assert stats["kept_by_reason"] == {"term_match": 1, "llm_relevant": 1}
    assert (stats["tokens_before"], stats["tokens_after"]) == (
        -(-chars_all // 4),
        -(-chars_kept // 4),
    )
    ig, x = stats["by_platform"]["instagram"], stats["by_platform"]["x"]
    assert (ig["comments_in"], ig["kept"], ig["sent_to_llm"], ig["dropped"]) == (
        3,
        1,
        1,
        {"spam": 1, "llm_irrelevant": 1},
    )
    assert (x["comments_in"], x["kept"], x["sent_to_llm"], x["dropped"]) == (1, 1, 1, {})
    assert items and ig["tokens_before"] > ig["tokens_after"]


def test_the_time_for_model_calls_is_capped_by_the_config_and_by_half_the_finish_reserve(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, _ = make(tmp_path, store)
    deps.budget._budget = Budget(max_seconds=300)  # type: ignore[attr-defined]  # the tracker's limits
    assert classify_budget_s(deps) == 60  # 300 * 0.5 / 2 = 75, capped by the 60 s setting
    deps.settings = deps.settings.model_copy(update={"relevance_timeout_s": 10})
    assert classify_budget_s(deps) == 10


# ------------------------------------------------------------------ the gap note


async def test_too_few_relevant_comments_on_a_platform_is_a_gap_note(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, brief = make(tmp_path, store)
    seed(store, brief, ALL)
    await nodes.relevance_filter(state_of(brief), deps)
    config = deps.use_cases.for_use_case(brief.use_case)

    coverage = coverage_of(store, brief.run_id, filtered_types=["social_comment"])
    assert (coverage.comments_judged, coverage.comments_kept) == (
        {"instagram": 9},
        {"instagram": 4},
    )
    gaps = find_gaps(brief, config, coverage, min_relevant_comments=5)
    note = next(g for g in gaps if g.id == "relevant_comments_instagram")
    assert note.severity == "minor"
    assert "only 4 relevant comment(s) on instagram remain after filtering 9" in note.description
    assert "relevant comment volume was low" in note.description
    assert not any(
        g.id.startswith("relevant_comments_")
        for g in find_gaps(brief, config, coverage, min_relevant_comments=4)
    )
    assert not any(
        g.id.startswith("relevant_comments_")
        for g in find_gaps(brief, config, coverage, min_relevant_comments=0)
    )


async def test_the_gap_check_node_reports_the_note_and_does_not_replan_for_it(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, brief = make(tmp_path, store)
    seed(store, brief, ALL)
    await nodes.relevance_filter(state_of(brief), deps)
    update = await nodes.gap_check(state_of(brief), deps)
    gaps = update["gaps"].value
    assert "relevant_comments_instagram" in {g["id"] for g in gaps}
    assert any(
        e["type"] == "gap_found" and e["gap_id"] == "relevant_comments_instagram"
        for e in update["events"]
    )

    off, _ = make(tmp_path, store, relevance_filter_enabled=False)
    update = await nodes.gap_check(state_of(brief), off)
    assert "relevant_comments_instagram" not in {g["id"] for g in update["gaps"].value}


async def test_a_platform_with_no_judged_comments_has_no_note(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps, brief = make(tmp_path, store)
    store.create_run(brief.run_id, brief.task_id, brief)
    store.add_batch(
        brief.run_id, brief.task_id, "x", [make_evidence("a post", platform=Platform.X)]
    )
    await nodes.relevance_filter(state_of(brief), deps)
    update = await nodes.gap_check(state_of(brief), deps)
    assert not any(g["id"].startswith("relevant_comments_") for g in update["gaps"].value)


# ------------------------------------------------------------------ the whole graph


async def test_the_graph_filters_collected_comments_before_analysis_and_reports_the_counts(
    tmp_path: Path,
) -> None:
    texts = [
        SPAM,
        EMOJI,
        SHORT,
        SHORT_TERM,
        LLM_YES,
        LLM_NO,
        "the price is too high for small teams",
    ]
    world = World(comment_texts=texts)
    social = [
        tool_calls(
            ("social_search", {"platform": "instagram", "query": "GitLab Duo"}),
            ("social_comments", {"platform": "instagram", "post_url": POST_URL}),
            tag="s",
        ),
        AIMessage(content="Collected."),
    ]
    scripted = models(social=social, relevance=price_model())
    brief = load_brief()
    result = await run_task(
        brief,
        providers=world_registry(world),
        llm=scripted.factory,
        settings=scenario_settings(tmp_path),
    )
    assert result.control_summary.status in {"complete", "partial"}

    stats = result.control_summary.coverage["relevance_filter"]
    assert stats["enabled"] is True and stats["comments_in"] == len(texts)
    assert stats["dropped"] == {"empty": 1, "spam": 1, "too_short": 1, "llm_irrelevant": 1}
    assert stats["kept"] == 3 and stats["by_platform"]["instagram"]["comments_in"] == len(texts)
    shown = " ".join(str(m.content) for call in scripted.analyst.calls for m in call)
    for noise in (SPAM, EMOJI, LLM_NO):
        assert noise not in shown
    assert "the price is too high for small teams" in shown

    with EvidenceStore.from_settings(scenario_settings(tmp_path)) as opened:
        audit = opened.query(
            brief.run_id, EvidenceFilters(source_type=COMMENT), limit=50, include_irrelevant=True
        )
        assert audit.total == len(texts)
        assert result.evidence_ref.count == opened.run_summary(brief.run_id).evidence_count
