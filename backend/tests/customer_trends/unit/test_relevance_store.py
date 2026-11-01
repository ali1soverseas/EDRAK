import pytest

from edrak.agents.customer_trends.schemas.common import Platform, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore, UnknownRunError
from tests.customer_trends.factories import RUN_ID, TASK_ID, make_evidence

COMMENT = SourceType.SOCIAL_COMMENT


def comment(text: str, platform: Platform = Platform.INSTAGRAM, **kw: object) -> EvidenceItem:
    return make_evidence(text, platform=platform, source_type=COMMENT, **kw)  # type: ignore[arg-type]


@pytest.fixture
def judged(store: EvidenceStore) -> tuple[EvidenceStore, dict[str, EvidenceItem]]:
    """A run with a post, three comments, and a verdict on two of the comments."""
    items = {
        "post": make_evidence("Launch day for the new card", platform=Platform.INSTAGRAM),
        "good": comment("the card fees are too high compared to others"),
        "noise": comment("tag your friends below please"),
        "unjudged": comment("when does it ship to Alexandria"),
    }
    store.create_run(RUN_ID, TASK_ID)
    store.add_batch(RUN_ID, TASK_ID, "t", list(items.values()))
    store.mark_relevance(
        RUN_ID,
        {
            items["good"].id: {
                "relevance": "relevant",
                "relevance_reason": "llm_relevant",
                "filter_stage": "c",
            },
            items["noise"].id: {
                "relevance": "irrelevant",
                "relevance_reason": "llm_irrelevant",
                "filter_stage": "c",
            },
        },
    )
    return store, items


def test_a_mark_is_merged_into_the_metadata_and_the_text_is_untouched(judged) -> None:  # type: ignore[no-untyped-def]  # a test fixture value
    store, items = judged
    stored = store.get_items(RUN_ID, [items["good"].id, items["noise"].id])
    good, noise = stored
    assert good.text == items["good"].text and noise.text == items["noise"].text
    assert (good.relevance, good.relevance_reason, good.filter_stage) == (
        "relevant",
        "llm_relevant",
        "c",
    )
    assert (noise.relevance, noise.relevance_reason, noise.filter_stage) == (
        "irrelevant",
        "llm_irrelevant",
        "c",
    )
    assert store.get_items(RUN_ID, [items["unjudged"].id])[0].relevance is None


def test_a_mark_keeps_the_metadata_the_provider_wrote(store: EvidenceStore) -> None:
    item = comment(
        "price is high", metadata={"post_url": "https://x.test/p/1", "likes_hidden": True}
    )
    store.create_run(RUN_ID, TASK_ID)
    store.add_batch(RUN_ID, TASK_ID, "t", [item])
    store.mark_relevance(RUN_ID, {item.id: {"relevance": "relevant", "duplicate_count": 2}})
    stored = store.get_items(RUN_ID, [item.id])[0]
    assert stored.metadata == {
        "post_url": "https://x.test/p/1",
        "likes_hidden": True,
        "relevance": "relevant",
        "duplicate_count": 2,
    }


def test_marking_returns_how_many_items_were_found_and_ignores_unknown_ids(
    store: EvidenceStore,
) -> None:
    item = comment("hello there friend")
    store.create_run(RUN_ID, TASK_ID)
    store.add_batch(RUN_ID, TASK_ID, "t", [item])
    assert (
        store.mark_relevance(
            RUN_ID, {item.id: {"relevance": "relevant"}, "f" * 16: {"relevance": "relevant"}}
        )
        == 1
    )
    with pytest.raises(UnknownRunError):
        store.mark_relevance("no-such-run", {item.id: {"relevance": "relevant"}})


