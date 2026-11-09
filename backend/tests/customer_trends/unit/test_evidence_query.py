from datetime import datetime, timedelta
from typing import Any

from edrak.agents.customer_trends.schemas.analysis import ThemeAggregate
from edrak.agents.customer_trends.schemas.common import Platform, SourceType, ToolStatus
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools import evidence_query
from edrak.agents.customer_trends.tools.base import ToolContext, invoke_tool
from tests.customer_trends.factories import BASE_DAY, RUN_ID, TASK_ID, make_evidence
from tests.customer_trends.tool_helpers import processing_context


async def query(ctx: ToolContext, **arguments: Any) -> Any:
    return await invoke_tool(ctx, evidence_query.SPEC, arguments)


def ids(response: Any) -> list[str]:
    return [item["id"] for item in response.items]


def store_items(store: EvidenceStore, items: list[EvidenceItem]) -> str:
    store.create_run(RUN_ID, TASK_ID)
    return store.add_batch(RUN_ID, TASK_ID, "test", items)[0]


def at(days: int) -> datetime:
    return BASE_DAY + timedelta(days=days)


async def test_the_default_returns_ten_of_the_forty_with_the_total(
    loaded_store: EvidenceStore,
) -> None:
    response = await query(processing_context(loaded_store))
    assert response.status is ToolStatus.OK
    assert (response.count, response.total) == (10, 40)
    assert response.warnings == ["30 more matching items were not returned"]


async def test_no_more_than_twenty_items_come_back(loaded_store: EvidenceStore) -> None:
    response = await query(processing_context(loaded_store), limit=500)
    assert (response.count, response.total) == (20, 40)
    assert response.warnings[0] == "limit lowered to 20, the most one call returns"


async def test_a_text_is_cut_to_five_hundred_characters(store: EvidenceStore) -> None:
    store_items(store, [make_evidence("long " * 300)])
    [item] = (await query(processing_context(store))).items
    assert len(item["text"]) == 500 and item["text"].endswith("…")


async def test_an_item_is_shown_with_what_a_finding_needs_to_cite_it(store: EvidenceStore) -> None:
    item = make_evidence(
        "Great app but the export is missing",
        platform=Platform.X,
        language="en",
        published_at=at(3),
        engagement={"likes": 4, "replies": 1},
        url="https://x.com/u/status/1",
    )
    news = make_evidence(
        "A news snippet",
        platform=None,
        source_type=SourceType.NEWS,
        snippet_only=True,
        published_at=None,
    )
    store_items(store, [item, news])
    response = await query(processing_context(store), sample="recent")
    assert response.items == [
        {
            "id": item.id,
            "platform": "x",
            "source_type": "social_post",
            "language": "en",
            "published": "2026-07-04",
            "engagement": {"likes": 4, "replies": 1},
            "url": "https://x.com/u/status/1",
            "text": "Great app but the export is missing",
        },
        {
            "id": news.id,
            "platform": "news",
            "source_type": "news",
            "language": "en",
            "published": None,
            "engagement": {},
            "url": news.url,
            "text": "A news snippet",
            "snippet_only": True,
        },
    ]


async def test_filters_combine(loaded_store: EvidenceStore) -> None:
    ctx = processing_context(loaded_store)
    arabic_x = await query(ctx, filters={"platform": "x", "language": "ar"}, limit=20)
    assert arabic_x.total == 4
    assert all(i["platform"] == "x" and i["language"] == "ar" for i in arabic_x.items)
    comments = await query(ctx, filters={"source_type": "social_comment"}, limit=20)
    assert comments.total == 10
    dated = await query(ctx, filters={"since": "2026-07-05", "until": "2026-07-07"}, limit=20)
    assert dated.total == 3
    assert {i["published"] for i in dated.items} == {"2026-07-05", "2026-07-06", "2026-07-07"}


async def test_text_contains_ignores_case_and_arabic_diacritics(store: EvidenceStore) -> None:
    store_items(
        store,
        [
            make_evidence("The Pricing is too high"),
            make_evidence("الأسعار مرتفعَة جدا", language="ar"),
            make_evidence("unrelated"),
        ],
    )
    ctx = processing_context(store)
    assert (await query(ctx, filters={"text_contains": "PRICING"})).total == 1
    assert (await query(ctx, filters={"text_contains": "مرتفعة"})).total == 1


