from datetime import UTC, datetime
from typing import Any

import pytest

from edrak.agents.customer_trends.schemas.common import Platform, SourceType, ToolStatus
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools import compute_metrics
from edrak.agents.customer_trends.tools.base import ToolContext, invoke_tool
from tests.customer_trends.factories import RUN_ID, TASK_ID, make_evidence
from tests.customer_trends.tool_helpers import processing_context, trend_series


def day(month: int, number: int) -> datetime:
    return datetime(2026, month, number, 12, 0, tzinfo=UTC)


def store_items(store: EvidenceStore, items: list[EvidenceItem]) -> str:
    store.create_run(RUN_ID, TASK_ID)
    batch_id, _, _ = store.add_batch(RUN_ID, TASK_ID, "test", items)
    return batch_id


async def metric(ctx: ToolContext, name: str, **arguments: Any) -> Any:
    return await invoke_tool(ctx, compute_metrics.SPEC, {"metric": name, **arguments})


# 2026-06-29 and 2026-07-06 are Mondays; the 5th is the Sunday between them.
DATED = [
    make_evidence("monday post", published_at=day(6, 29)),
    make_evidence("wednesday post", published_at=day(7, 1)),
    make_evidence("sunday post", published_at=day(7, 5), platform=Platform.X),
    make_evidence("next monday post", published_at=day(7, 6), platform=Platform.X),
    make_evidence("no date", published_at=None, platform=None, source_type=SourceType.NEWS),
]


async def test_volume_over_time_counts_weeks_that_start_on_monday(store: EvidenceStore) -> None:
    store_items(store, DATED)
    response = await metric(processing_context(store), "volume_over_time")
    assert response.status is ToolStatus.OK and response.count == 5
    assert response.values == {
        "total": 5,
        "dated": 4,
        "undated": 1,
        "buckets": {"2026-06-29": 3, "2026-07-06": 1},
        "peak_bucket": "2026-06-29",
        "peak_count": 3,
    }
    assert response.params == {"bucket": "week", "filters": {}}


async def test_volume_over_time_by_day_lists_only_days_with_items(store: EvidenceStore) -> None:
    store_items(store, DATED)
    response = await metric(processing_context(store), "volume_over_time", params={"bucket": "day"})
    assert response.values["buckets"] == {
        "2026-06-29": 1,
        "2026-07-01": 1,
        "2026-07-05": 1,
        "2026-07-06": 1,
    }
    assert response.values["peak_bucket"] == "2026-06-29"


async def test_volume_over_time_uses_utc_days(store: EvidenceStore) -> None:
    late = make_evidence(
        "late sunday in utc", published_at=datetime(2026, 7, 5, 23, 30, tzinfo=UTC)
    )
    store_items(store, [late])
    response = await metric(processing_context(store), "volume_over_time")
    assert response.values["buckets"] == {"2026-06-29": 1}


ENGAGEMENT = [
    make_evidence("r1", engagement={"likes": 2, "views": 5000}),
    make_evidence("r2", engagement={"likes": 3, "replies": 1}),
    make_evidence("r3", engagement={"likes": 10, "replies": 2}),
    *[make_evidence(f"x{n}", platform=Platform.X, engagement={"likes": n}) for n in (1, 3, 5, 7)],
    make_evidence("a news item", platform=None, source_type=SourceType.NEWS),
]


async def test_engagement_stats_per_platform_and_overall(store: EvidenceStore) -> None:
    store_items(store, ENGAGEMENT)
    response = await metric(processing_context(store), "engagement_stats")
    # overall, ascending: 0 1 2 3 4 5 7 12: mean 34/8, median (3+4)/2, p90 at rank 6.3
    assert response.values["all"] == {"items": 8, "mean": 4.25, "median": 3.5, "p90": 8.5}
    assert response.values["by_platform"] == {
        "x": {"items": 4, "mean": 4.0, "median": 4.0, "p90": 6.4},
        "reddit": {"items": 3, "mean": 6.0, "median": 4, "p90": 10.4},
        "news": {"items": 1, "mean": 0.0, "median": 0, "p90": 0.0},
    }


