import json
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import httpx
import pytest

from edrak.agents.customer_trends.llm.fake import ScriptedChatModel
from edrak.agents.customer_trends.prompts import ANALYZE_THEMES
from edrak.agents.customer_trends.schemas.analysis import Sentiment, ThemeAggregate
from edrak.agents.customer_trends.schemas.common import Platform, SourceType, ToolStatus
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools import analyze_text
from edrak.agents.customer_trends.tools.analyze_text import (
    ItemLabel,
    build_clusters,
    clip_quote,
    cluster_label,
    pick_quotes,
)
from edrak.agents.customer_trends.tools.base import ToolContext, invoke_tool
from edrak.agents.customer_trends.tools.selection import load_items
from tests.customer_trends.factories import (
    ARABIC_PHRASES,
    BASE_DAY,
    ENGLISH_PHRASES,
    RUN_ID,
    TASK_ID,
    make_evidence,
)
from tests.customer_trends.tool_helpers import processing_context

Answer = dict[str, Any]


def store_items(store: EvidenceStore, items: list[EvidenceItem]) -> str:
    store.create_run(RUN_ID, TASK_ID)
    return store.add_batch(RUN_ID, TASK_ID, "test", items)[0]


def labelled(item: EvidenceItem, themes: list[str], sentiment: str = "neutral") -> dict[str, Any]:
    return {"id": item.id, "sentiment": sentiment, "themes": themes, "language": item.language}


def answer(*labels: dict[str, Any], candidates: Sequence[tuple[str, str]] = ()) -> Answer:
    return {
        "items": list(labels),
        "candidate_themes": [{"label": a, "description": b} for a, b in candidates],
    }


async def analyze(
    store: EvidenceStore, model: ScriptedChatModel, batch_id: str, **arguments: Any
) -> Any:
    ctx = processing_context(store, analyst=model)
    return await run(ctx, batch_id, **arguments)


async def run(ctx: ToolContext, batch_id: str, **arguments: Any) -> Any:
    return await invoke_tool(ctx, analyze_text.SPEC, {"batch_ids": [batch_id], **arguments})


def prompt_items(model: ScriptedChatModel, call: int = 0) -> list[dict[str, Any]]:
    human = model.calls[call][1].content
    assert isinstance(human, str)
    items: list[dict[str, Any]] = json.loads(human.split("Items:\n", 1)[1])
    return items


def at(days: int) -> Any:
    return BASE_DAY + timedelta(days=days)


# six items with hand-computed counts: three about support, two about pricing, one about neither
A1 = make_evidence(
    "Support is slow and tickets sit for days",
    platform=Platform.REDDIT,
    published_at=at(1),
    engagement={"likes": 9},
)
A2 = make_evidence(
    "Their support team takes forever to answer",
    platform=Platform.X,
    published_at=at(2),
    engagement={"likes": 5},
)
A3 = make_evidence(
    "الدعم الفني بطيء جدا ولا يرد على الرسائل",
    platform=Platform.YOUTUBE,
    source_type=SourceType.SOCIAL_COMMENT,
    language="ar",
    published_at=at(3),
    engagement={"likes": 7},
)
P1 = make_evidence(
    "Pricing keeps going up every quarter", published_at=at(4), engagement={"likes": 3}
)
P2 = make_evidence("Too expensive for what you get", published_at=at(5), engagement={"likes": 1})
O1 = make_evidence(
    "Company announces a new release",
    platform=None,
    source_type=SourceType.NEWS,
    published_at=None,
)
SIX = [A1, A2, A3, P1, P2, O1]


def six_answer() -> Answer:
    return answer(
        labelled(A1, ["Slow customer support"], "negative"),
        labelled(A2, ["slow support"], "negative"),
        labelled(A3, ["Slow support"], "neutral"),
        labelled(P1, ["Pricing"], "negative"),
        labelled(P2, ["pricing"], "mixed"),
        labelled(O1, []),
        candidates=[("Slow customer support", "Support answers arrive late"), ("Pricing", "")],
    )