async def test_min_engagement_and_batch_ids(store: EvidenceStore) -> None:
    first = store_items(
        store,
        [
            make_evidence("quiet", engagement={"likes": 1}),
            make_evidence("busy", engagement={"likes": 9}),
        ],
    )
    store.add_batch(RUN_ID, TASK_ID, "test", [make_evidence("busier", engagement={"likes": 20})])
    ctx = processing_context(store)
    assert (await query(ctx, filters={"min_engagement": 5})).total == 2
    only_first = await query(ctx, filters={"batch_ids": [first], "min_engagement": 5})
    assert [i["text"] for i in only_first.items] == ["busy"]


async def test_samples_pick_the_most_engaged_the_newest_or_a_draw(store: EvidenceStore) -> None:
    items = [
        make_evidence(f"item {n}", published_at=at(n), engagement={"likes": 100 - n * 10})
        for n in range(5)
    ]
    store_items(store, items)
    ctx = processing_context(store)
    top = await query(ctx, limit=2, sample="top")
    assert [i["text"] for i in top.items] == ["item 0", "item 1"]
    recent = await query(ctx, limit=2, sample="recent")
    assert [i["text"] for i in recent.items] == ["item 4", "item 3"]
    drawn = await query(ctx, limit=3, sample="random")
    assert drawn.count == 3 and drawn.total == 5 and len(set(ids(drawn))) == 3


async def test_a_theme_filter_uses_the_stored_aggregate_and_tolerates_spelling(
    store: EvidenceStore,
) -> None:
    items = [make_evidence(f"post {n}") for n in range(4)]
    store_items(store, items)
    store.save_aggregates(
        RUN_ID,
        [
            ThemeAggregate(
                theme_label="Slow customer support",
                count=2,
                share=0.5,
                evidence_ids=[items[0].id, items[2].id],
            ),
            ThemeAggregate(theme_label="Pricing", count=1, share=0.25, evidence_ids=[items[1].id]),
        ],
    )
    ctx = processing_context(store)
    for spelling in ("Slow customer support", "slow customer supports", "SLOW  Customer-Support"):
        response = await query(ctx, filters={"theme": spelling}, limit=20)
        assert sorted(ids(response)) == sorted([items[0].id, items[2].id]), spelling
        assert response.warnings == []


async def test_an_unknown_theme_lists_the_stored_ones(store: EvidenceStore) -> None:
    item = make_evidence("post")
    store_items(store, [item])
    store.save_aggregates(
        RUN_ID,
        [ThemeAggregate(theme_label="Pricing", count=1, share=1.0, evidence_ids=[item.id])],
    )
    response = await query(processing_context(store), filters={"theme": "latency"})
    assert response.total == 0 and response.items == []
    assert response.warnings == [
        "no stored theme matches 'latency'; stored themes include: Pricing"
    ]


async def test_a_theme_filter_before_analysis_says_to_run_it(store: EvidenceStore) -> None:
    store_items(store, [make_evidence("post")])
    response = await query(processing_context(store), filters={"theme": "pricing"})
    assert response.total == 0
    assert response.warnings == ["no themes are stored yet; run analyze_text first"]


async def test_no_match_says_so(store: EvidenceStore) -> None:
    store_items(store, [make_evidence("post")])
    response = await query(processing_context(store), filters={"text_contains": "zzz"})
    assert (response.count, response.total) == (0, 0)
    assert response.warnings == ["no stored item matches these filters"]


async def test_an_unknown_filter_or_a_bad_limit_is_a_readable_error(store: EvidenceStore) -> None:
    ctx = processing_context(store)
    unknown = await query(ctx, filters={"colour": "red"})
    assert unknown.error_code == "invalid_input" and "filters.colour" in unknown.gaps[0]
    bad_limit = await query(ctx, limit=0)
    assert bad_limit.error_code == "invalid_input" and "limit" in bad_limit.gaps[0]
    bad_sample = await query(ctx, sample="oldest")
    assert bad_sample.error_code == "invalid_input" and "sample" in bad_sample.gaps[0]


async def test_the_call_is_reported_as_an_event(store: EvidenceStore) -> None:
    events: list[dict[str, Any]] = []
    store_items(store, [make_evidence("post")])
    await query(processing_context(store, emit=events.append), filters={"platform": "reddit"})
    [event] = events
    assert event["tool"] == "evidence_query" and event["count"] == 1
    assert event["args"] == {"filters": {"platform": "reddit"}}