async def test_share_of_voice_counts_items_that_name_each_brand(store: EvidenceStore) -> None:
    texts = [
        "I switched from GitHub Copilot to GitLab Duo last week",
        "gitlab duo is great, GITLAB DUO again",
        "GitHub Copilot keeps hallucinating",
        "No tools are mentioned in this one",
        "ممتاز من امازون",
        "GitLab Duology is a different thing",
    ]
    store_items(store, [make_evidence(text) for text in texts])
    response = await metric(
        processing_context(store),
        "share_of_voice",
        params={"entity": "GitLab Duo", "competitors": ["GitHub Copilot", "أمازون"]},
    )
    # Duo: items 1 and 2 (an item counts once however often it repeats the name, and
    # "Duology" is another word); Copilot: items 1 and 3; the Arabic name: item 5, which
    # spells its alef without the hamza.
    assert response.values == {
        "items": 6,
        "mentions": {"GitLab Duo": 2, "GitHub Copilot": 2, "أمازون": 1},
        "percent": {"GitLab Duo": 40.0, "GitHub Copilot": 40.0, "أمازون": 20.0},
        "items_without_a_mention": 2,
    }


async def test_share_of_voice_takes_the_entity_from_the_brief_defaults(
    store: EvidenceStore,
) -> None:
    store_items(store, [make_evidence("gitlab duo rocks"), make_evidence("nothing here")])
    ctx = processing_context(store, defaults={"entity": "GitLab Duo"})
    response = await metric(ctx, "share_of_voice")
    assert response.values["mentions"] == {"GitLab Duo": 1}


async def test_share_of_voice_needs_an_entity(store: EvidenceStore) -> None:
    store_items(store, [make_evidence("anything")])
    response = await metric(processing_context(store), "share_of_voice")
    assert response.status is ToolStatus.ERROR and response.error_code == "invalid_input"
    assert "entity" in response.gaps[0]


async def test_share_of_voice_with_no_mentions_says_so(store: EvidenceStore) -> None:
    store_items(store, [make_evidence("nothing relevant")])
    response = await metric(processing_context(store), "share_of_voice", params={"entity": "Zoho"})
    assert response.values["percent"] == {"Zoho": 0.0}
    assert response.warnings == ["no item mentions any of the names"]


async def test_platform_mix_and_language_mix_over_the_synthetic_set(
    loaded_store: EvidenceStore,
) -> None:
    ctx = processing_context(loaded_store)
    platforms = await metric(ctx, "platform_mix")
    assert platforms.values["total"] == 40
    assert platforms.values["counts"] == {
        "reddit": 10,
        "x": 8,
        "youtube": 6,
        "news": 5,
        "facebook": 3,
        "review": 3,
        "tiktok": 3,
        "instagram": 2,
    }
    assert platforms.values["percent"]["news"] == 12.5
    languages = await metric(ctx, "language_mix")
    assert languages.values["counts"] == {"en": 25, "ar": 15}
    assert languages.values["percent"] == {"en": 62.5, "ar": 37.5}


async def test_missing_languages_are_counted_as_unknown(store: EvidenceStore) -> None:
    store_items(
        store, [make_evidence("short", language=None), make_evidence("another", language="en")]
    )
    response = await metric(processing_context(store), "language_mix")
    assert response.values["counts"] == {"en": 1, "unknown": 1}


async def test_filters_narrow_what_is_counted(loaded_store: EvidenceStore) -> None:
    ctx = processing_context(loaded_store)
    response = await metric(
        ctx, "language_mix", params={"filters": {"platform": "x", "language": "ar"}}
    )
    assert response.values["counts"] == {"ar": 4}
    assert response.count == 4
    assert response.params == {"filters": {"platform": "x", "language": "ar"}}


async def test_trend_points_are_not_people_talking_and_are_not_counted(
    store: EvidenceStore,
) -> None:
    trend = make_evidence(
        "Search interest for gitlab duo is up",
        platform=None,
        source_type=SourceType.TREND_POINT,
        language="en",
    )
    store_items(store, [make_evidence("a real post"), trend])
    response = await metric(processing_context(store), "platform_mix")
    assert response.values["counts"] == {"reddit": 1}


def trend_batch(store: EvidenceStore, **series: list[float]) -> str:
    batch_id = store_items(store, [])
    store.save_trend_series(
        RUN_ID,
        [
            trend_series(keyword, values).model_copy(update={"batch_id": batch_id})
            for keyword, values in series.items()
        ],
    )
    return batch_id


async def test_trend_growth_compares_the_first_and_last_quarter_of_the_points(
    store: EvidenceStore,
) -> None:
    trend_batch(
        store,
        short=[10, 10, 20, 20, 30, 30, 40, 40],
        longer=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 100, 100],
    )
    response = await metric(processing_context(store), "trend_growth")
    # 8 points: window 2, mean(10, 10) = 10 to mean(40, 40) = 40. 12 points: window 3,
    # mean(10, 20, 30) = 20 to mean(100, 100, 100) = 100.
    assert response.values == {
        "short": {
            "insufficient_data": False,
            "points": 8,
            "window": 2,
            "first_mean": 10.0,
            "last_mean": 40.0,
            "percent_change": 300.0,
        },
        "longer": {
            "insufficient_data": False,
            "points": 12,
            "window": 3,
            "first_mean": 20.0,
            "last_mean": 100.0,
            "percent_change": 400.0,
        },
    }