async def test_themes_are_merged_counted_and_stored(store: EvidenceStore) -> None:
    batch_id = store_items(store, SIX)
    model = ScriptedChatModel(responses=[six_answer()])
    response = await analyze(store, model, batch_id)

    assert response.status is ToolStatus.OK
    assert (response.analyzed, response.total_items, response.count) == (6, 6, 6)
    assert response.themes_total == 2
    support, pricing = store.get_aggregates(RUN_ID)
    # "Slow customer support", "slow support" and "Slow support" share the words slow and
    # support (2 of 3 words in common, 0.67); the most used wording ties at one use each, so
    # the shortest, then the alphabetically first, is the label. Members are listed most
    # engaged first: A1 (9 likes), A3 (7), A2 (5).
    assert support.theme_label == "Slow support"
    assert support.count == 3 and support.share == 0.5
    assert support.evidence_ids == [A1.id, A3.id, A2.id]
    assert support.sentiment_mix == {"negative": 0.6667, "neutral": 0.3333}
    assert support.by_platform == {"reddit": 1, "x": 1, "youtube": 1}
    assert support.by_language == {"ar": 1, "en": 2}
    assert pricing.theme_label == "Pricing"
    assert pricing.count == 2 and pricing.share == 0.3333
    assert pricing.evidence_ids == [P1.id, P2.id]
    assert pricing.sentiment_mix == {"mixed": 0.5, "negative": 0.5}
    assert pricing.by_platform == {"reddit": 2} and pricing.by_language == {"en": 2}
    assert [t.label for t in response.themes] == ["Slow support", "Pricing"]
    assert response.themes[0].description == "Support answers arrive late"
    assert response.themes[0].share == 0.5 and response.themes[1].share == 0.333


async def test_the_overviews_count_every_labelled_item(store: EvidenceStore) -> None:
    batch_id = store_items(store, SIX)
    response = await analyze(store, ScriptedChatModel(responses=[six_answer()]), batch_id)
    assert response.sentiment == {
        "counts": {"negative": 3, "neutral": 2, "mixed": 1},
        "percent": {"negative": 50.0, "neutral": 33.3, "mixed": 16.7},
    }
    assert response.languages == {"en": 5, "ar": 1}


async def test_arabic_items_are_sent_as_written_and_counted(loaded_store: EvidenceStore) -> None:
    items, _ = load_items(loaded_store, RUN_ID, EvidenceFilters(), limit=300)
    themes = ["slow support and pricing", "missing excel export", "irregular income tracking",
              "easy to use", "cheaper competitors"]  # fmt: skip

    def theme_of(item: EvidenceItem) -> str:
        phrase = item.text.rsplit(" #", 1)[0]
        phrases = ARABIC_PHRASES if item.language == "ar" else ENGLISH_PHRASES
        return themes[phrases.index(phrase)]

    batch_id = loaded_store.run_summary(RUN_ID).batch_ids[0]
    model = ScriptedChatModel(
        responses=[
            answer(*(labelled(i, [theme_of(i)]) for i in items[:25])),
            answer(*(labelled(i, [theme_of(i)]) for i in items[25:])),
        ]
    )
    response = await analyze(loaded_store, model, batch_id)

    assert [len(prompt_items(model, n)) for n in (0, 1)] == [25, 15]
    sent = " ".join(item["text"] for n in (0, 1) for item in prompt_items(model, n))
    assert all(phrase in sent for phrase in ARABIC_PHRASES)
    assert (response.analyzed, response.total_items) == (40, 40)
    aggregates = loaded_store.get_aggregates(RUN_ID)
    assert sorted(a.theme_label for a in aggregates) == sorted(themes)
    assert all(a.count == 8 and a.share == 0.2 for a in aggregates)
    # 25 English and 15 Arabic items over the five themes
    assert sum(a.by_language.get("ar", 0) for a in aggregates) == 15
    assert sum(a.by_language.get("en", 0) for a in aggregates) == 25
    assert response.languages == {"en": 25, "ar": 15}


def test_similar_labels_merge_from_a_jaccard_of_0_6_and_do_not_drift() -> None:
    def labels(*themes: str) -> dict[str, ItemLabel]:
        return {
            str(n): ItemLabel(id=str(n), sentiment=Sentiment.NEUTRAL, themes=[t])
            for n, t in enumerate(themes)
        }

    clusters = build_clusters(
        labels("Slow customer support", "slow support", "Pricing", "pricing and support")
    )
    assert sorted(sorted(c.keys) for c in clusters) == [
        ["pricing"],
        ["pricing support"],
        ["slow customer support", "slow support"],
    ]
    # "alpha beta gamma epsilon" joins its head "alpha beta gamma delta" (3 of 5 words, 0.6),
    # but "alpha beta epsilon zeta" overlaps the head only 2 of 6 and stays apart, even
    # though it overlaps the second label at 0.6.
    head, second, third = (
        "alpha beta gamma delta",
        "alpha beta gamma epsilon",
        "alpha beta epsilon zeta",
    )
    chain = build_clusters(labels(*[head] * 3, *[second] * 2, third))
    assert [sorted(c.keys) for c in chain] == [[head, second], [third]]
    assert chain[0].members == {"0", "1", "2", "3", "4"}