def test_reads_leave_out_irrelevant_items_by_default(judged) -> None:  # type: ignore[no-untyped-def]  # a test fixture value
    store, items = judged
    seen = {i.id for i in store.query(RUN_ID, limit=50).items}
    assert seen == {
        items[k].id for k in ("post", "good", "unjudged")
    }  # unjudged counts as relevant
    assert store.query(RUN_ID, limit=50).total == 3
    assert store.count_by(RUN_ID, "source_type") == {"social_comment": 2, "social_post": 1}
    assert store.count_by(RUN_ID, "platform") == {"instagram": 3}
    assert store.existing_ids(RUN_ID, [i.id for i in items.values()]) == {
        items[k].id for k in ("post", "good", "unjudged")
    }
    summary = store.run_summary(RUN_ID)
    assert summary.evidence_count == 3 and summary.by_source_type == {
        "social_comment": 2,
        "social_post": 1,
    }


def test_an_audit_read_still_sees_every_item(judged) -> None:  # type: ignore[no-untyped-def]  # a test fixture value
    store, items = judged
    everything = store.query(RUN_ID, limit=50, include_irrelevant=True)
    assert everything.total == 4 and items["noise"].id in {i.id for i in everything.items}
    assert store.count_by(RUN_ID, "source_type", include_irrelevant=True)["social_comment"] == 3
    assert items["noise"].id in store.existing_ids(
        RUN_ID, [items["noise"].id], include_irrelevant=True
    )
    assert store.get_items(RUN_ID, [items["noise"].id])  # an explicit lookup by id still finds it


def test_filters_combine_with_the_relevance_rule(judged) -> None:  # type: ignore[no-untyped-def]  # a test fixture value
    store, items = judged
    only = store.query(
        RUN_ID, EvidenceFilters(source_type=COMMENT, text_contains="tag your friends"), limit=10
    )
    assert only.total == 0
    shown = store.query(
        RUN_ID,
        EvidenceFilters(source_type=COMMENT, text_contains="tag your friends"),
        limit=10,
        include_irrelevant=True,
    )
    assert [i.id for i in shown.items] == [items["noise"].id]


def test_judged_items_come_back_in_storage_order_with_the_ids_already_judged(judged) -> None:  # type: ignore[no-untyped-def]  # a test fixture value
    store, items = judged
    found, done = store.judged_items(RUN_ID, [COMMENT])
    assert [i.id for i in found] == [items["good"].id, items["noise"].id, items["unjudged"].id]
    assert done == {items["good"].id, items["noise"].id}
    posts, _ = store.judged_items(RUN_ID, ["social_post"])
    assert [i.id for i in posts] == [items["post"].id]


def test_relevance_rows_count_judged_items_by_platform_verdict_reason_and_stage(
    store: EvidenceStore,
) -> None:
    items = [
        comment("one two three", Platform.INSTAGRAM),
        comment("four five six", Platform.INSTAGRAM),
        comment("seven eight nine ten", Platform.X),
    ]
    store.create_run(RUN_ID, TASK_ID)
    store.add_batch(RUN_ID, TASK_ID, "t", items)
    store.mark_relevance(
        RUN_ID,
        {
            items[0].id: {
                "relevance": "relevant",
                "relevance_reason": "term_match",
                "filter_stage": "b",
            },
            items[1].id: {
                "relevance": "irrelevant",
                "relevance_reason": "spam",
                "filter_stage": "a",
            },
            items[2].id: {
                "relevance": "relevant",
                "relevance_reason": "llm_relevant",
                "filter_stage": "c",
            },
        },
    )
    rows = store.relevance_rows(RUN_ID, [COMMENT])
    assert rows == [
        {
            "platform": "instagram",
            "relevance": "irrelevant",
            "reason": "spam",
            "stage": "a",
            "n": 1,
            "chars": len("four five six"),
        },
        {
            "platform": "instagram",
            "relevance": "relevant",
            "reason": "term_match",
            "stage": "b",
            "n": 1,
            "chars": len("one two three"),
        },
        {
            "platform": "x",
            "relevance": "relevant",
            "reason": "llm_relevant",
            "stage": "c",
            "n": 1,
            "chars": len("seven eight nine ten"),
        },
    ]
    assert store.relevance_rows(RUN_ID, ["review"]) == []
