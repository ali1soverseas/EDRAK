import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from edrak.agents.customer_trends.schemas.analysis import MetricResult, ThemeAggregate
from edrak.agents.customer_trends.schemas.common import Confidence, FindingType, Platform
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.schemas.findings import Finding
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.evidence_store import (
    EvidenceStore,
    UnknownRunError,
)
from tests.customer_trends.factories import (
    BASE_DAY,
    RUN_ID,
    TASK_ID,
    load_brief,
    make_evidence,
    synthetic_evidence,
)


def ids(items: list[EvidenceItem]) -> list[str]:
    return [item.id for item in items]


# runs, batches, migrations


def test_migration_is_applied_once_and_data_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "evidence.db"
    with EvidenceStore(path) as first:
        assert first.schema_version() == 1
        first.create_run(RUN_ID, TASK_ID)
        first.add_batch(RUN_ID, TASK_ID, "t", [make_evidence("persisted")])
    with EvidenceStore(path) as second:
        assert second.schema_version() == 1
        assert second.run_summary(RUN_ID).evidence_count == 1


def test_database_uses_wal_mode(store: EvidenceStore) -> None:
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_create_run_is_idempotent(store: EvidenceStore) -> None:
    brief = load_brief()
    assert store.create_run(brief.run_id, brief.task_id, brief) is True
    assert store.create_run(brief.run_id, "another-task") is False
    assert store.run_summary(brief.run_id).task_id == brief.task_id


def test_writes_for_an_unknown_run_are_rejected(store: EvidenceStore) -> None:
    with pytest.raises(UnknownRunError):
        store.add_batch("nope", TASK_ID, "t", [make_evidence("x")])
    with pytest.raises(UnknownRunError):
        store.save_findings("nope", [])
    with pytest.raises(UnknownRunError):
        store.run_summary("nope")