def test_a_taxonomy_label_names_a_theme_it_matches() -> None:
    labels = {"1": ItemLabel(id="1", sentiment=Sentiment.NEGATIVE, themes=["pricing concerns"])}
    [cluster] = build_clusters(labels)
    assert cluster_label(cluster, ["Missing export", "Pricing concern"]) == "Pricing concern"
    assert cluster_label(cluster, None) == "pricing concerns"


async def test_labels_from_different_chunks_merge(store: EvidenceStore) -> None:
    items = [
        make_evidence(f"post number {n} about support", engagement={"likes": 100 - n})
        for n in range(30)
    ]
    batch_id = store_items(store, items)
    model = ScriptedChatModel(
        responses=[
            answer(*(labelled(i, ["slow customer support"]) for i in items[:25])),
            answer(*(labelled(i, ["Slow support"]) for i in items[25:])),
        ]
    )
    response = await analyze(store, model, batch_id)
    [theme] = store.get_aggregates(RUN_ID)
    assert (theme.theme_label, theme.count, theme.share) == ("slow customer support", 30, 1.0)
    assert response.themes_total == 1


async def test_a_chunk_that_cannot_be_answered_is_skipped_with_a_warning(
    store: EvidenceStore,
) -> None:
    items = [
        make_evidence(f"post number {n} about pricing", engagement={"likes": 100 - n})
        for n in range(30)
    ]
    batch_id = store_items(store, items)
    model = ScriptedChatModel(
        responses=[
            {"items": "not a list"},
            {"items": "still not a list"},
            answer(*(labelled(i, ["pricing"]) for i in items[25:])),
        ]
    )
    response = await analyze(store, model, batch_id)
    assert len(model.calls) == 3
    assert response.status is ToolStatus.PARTIAL
    assert response.analyzed == 5 and response.total_items == 30
    assert response.warnings == ["chunk 1 of 2 skipped (no valid answer): 25 items not analyzed"]
    [theme] = store.get_aggregates(RUN_ID)
    assert (theme.count, theme.share) == (5, 1.0)


async def test_a_chunk_is_retried_once_before_it_is_skipped(store: EvidenceStore) -> None:
    items = [make_evidence(f"post {n} about pricing") for n in range(3)]
    batch_id = store_items(store, items)
    model = ScriptedChatModel(
        responses=[{"items": [{"id": "x"}]}, answer(*(labelled(i, ["pricing"]) for i in items))]
    )
    response = await analyze(store, model, batch_id)
    assert len(model.calls) == 2
    assert response.status is ToolStatus.OK and response.warnings == []
    repair = model.calls[1][-1].content
    assert isinstance(repair, str) and "previous output was invalid" in repair