async def test_trend_growth_flags_short_series_and_zero_baselines(store: EvidenceStore) -> None:
    trend_batch(
        store,
        brief=[1, 2, 3, 4, 5, 6, 7],
        flat=[0, 0, 0, 0, 5, 5, 5, 5],
        down=[50, 50, 25, 25, 20, 20, 10, 10],
    )
    response = await metric(processing_context(store), "trend_growth")
    assert response.values["brief"] == {"insufficient_data": True, "points": 7}
    assert response.values["flat"]["zero_baseline"] is True
    assert "percent_change" not in response.values["flat"]
    assert response.values["down"]["percent_change"] == -80.0


async def test_trend_growth_for_one_keyword(store: EvidenceStore) -> None:
    trend_batch(store, a=[1] * 8, b=[2] * 8)
    response = await metric(processing_context(store), "trend_growth", params={"keyword": "B"})
    assert list(response.values) == ["b"]


async def test_trend_growth_without_a_stored_series_asks_for_search_interest(
    store: EvidenceStore,
) -> None:
    store_items(store, [make_evidence("anything")])
    response = await metric(processing_context(store), "trend_growth")
    assert response.error_code == "no_data" and "search_interest" in response.gaps[0]


async def test_a_metric_is_stored_and_its_id_is_stable(store: EvidenceStore) -> None:
    batch_id = store_items(store, DATED)
    ctx = processing_context(store)
    first = await metric(ctx, "volume_over_time", batch_ids=[batch_id])
    again = await metric(ctx, "volume_over_time")
    other = await metric(ctx, "volume_over_time", params={"bucket": "day"})
    assert first.metric_id == again.metric_id != other.metric_id
    assert first.batch_ids == [batch_id]
    stored = store.get_metric(RUN_ID, first.metric_id)
    assert stored is not None
    assert stored.values == first.values and stored.metric == "volume_over_time"
    assert stored.params == first.params and stored.batch_ids == [batch_id]


async def test_batch_ids_limit_the_items_and_unknown_batches_are_refused(
    store: EvidenceStore,
) -> None:
    first = store_items(store, [make_evidence("in the first batch")])
    store.add_batch(RUN_ID, TASK_ID, "test", [make_evidence("in the second batch")])
    ctx = processing_context(store)
    assert (await metric(ctx, "platform_mix", batch_ids=[first])).values["total"] == 1
    assert (await metric(ctx, "platform_mix")).values["total"] == 2
    refused = await metric(ctx, "platform_mix", batch_ids=["b_missing"])
    assert refused.error_code == "unknown_batch" and "b_missing" in refused.gaps[0]


async def test_no_stored_evidence_is_an_error_not_an_empty_metric(store: EvidenceStore) -> None:
    response = await metric(processing_context(store), "platform_mix")
    assert response.status is ToolStatus.ERROR and response.error_code == "no_data"


@pytest.mark.parametrize(
    ("name", "params", "problem"),
    [
        ("volume_over_time", {"bucket": "month"}, "params.bucket"),
        ("share_of_voice", {"competitors": "not a list"}, "params.competitors"),
        ("platform_mix", {"unknown_option": 1}, "params.unknown_option"),
        ("trend_growth", {"keyword": ""}, "params.keyword"),
    ],
)
async def test_bad_params_are_reported_with_their_path(
    store: EvidenceStore, name: str, params: dict[str, Any], problem: str
) -> None:
    store_items(store, [make_evidence("anything")])
    response = await metric(processing_context(store), name, params=params)
    assert response.error_code == "invalid_input" and problem in response.gaps[0]


async def test_an_unknown_metric_is_refused_by_validation(store: EvidenceStore) -> None:
    response = await metric(processing_context(store), "median_mood")
    assert response.error_code == "invalid_input" and "metric" in response.gaps[0]


async def test_the_call_is_reported_as_an_event(store: EvidenceStore) -> None:
    events: list[dict[str, Any]] = []
    store_items(store, DATED)
    await metric(processing_context(store, emit=events.append), "volume_over_time")
    [event] = events
    assert event["type"] == "tool_called" and event["tool"] == "compute_metrics"
    assert event["status"] == "ok" and event["count"] == 5 and event["provider"] is None
    assert event["args"] == {"metric": "volume_over_time"}