def test_add_batch_stamps_ids_and_reports_counts(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    items = [make_evidence(f"item {i}", run_id="other", task_id="other") for i in range(3)]
    batch_id, inserted, duplicates = store.add_batch(RUN_ID, TASK_ID, "social", items, {"q": "x"})
    assert (inserted, duplicates) == (3, 0)
    stored = store.get_items(RUN_ID, ids(items))
    assert {item.batch_id for item in stored} == {batch_id}
    assert {item.run_id for item in stored} == {RUN_ID}
    assert {item.task_id for item in stored} == {TASK_ID}
    assert store.batch_meta(RUN_ID, batch_id) == {"q": "x"}
    assert store.batch_meta(RUN_ID, "missing") is None


def test_empty_batch_is_allowed_and_carries_metadata(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    batch_id, inserted, duplicates = store.add_batch(RUN_ID, TASK_ID, "trends", [], {"k": 1})
    assert (inserted, duplicates) == (0, 0)
    assert store.batch_meta(RUN_ID, batch_id) == {"k": 1}
    assert store.run_summary(RUN_ID).batch_ids == [batch_id]


def test_stored_items_round_trip_exactly(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    original = make_evidence(
        "نص عربي مع English و 🔥",
        language="ar",
        engagement={"likes": 4, "rating": 5},
        metadata={"nested": {"قيمة": [1, 2.5, None]}},
        snippet_only=True,
    )
    batch_id, _, _ = store.add_batch(RUN_ID, TASK_ID, "t", [original])
    [stored] = store.get_items(RUN_ID, [original.id])
    assert stored == original.model_copy(update={"batch_id": batch_id})


# dedupe


def test_repeating_a_batch_inserts_nothing(
    loaded_store: EvidenceStore, evidence: list[EvidenceItem]
) -> None:
    batch_id, inserted, duplicates = loaded_store.add_batch(RUN_ID, TASK_ID, "again", evidence)
    assert (inserted, duplicates) == (0, 40)
    assert loaded_store.run_summary(RUN_ID).evidence_count == 40
    assert batch_id


def test_duplicates_inside_one_batch_are_counted(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    item = make_evidence("same text")
    _, inserted, duplicates = store.add_batch(
        RUN_ID, TASK_ID, "t", [item, item, make_evidence("other")]
    )
    assert (inserted, duplicates) == (2, 1)


def test_same_text_on_different_platforms_is_not_a_duplicate(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    batch = [
        make_evidence("shared text", platform=Platform.X),
        make_evidence("shared text", platform=Platform.REDDIT),
        make_evidence("shared text", platform=None),
    ]
    assert store.add_batch(RUN_ID, TASK_ID, "t", batch)[1:] == (3, 0)


def test_platformless_items_are_deduplicated_by_content(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    first = make_evidence("an article", platform=None)
    again = first.model_copy(update={"id": "f" * 16, "url": "https://other.test/a"})
    assert store.add_batch(RUN_ID, TASK_ID, "t", [first, again])[1:] == (1, 1)


def test_cosmetic_text_differences_are_duplicates(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    plain = make_evidence("مدرسة جميلة")
    decorated = make_evidence("مَدْرَسَة   جـميلة").model_copy(update={"id": "e" * 16})
    assert store.add_batch(RUN_ID, TASK_ID, "t", [plain, decorated])[1:] == (1, 1)


def test_the_same_content_in_two_runs_is_stored_twice(store: EvidenceStore) -> None:
    item = make_evidence("shared")
    for run_id in ("run-a", "run-b"):
        store.create_run(run_id, TASK_ID)
        assert store.add_batch(run_id, TASK_ID, "t", [item])[1:] == (1, 0)


# reads


def test_get_items_follows_the_requested_order_and_skips_unknown_ids(
    loaded_store: EvidenceStore, evidence: list[EvidenceItem]
) -> None:
    wanted = [evidence[5].id, "0" * 16, evidence[2].id, evidence[5].id]
    assert ids(loaded_store.get_items(RUN_ID, wanted)) == [evidence[5].id, evidence[2].id]
    assert loaded_store.get_items(RUN_ID, []) == []
    assert loaded_store.get_items("other-run", [evidence[0].id]) == []


def test_existing_ids(loaded_store: EvidenceStore, evidence: list[EvidenceItem]) -> None:
    probe = [evidence[0].id, evidence[39].id, "0" * 16]
    assert loaded_store.existing_ids(RUN_ID, probe) == {evidence[0].id, evidence[39].id}
    assert loaded_store.existing_ids(RUN_ID, []) == set()
    assert loaded_store.existing_ids("other-run", probe) == set()


def test_count_by(loaded_store: EvidenceStore) -> None:
    assert loaded_store.count_by(RUN_ID, "platform") == {
        "reddit": 10,
        "x": 8,
        "unknown": 8,
        "youtube": 6,
        "facebook": 3,
        "tiktok": 3,
        "instagram": 2,
    }
    assert loaded_store.count_by(RUN_ID, "language") == {"en": 25, "ar": 15}
    assert loaded_store.count_by(RUN_ID, "source_type")["social_post"] == 22
    assert loaded_store.count_by(RUN_ID, "snippet_only") == {"0": 35, "1": 5}
    assert loaded_store.count_by("other-run", "platform") == {}


def test_count_by_can_leave_one_source_type_out(loaded_store: EvidenceStore) -> None:
    assert loaded_store.count_by(RUN_ID, "source_type", exclude_source_type="news") == {
        "social_post": 22,
        "social_comment": 10,
        "review": 3,
    }
    assert loaded_store.count_by(RUN_ID, "language", exclude_source_type="news") == {
        "en": 22,
        "ar": 13,
    }


def test_count_by_batches_and_invalid_field(loaded_store: EvidenceStore) -> None:
    batch_id, _, _ = loaded_store.add_batch(
        RUN_ID,
        TASK_ID,
        "extra",
        [make_evidence("new item", platform=Platform.TIKTOK, language="en")],
    )
    assert loaded_store.count_by(RUN_ID, "platform", [batch_id]) == {"tiktok": 1}
    assert loaded_store.count_by(RUN_ID, "platform", []) == {}
    with pytest.raises(ValueError, match="cannot count by"):
        loaded_store.count_by(RUN_ID, "text; DROP TABLE evidence")


# query filters


def run_query(store: EvidenceStore, **filters: object) -> tuple[list[EvidenceItem], int]:
    result = store.query(RUN_ID, EvidenceFilters(**filters), limit=100)  # type: ignore[arg-type]  # loosely typed test input
    return result.items, result.total


def test_query_without_filters_returns_everything_up_to_the_limit(
    loaded_store: EvidenceStore,
) -> None:
    result = loaded_store.query(RUN_ID, limit=7)
    assert len(result.items) == 7
    assert result.total == 40


def test_query_filters_by_platform_source_type_and_language(loaded_store: EvidenceStore) -> None:
    items, total = run_query(loaded_store, platform="reddit")
    assert total == 10 and {i.platform for i in items} == {Platform.REDDIT}
    items, total = run_query(loaded_store, source_type="social_comment")
    assert total == 10
    items, total = run_query(loaded_store, language="ar")
    assert total == 15 and {i.language for i in items} == {"ar"}
    items, total = run_query(loaded_store, platform="x", language="ar")
    assert total == 4


def test_query_text_contains_is_case_and_diacritic_insensitive(
    loaded_store: EvidenceStore, evidence: list[EvidenceItem]
) -> None:
    excel = len([e for e in evidence if "excel" in e.text.lower()])
    support_ar = len([e for e in evidence if "الدعم" in e.text])
    assert excel > 0 and support_ar > 0
    assert run_query(loaded_store, text_contains="EXCEL")[1] == excel
    assert run_query(loaded_store, text_contains="الدَّعم")[1] == support_ar
    assert run_query(loaded_store, text_contains="no such phrase")[1] == 0
    assert run_query(loaded_store, text_contains="%")[1] == 0


def test_query_min_engagement_counts_interactions_not_views_or_ratings(
    loaded_store: EvidenceStore, evidence: list[EvidenceItem]
) -> None:
    items, total = run_query(loaded_store, min_engagement=40)
    assert total == len([e for e in evidence if e.engagement_total >= 40])
    assert all(i.engagement_total >= 40 for i in items)
    assert run_query(loaded_store, min_engagement=0)[1] == 40


def test_query_date_bounds_apply_to_publication_date(loaded_store: EvidenceStore) -> None:
    items, total = run_query(loaded_store, since=date(2026, 7, 11))
    assert total == 30
    assert min(i.published_at for i in items if i.published_at) >= datetime(
        2026, 7, 11, tzinfo=BASE_DAY.tzinfo
    )
    assert run_query(loaded_store, until=date(2026, 7, 10))[1] == 10
    assert run_query(loaded_store, since=date(2026, 7, 11), until=date(2026, 7, 15))[1] == 5
    assert run_query(loaded_store, since=date(2027, 1, 1))[1] == 0


def test_query_date_bounds_exclude_undated_items(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    store.add_batch(RUN_ID, TASK_ID, "t", [make_evidence("undated", published_at=None)])
    assert run_query(store)[1] == 1
    assert run_query(store, since=date(2000, 1, 1))[1] == 0


def test_query_batch_filter(loaded_store: EvidenceStore) -> None:
    batch_id, _, _ = loaded_store.add_batch(
        RUN_ID, TASK_ID, "second", [make_evidence("fresh item")]
    )
    items, total = run_query(loaded_store, batch_ids=[batch_id])
    assert total == 1 and items[0].text == "fresh item"
    assert run_query(loaded_store, batch_ids=[])[1] == 0


def test_query_theme_filter_uses_stored_aggregates(
    loaded_store: EvidenceStore, evidence: list[EvidenceItem]
) -> None:
    chosen = [evidence[1].id, evidence[20].id]
    loaded_store.save_aggregates(
        RUN_ID,
        [ThemeAggregate(theme_label="Slow Support", count=2, share=0.05, evidence_ids=chosen)],
    )
    items, total = run_query(loaded_store, theme="slow support")
    assert total == 2 and set(ids(items)) == set(chosen)
    assert run_query(loaded_store, theme="unknown theme")[1] == 0
    assert run_query(loaded_store, theme="slow support", platform="x")[1] == len(
        [i for i in items if i.platform is Platform.X]
    )


def test_query_combines_filters_and_reports_total_beyond_limit(loaded_store: EvidenceStore) -> None:
    result = loaded_store.query(
        RUN_ID, EvidenceFilters(language="en", source_type="social_post"), limit=3
    )
    assert len(result.items) == 3
    assert result.total == 12


# sampling


def test_top_sample_orders_by_interactions(
    loaded_store: EvidenceStore, evidence: list[EvidenceItem]
) -> None:
    result = loaded_store.query(RUN_ID, limit=40, sample="top")
    expected = sorted(
        evidence,
        key=lambda e: (
            -e.engagement_total,
            -(e.published_at.timestamp() if e.published_at else 0),
            e.id,
        ),
    )
    assert ids(result.items) == ids(expected)
    assert result.items[0].engagement_total == max(e.engagement_total for e in evidence)


def test_recent_sample_orders_by_publication_date_with_undated_last(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    old = make_evidence("old", published_at=BASE_DAY)
    new = make_evidence("new", published_at=BASE_DAY + timedelta(days=5))
    undated = make_evidence("undated", published_at=None)
    store.add_batch(RUN_ID, TASK_ID, "t", [old, undated, new])
    result = store.query(RUN_ID, limit=10, sample="recent")
    assert [i.text for i in result.items] == ["new", "old", "undated"]


def test_random_sample_is_reproducible_with_a_seed(loaded_store: EvidenceStore) -> None:
    first = loaded_store.query(RUN_ID, limit=8, sample="random", seed=7)
    again = loaded_store.query(RUN_ID, limit=8, sample="random", seed=7)
    other = loaded_store.query(RUN_ID, limit=8, sample="random", seed=8)
    assert ids(first.items) == ids(again.items)
    assert len(set(ids(first.items))) == 8
    assert set(ids(first.items)) != set(ids(other.items))
    assert first.total == 40


def test_random_sample_respects_filters_and_small_populations(loaded_store: EvidenceStore) -> None:
    result = loaded_store.query(
        RUN_ID, EvidenceFilters(platform="instagram"), limit=10, sample="random", seed=1
    )
    assert len(result.items) == 2 and result.total == 2
    assert {i.platform for i in result.items} == {Platform.INSTAGRAM}


def test_query_rejects_a_non_positive_limit(loaded_store: EvidenceStore) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        loaded_store.query(RUN_ID, limit=0)


# analysis products, results and summaries


def aggregate(label: str, count: int) -> ThemeAggregate:
    return ThemeAggregate(theme_label=label, count=count, share=0.1, evidence_ids=["a" * 16])


def test_aggregates_are_replaced_per_run_and_sorted_by_count(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    store.save_aggregates(RUN_ID, [aggregate("price", 3), aggregate("support", 9)])
    assert [a.theme_label for a in store.get_aggregates(RUN_ID)] == ["support", "price"]
    store.save_aggregates(RUN_ID, [aggregate("speed", 1)])
    assert [a.theme_label for a in store.get_aggregates(RUN_ID)] == ["speed"]
    assert store.get_aggregates("other-run") == []


def test_trend_series_round_trip_and_batch_filter(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)

    def series(keyword: str, batch_id: str) -> TrendSeries:
        return TrendSeries(
            keyword=keyword,
            timeframe="today 12-m",
            granularity="week",
            points=[(date(2026, 1, 4), 10.0)],
            source="apify",
            batch_id=batch_id,
        )

    store.save_trend_series(RUN_ID, [series("duo", "b1"), series("copilot", "b2")])
    assert [s.keyword for s in store.get_trend_series(RUN_ID)] == ["duo", "copilot"]
    assert [s.keyword for s in store.get_trend_series(RUN_ID, ["b2"])] == ["copilot"]
    assert store.get_trend_series(RUN_ID, []) == []
    assert store.get_trend_series(RUN_ID)[0].points == [(date(2026, 1, 4), 10.0)]


def test_metrics_round_trip_and_replace(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    record = MetricResult(
        metric_id="m1", metric="platform_mix", values={"x": 0.4}, batch_ids=["b1"]
    )
    store.save_metric(RUN_ID, record)
    assert store.get_metric(RUN_ID, "m1") == record
    store.save_metric(RUN_ID, record.model_copy(update={"values": {"x": 0.5}}))
    fetched = store.get_metric(RUN_ID, "m1")
    assert fetched is not None and fetched.values == {"x": 0.5}
    assert store.get_metric(RUN_ID, "missing") is None
    assert store.get_metric("other-run", "m1") is None


def finding(finding_id: str, claim: str) -> Finding:
    return Finding(
        id=finding_id,
        type=FindingType.PAIN_POINT,
        claim=claim,
        confidence=Confidence.MEDIUM,
        evidence_ids=["a" * 16, "b" * 16],
    )


def test_findings_upsert_keeps_order(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    store.save_findings(RUN_ID, [finding("f1", "first"), finding("f2", "second")])
    store.save_findings(RUN_ID, [finding("f1", "first, revised"), finding("f3", "third")])
    stored = store.get_findings(RUN_ID)
    assert [(f.id, f.claim) for f in stored] == [
        ("f1", "first, revised"),
        ("f2", "second"),
        ("f3", "third"),
    ]
    assert store.get_findings("other-run") == []


def test_result_registration_and_summaries(loaded_store: EvidenceStore) -> None:
    store = loaded_store
    assert store.result_location(RUN_ID) is None
    store.save_findings(RUN_ID, [finding("f1", "claim")])
    store.register_result(RUN_ID, "runs/r/result.json")
    store.register_result(RUN_ID, "runs/r/result-2.json")
    summary = store.run_summary(RUN_ID)
    assert summary.evidence_count == 40
    assert summary.findings_count == 1
    assert summary.result_location == "runs/r/result-2.json"
    assert summary.by_language == {"en": 25, "ar": 15}
    assert summary.by_platform["reddit"] == 10
    assert len(summary.batch_ids) == 1


def test_list_runs_is_newest_first(store: EvidenceStore) -> None:
    for run_id in ("run-1", "run-2", "run-3"):
        store.create_run(run_id, TASK_ID)
    assert [r.run_id for r in store.list_runs()] == ["run-3", "run-2", "run-1"]


def test_from_settings_creates_the_database_under_data_dir(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, edrak_data_dir=tmp_path / "worker-data")
    with EvidenceStore.from_settings(settings) as opened:
        assert opened.path == tmp_path / "worker-data" / "evidence.db"
    assert (tmp_path / "worker-data" / "evidence.db").is_file()


# concurrency


def test_threads_sharing_one_store_insert_without_loss(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, TASK_ID)
    shared = [make_evidence(f"shared {i}") for i in range(20)]
    errors: list[BaseException] = []
    totals: list[tuple[int, int]] = []

    def work(worker: int) -> None:
        try:
            own = [make_evidence(f"worker {worker} item {i}") for i in range(25)]
            _, inserted, duplicates = store.add_batch(RUN_ID, TASK_ID, f"w{worker}", own + shared)
            totals.append((inserted, duplicates))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert store.run_summary(RUN_ID).evidence_count == 8 * 25 + 20
    assert sum(inserted for inserted, _ in totals) == 8 * 25 + 20
    assert sum(duplicates for _, duplicates in totals) == 7 * 20


def test_threads_with_their_own_connections_share_one_file(tmp_path: Path) -> None:
    path = tmp_path / "shared.db"
    with EvidenceStore(path) as setup:
        setup.create_run(RUN_ID, TASK_ID)
    errors: list[BaseException] = []

    def work(worker: int) -> None:
        try:
            with EvidenceStore(path) as own:
                for i in range(10):
                    own.add_batch(RUN_ID, TASK_ID, "t", [make_evidence(f"w{worker} i{i}")])
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with EvidenceStore(path) as check:
        assert check.run_summary(RUN_ID).evidence_count == 60


def test_synthetic_evidence_is_a_stable_fixture() -> None:
    first, second = synthetic_evidence(), synthetic_evidence()
    assert ids(first) == ids(second)
    assert len(set(ids(first))) == 40