async def test_a_dropped_connection_skips_the_chunk_too(
    store: EvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [make_evidence(f"post {n} about pricing") for n in range(3)]
    batch_id = store_items(store, items)

    async def unreachable(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(analyze_text, "structured_call", unreachable)
    response = await analyze(store, ScriptedChatModel(responses=[]), batch_id)
    assert response.error_code == "analysis_failed"
    assert "chunk 1 of 1 skipped (the model call failed (ConnectError))" in response.gaps[0]


async def test_a_failing_analysis_leaves_the_stored_themes_alone(store: EvidenceStore) -> None:
    batch_id = store_items(store, SIX)
    await analyze(store, ScriptedChatModel(responses=[six_answer()]), batch_id)
    bad = ScriptedChatModel(responses=[{"items": 1}, {"items": 2}])
    response = await analyze(store, bad, batch_id)
    assert response.status is ToolStatus.ERROR and response.error_code == "analysis_failed"
    assert [a.theme_label for a in store.get_aggregates(RUN_ID)] == ["Slow support", "Pricing"]


async def test_a_bug_is_not_mistaken_for_a_failed_chunk(store: EvidenceStore) -> None:
    batch_id = store_items(store, [make_evidence("a post")])
    response = await analyze(store, ScriptedChatModel(responses=[]), batch_id)
    assert response.status is ToolStatus.ERROR and response.error_code == "tool_error"


async def test_ids_the_model_made_up_or_left_out_are_reported(store: EvidenceStore) -> None:
    batch_id = store_items(store, [A1, A2, P1])
    model = ScriptedChatModel(
        responses=[
            answer(
                labelled(A1, ["slow support"], "negative"),
                {"id": "ffffffffffffffff", "sentiment": "positive", "themes": ["ghost"]},
                labelled(A1, ["pricing"], "positive"),
                labelled(A2, ["slow support"], "negative"),
            )
        ]
    )
    response = await analyze(store, model, batch_id)
    assert response.analyzed == 2
    assert response.warnings == [
        "1 labels named ids that were not in the input; ignored",
        "1 items got no label from the model",
    ]
    [theme] = store.get_aggregates(RUN_ID)
    assert theme.theme_label == "slow support" and theme.count == 2 and theme.share == 1.0


async def test_the_model_answer_is_forgiving_about_case_and_extra_keys(
    store: EvidenceStore,
) -> None:
    batch_id = store_items(store, [A1])
    model = ScriptedChatModel(
        responses=[
            {
                "items": [
                    {
                        "id": A1.id,
                        "sentiment": " Negative ",
                        "themes": [" slow support ", "", "a", "b", "c"],
                        "language": "English",
                        "confidence": 0.9,
                    }
                ],
                "candidate_themes": [],
                "notes": "ignored",
            }
        ]
    )
    response = await analyze(store, model, batch_id)
    assert response.sentiment["counts"] == {"negative": 1}
    assert response.languages == {"en": 1}
    assert sorted(a.theme_label for a in store.get_aggregates(RUN_ID)) == ["a", "b", "slow support"]


async def test_quotes_are_verbatim_parts_of_stored_items_and_at_most_240_long(
    store: EvidenceStore,
) -> None:
    long_text = "word " * 100 + "the end"
    items = [
        make_evidence(long_text, engagement={"likes": 50}),
        make_evidence("Support is slow and tickets sit for days", engagement={"likes": 40}),
        make_evidence("slow!!", engagement={"likes": 30}),
        make_evidence("Their support team takes forever to answer", engagement={"likes": 20}),
        make_evidence("Another support complaint about the queue", engagement={"likes": 10}),
    ]
    batch_id = store_items(store, items)
    model = ScriptedChatModel(
        responses=[answer(*(labelled(i, ["slow support"], "negative") for i in items))]
    )
    await analyze(store, model, batch_id)
    [theme] = store.get_aggregates(RUN_ID)
    texts = {item.id: item.text for item in items}
    assert len(theme.representative_quotes) == 3
    for quote in theme.representative_quotes:
        assert len(quote) <= 240
        assert any(quote in text for text in texts.values()), quote
    # most engaged first; the 6 character item only fills a gap, so it is not needed here
    assert theme.representative_quotes == [
        clip_quote(long_text),
        "Support is slow and tickets sit for days",
        "Their support team takes forever to answer",
    ]
    assert clip_quote(long_text) == "word " * 47 + "word"


def test_a_short_text_is_a_quote_only_when_nothing_longer_is_left() -> None:
    long_enough = make_evidence("a complaint that is long enough")
    short = make_evidence("meh")
    assert pick_quotes([short, long_enough]) == ["a complaint that is long enough", "meh"]
    assert pick_quotes([short, make_evidence("meh")]) == ["meh"]
    assert clip_quote("  padded  ") == "padded"
    assert clip_quote("x" * 300) == "x" * 240


def growth_items() -> tuple[list[EvidenceItem], dict[int, list[str]]]:
    themes: dict[int, list[str]] = {
        0: ["support", "rare topic"],
        1: ["pricing"],
        2: ["support"],
        3: ["support"],
        4: ["support"],
        5: ["support"],
        6: ["pricing"],
        7: ["pricing"],
        8: ["pricing", "rare topic"],
    }
    items = [make_evidence(f"item for day {day}", published_at=at(day)) for day in themes]
    return items, themes


async def test_recent_growth_compares_the_latest_third_with_the_earlier_two_thirds(
    store: EvidenceStore,
) -> None:
    items, themes = growth_items()
    batch_id = store_items(store, items)
    model = ScriptedChatModel(
        responses=[answer(*(labelled(i, themes[day]) for day, i in enumerate(items)))]
    )
    await analyze(store, model, batch_id)
    by_label = {a.theme_label: a for a in store.get_aggregates(RUN_ID)}
    # Days 0 to 8: the cut is at day 5.33, so days 0 to 5 are the earlier period (6 items)
    # and days 6 to 8 the latest (3). Pricing: 1 of 6 earlier, 3 of 3 latest: 1 / (1/6) - 1.
    # Support: 5 of 6 earlier, none latest: -1. "rare topic" has only 2 items: no growth.
    assert by_label["pricing"].recent_growth == 5.0
    assert by_label["support"].recent_growth == -1.0
    assert by_label["rare topic"].recent_growth is None


async def test_growth_needs_enough_dated_items(store: EvidenceStore) -> None:
    items = [make_evidence(f"item {n}", published_at=at(n) if n < 5 else None) for n in range(9)]
    batch_id = store_items(store, items)
    model = ScriptedChatModel(responses=[answer(*(labelled(i, ["support"]) for i in items))])
    await analyze(store, model, batch_id)
    [theme] = store.get_aggregates(RUN_ID)
    assert theme.count == 9 and theme.recent_growth is None


async def test_max_items_keeps_the_most_engaged(loaded_store: EvidenceStore) -> None:
    batch_id = loaded_store.run_summary(RUN_ID).batch_ids[0]
    top, total = load_items(loaded_store, RUN_ID, EvidenceFilters(), limit=10)
    assert total == 40
    model = ScriptedChatModel(responses=[answer(*(labelled(i, ["pricing"]) for i in top))])
    response = await analyze(loaded_store, model, batch_id, max_items=10)
    assert [item["id"] for item in prompt_items(model)] == [i.id for i in top]
    assert min(i.engagement_total for i in top) >= max(
        i.engagement_total for i in loaded_store.query(RUN_ID, limit=40).items if i not in top
    )
    assert (response.analyzed, response.total_items) == (10, 40)
    assert response.warnings == [
        "analyzed the 10 most engaged of 40 items; raise max_items to read more"
    ]


async def test_trend_point_items_are_not_analyzed(store: EvidenceStore) -> None:
    trend = make_evidence(
        "Search interest for gitlab duo is up", platform=None, source_type=SourceType.TREND_POINT
    )
    batch_id = store_items(store, [A1, trend])
    model = ScriptedChatModel(responses=[answer(labelled(A1, ["slow support"]))])
    response = await analyze(store, model, batch_id)
    assert [item["id"] for item in prompt_items(model)] == [A1.id]
    assert response.total_items == 1


async def test_the_prompt_carries_the_taxonomy_the_tasks_and_cut_texts(
    store: EvidenceStore,
) -> None:
    long = make_evidence("long " * 400)
    batch_id = store_items(store, [long])
    model = ScriptedChatModel(responses=[answer(labelled(long, ["pricing concern"]))])
    await analyze(
        store, model, batch_id, taxonomy=["Pricing concerns", "Missing export"], tasks=["themes"]
    )
    system, human = model.calls[0][0], model.calls[0][1]
    assert system.content == ANALYZE_THEMES
    assert isinstance(human.content, str)
    assert human.content.startswith(
        "Tasks wanted: themes\nTaxonomy: Pricing concerns, Missing export\nItems:\n"
    )
    [sent] = prompt_items(model)
    assert sent == {"id": long.id, "platform": "reddit", "text": sent["text"]}
    assert len(sent["text"]) == 600
    assert [a.theme_label for a in store.get_aggregates(RUN_ID)] == ["Pricing concerns"]


async def test_without_the_themes_task_nothing_is_stored(store: EvidenceStore) -> None:
    batch_id = store_items(store, SIX)
    response = await analyze(
        store, ScriptedChatModel(responses=[six_answer()]), batch_id, tasks=["language"]
    )
    assert response.themes == [] and response.sentiment == {}
    assert response.languages == {"en": 5, "ar": 1}
    assert store.get_aggregates(RUN_ID) == []


async def test_without_the_sentiment_task_the_mixes_are_left_out(store: EvidenceStore) -> None:
    batch_id = store_items(store, SIX)
    response = await analyze(
        store, ScriptedChatModel(responses=[six_answer()]), batch_id, tasks=["themes"]
    )
    assert response.sentiment == {} and response.languages == {}
    assert all(a.sentiment_mix == {} for a in store.get_aggregates(RUN_ID))
    assert response.themes[0].sentiment_mix == {}


async def test_a_second_analysis_replaces_the_first_and_says_so(store: EvidenceStore) -> None:
    batch_id = store_items(store, SIX)
    first = await analyze(store, ScriptedChatModel(responses=[six_answer()]), batch_id)
    assert first.warnings == []
    again = await analyze(store, ScriptedChatModel(responses=[six_answer()]), batch_id)
    assert again.warnings == ["replaced 2 theme(s) stored by an earlier analysis"]
    assert len(store.get_aggregates(RUN_ID)) == 2


async def test_an_answer_without_themes_keeps_the_stored_ones(store: EvidenceStore) -> None:
    batch_id = store_items(store, SIX)
    await analyze(store, ScriptedChatModel(responses=[six_answer()]), batch_id)
    empty = answer(*(labelled(i, []) for i in SIX))
    response = await analyze(store, ScriptedChatModel(responses=[empty]), batch_id)
    assert response.themes_total == 0
    assert response.warnings == [
        "the model found no themes; the stored themes were left as they were"
    ]
    assert len(store.get_aggregates(RUN_ID)) == 2


async def test_the_answer_lists_at_most_fifteen_themes_but_stores_all(
    store: EvidenceStore,
) -> None:
    items = [make_evidence(f"topic {n} is discussed") for n in range(20)]
    batch_id = store_items(store, items)
    model = ScriptedChatModel(
        responses=[answer(*(labelled(i, [f"topic{chr(97 + n)}"]) for n, i in enumerate(items)))]
    )
    response = await analyze(store, model, batch_id)
    assert response.themes_total == 20 and len(response.themes) == 15
    assert len(store.get_aggregates(RUN_ID)) == 20


async def test_a_long_description_is_cut_for_the_model(store: EvidenceStore) -> None:
    batch_id = store_items(store, [A1])
    model = ScriptedChatModel(
        responses=[answer(labelled(A1, ["slow support"]), candidates=[("slow support", "d" * 400)])]
    )
    response = await analyze(store, model, batch_id)
    assert len(response.themes[0].description) == 160


async def test_unknown_or_empty_batches_and_a_missing_model_are_errors(
    store: EvidenceStore,
) -> None:
    batch_id = store_items(store, [A1])
    empty_batch = store.add_batch(RUN_ID, TASK_ID, "test", [])[0]
    ctx = processing_context(store)
    unknown = await run(ctx, "b_missing")
    assert unknown.error_code == "unknown_batch" and "b_missing" in unknown.gaps[0]
    nothing = await run(ctx, empty_batch)
    assert nothing.error_code == "no_data"
    no_model = await run(ctx, batch_id)
    assert no_model.error_code == "llm_unavailable" and "OLLAMA_API_KEY" in no_model.gaps[0]


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        ({"batch_ids": []}, "batch_ids"),
        ({"tasks": ["summarize"]}, "tasks"),
        ({"tasks": []}, "tasks"),
        ({"max_items": 0}, "max_items"),
        ({"max_items": 5000}, "max_items"),
        ({"taxonomy": [""]}, "taxonomy"),
    ],
)
async def test_bad_arguments_are_readable_errors(
    store: EvidenceStore, arguments: dict[str, Any], problem: str
) -> None:
    store.create_run(RUN_ID, TASK_ID)
    ctx = processing_context(store)
    raw = {"batch_ids": ["b_1"], **arguments}
    response = await invoke_tool(ctx, analyze_text.SPEC, raw)
    assert response.error_code == "invalid_input" and problem in response.gaps[0]


async def test_the_call_is_reported_as_an_event(store: EvidenceStore) -> None:
    events: list[dict[str, Any]] = []
    batch_id = store_items(store, SIX)
    ctx = processing_context(
        store, analyst=ScriptedChatModel(responses=[six_answer()]), emit=events.append
    )
    await run(ctx, batch_id)
    [event] = events
    assert event["tool"] == "analyze_text" and event["status"] == "ok" and event["count"] == 6


def test_the_prompt_names_the_rules_the_tool_depends_on() -> None:
    for required in (
        "never translate",
        "candidate_themes",
        "Never invent",
        "2 to 4 words",
        "Arabic",
        "taxonomy",
    ):
        assert required.lower() in ANALYZE_THEMES.lower(), required
    assert chr(0x2014) not in ANALYZE_THEMES


def test_an_aggregate_keeps_its_quotes_through_the_store(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    aggregate = ThemeAggregate(
        theme_label="pricing", count=1, share=1.0, representative_quotes=["a quote"]
    )
    store.save_aggregates(RUN_ID, [aggregate])
    assert store.get_aggregates(RUN_ID) == [aggregate]
