from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ValidationError

from edrak.agents.customer_trends.schemas.analysis import MetricResult, Theme, ThemeAggregate
from edrak.agents.customer_trends.schemas.common import (
    Budget,
    Confidence,
    Depth,
    FindingType,
    Platform,
    RequestBase,
    ToolResponse,
    ToolStatus,
    UseCase,
    effective_max_results,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.schemas.findings import (
    ControlSummary,
    CustomerTrendsResult,
    Finding,
)
from edrak.agents.customer_trends.schemas.task import QueryPlan, ReviewTarget, TaskBrief
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from tests.customer_trends.factories import load_brief, make_evidence, make_result


def roundtrip[M: BaseModel](model: M) -> M:
    cls = type(model)
    from_json = cls.model_validate_json(model.model_dump_json())
    from_dict = cls.model_validate(model.model_dump(mode="json"))
    assert from_json == model
    assert from_dict == model
    return from_json


def brief_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "task_id": "t1",
        "run_id": "r1",
        "use_case": "market_entry",
        "entity": "Acme",
        "question": "Is there demand?",
    }
    fields.update(overrides)
    return fields


@pytest.mark.parametrize("use_case", ["competitive_intelligence", "market_entry", "product_launch"])
def test_sample_briefs_validate_and_round_trip(use_case: str) -> None:
    brief = load_brief(use_case)
    assert brief.use_case == UseCase(use_case)
    roundtrip(brief)


def test_gitlab_pilot_brief_content() -> None:
    brief = load_brief("competitive_intelligence")
    assert brief.entity == "GitLab"
    assert brief.languages == ["en", "ar"]
    assert "GitHub Copilot" in brief.competitors


def test_task_brief_defaults() -> None:
    brief = TaskBrief(**brief_fields())  # type: ignore[arg-type]  # loosely typed test input
    assert brief.languages == ["ar", "en"]
    assert brief.depth is Depth.STANDARD
    assert brief.budget == Budget(max_tool_calls=60, max_cost_usd=2.0, max_seconds=300)
    assert brief.focus == []
    assert brief.competitors == []
    assert brief.geo is None


def test_task_brief_normalizes_codes() -> None:
    brief = TaskBrief(**brief_fields(geo=" eg ", languages=["AR", "En"]))  # type: ignore[arg-type]  # loosely typed test input
    assert brief.geo == "EG"
    assert brief.languages == ["ar", "en"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"geo": "EGY"},
        {"languages": []},
        {"languages": ["arabic"]},
        {"entity": ""},
        {"run_id": "../escape"},
        {"since": date(2026, 2, 1), "until": date(2026, 1, 1)},
        {"focus": ["revenue"]},
        {"unknown_field": 1},
        {"budget": {"max_tool_calls": 0}},
    ],
)
def test_task_brief_rejects_invalid_input(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TaskBrief(**brief_fields(**overrides))  # type: ignore[arg-type]  # loosely typed test input


def test_effective_max_results_clamps_by_depth() -> None:
    assert effective_max_results(Depth.LIGHT, None) == 50
    assert effective_max_results(Depth.STANDARD, None) == 200
    assert effective_max_results(Depth.DEEP, None) == 1000
    assert effective_max_results(Depth.LIGHT, 500) == 50
    assert effective_max_results(Depth.STANDARD, 30) == 30
    assert effective_max_results(Depth.DEEP, 5000) == 1000
    with pytest.raises(ValueError, match="at least 1"):
        effective_max_results(Depth.LIGHT, 0)


def test_request_base_clamps_max_results_and_keeps_none() -> None:
    clamped = RequestBase(run_id="r1", task_id="t1", depth="light", max_results=900)
    assert clamped.max_results == 50
    assert RequestBase(run_id="r1", task_id="t1", max_results=10).max_results == 10
    assert RequestBase(run_id="r1", task_id="t1").max_results is None
    with pytest.raises(ValidationError):
        RequestBase(run_id="r1", task_id="t1", max_results=0)


def test_request_base_defaults_and_date_order() -> None:
    base = RequestBase(run_id="r1", task_id="t1")
    assert base.languages == ["ar", "en"]
    assert base.depth is Depth.STANDARD
    with pytest.raises(ValidationError, match="since"):
        RequestBase(run_id="r1", task_id="t1", since="2026-05-01", until="2026-04-01")


def test_tool_response_limits_preview_items_and_snippets() -> None:
    preview = [{"id": str(i), "snippet": "x" * 500, "url": "https://e.test"} for i in range(9)]
    response = ToolResponse(status=ToolStatus.OK, count=9, preview=preview)
    assert len(response.preview) == 5
    assert all(len(item["snippet"]) == 200 for item in response.preview)
    assert response.preview[0]["id"] == "0"
    assert response.preview[0]["url"] == "https://e.test"


def test_tool_response_keeps_short_snippets_and_arabic_intact() -> None:
    response = ToolResponse(status="ok", preview=[{"snippet": "الدعم الفني بطيء"}])
    assert response.preview[0]["snippet"] == "الدعم الفني بطيء"


def test_tool_response_defaults_and_round_trip() -> None:
    response = ToolResponse(status="error", error_code="provider_exhausted", gaps=["no provider"])
    assert response.count == 0
    assert response.fallback_used is False
    assert response.batch_id is None
    roundtrip(response)
    with pytest.raises(ValidationError):
        ToolResponse(status="ok", surprise=1)  # type: ignore[call-arg]  # unknown field on purpose


def test_query_plan_limits_trend_keywords_and_round_trips_platform_keys() -> None:
    plan = QueryPlan(
        social_queries={Platform.X: ["gitlab duo"], Platform.REDDIT: ["copilot vs duo"]},
        hashtags={Platform.INSTAGRAM: ["#devops"]},
        trend_keywords=["gitlab duo", "github copilot"],
        review_targets=[ReviewTarget(store="google_play", target="com.example", country="eg")],
        rationale="why",
    )
    assert plan.review_targets[0].country == "EG"
    assert roundtrip(plan).social_queries[Platform.X] == ["gitlab duo"]
    with pytest.raises(ValidationError):
        QueryPlan(trend_keywords=["a", "b", "c", "d", "e", "f"])
    with pytest.raises(ValidationError):
        ReviewTarget(store="windows_store", target="x", country="EG")


def test_evidence_item_validation_and_round_trip() -> None:
    item = make_evidence(
        "الدعم الفني بطيء جدا",
        language="ar",
        engagement={"likes": 3, "views": 900, "rating": 4},
        metadata={"nested": {"a": [1, 2]}, "ar": "قيمة"},
    )
    assert item.engagement_total == 3
    assert roundtrip(item) == item
    fields = item.model_dump()
    for bad in ({"id": "short"}, {"content_hash": "abc"}, {"text": ""}, {"language": "arabic"}):
        with pytest.raises(ValidationError):
            EvidenceItem(**{**fields, **bad})
    with pytest.raises(ValidationError):
        EvidenceItem(**fields, surprise=1)


def test_evidence_datetimes_are_utc() -> None:
    cairo = timezone(timedelta(hours=2))
    item = make_evidence(
        "dates", published_at=datetime(2026, 7, 1, 14, 0, tzinfo=cairo)
    ).model_copy()
    revalidated = EvidenceItem.model_validate(item.model_dump())
    assert revalidated.published_at == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    naive = EvidenceItem.model_validate({**item.model_dump(), "published_at": datetime(2026, 7, 1)})
    assert naive.published_at == datetime(2026, 7, 1, tzinfo=UTC)


def test_evidence_filters_reject_unknown_values() -> None:
    assert EvidenceFilters(platform="x", min_engagement=3).platform is Platform.X
    with pytest.raises(ValidationError):
        EvidenceFilters(platform="myspace")
    with pytest.raises(ValidationError):
        EvidenceFilters(min_engagement=-1)


def test_trend_series_round_trip_and_range_check() -> None:
    series = TrendSeries(
        keyword="gitlab duo",
        geo="eg",
        timeframe="today 12-m",
        granularity="week",
        points=[(date(2026, 1, 4), 10.0), (date(2026, 1, 11), 100.0)],
        related_queries=["copilot"],
        source="apify",
        batch_id="b1",
    )
    assert series.geo == "EG"
    assert roundtrip(series).points[1] == (date(2026, 1, 11), 100.0)
    with pytest.raises(ValidationError, match="between 0 and 100"):
        TrendSeries.model_validate({**series.model_dump(), "points": [(date(2026, 1, 4), 150.0)]})
    raw = TrendSeries(
        **{**series.model_dump(), "normalized": False, "points": [(date(2026, 1, 4), 1500.0)]}
    )
    assert raw.points[0][1] == 1500.0


def test_theme_quotes_limits() -> None:
    quote = "ا" * 240
    theme = Theme(label="slow support", sentiment="negative", representative_quotes=[quote] * 3)
    assert roundtrip(theme).representative_quotes == [quote] * 3
    with pytest.raises(ValidationError):
        Theme(label="x", sentiment="negative", representative_quotes=[quote] * 4)
    with pytest.raises(ValidationError):
        Theme(label="x", sentiment="negative", representative_quotes=["a" * 241])
    with pytest.raises(ValidationError):
        Theme(label="x", sentiment="angry")


def test_theme_aggregate_and_metric_round_trip() -> None:
    aggregate = ThemeAggregate(
        theme_label="slow support",
        count=12,
        share=0.3,
        sentiment_mix={"negative": 0.8, "neutral": 0.2},
        by_platform={"x": 7, "reddit": 5},
        by_language={"en": 9, "ar": 3},
        recent_growth=0.25,
        evidence_ids=["0123456789abcdef"],
    )
    assert roundtrip(aggregate) == aggregate
    with pytest.raises(ValidationError):
        ThemeAggregate(theme_label="x", count=1, share=1.5)
    metric = MetricResult(metric_id="m1", metric="platform_mix", values={"x": 3}, batch_ids=["b1"])
    assert roundtrip(metric) == metric


def finding(**overrides: object) -> Finding:
    fields: dict[str, object] = {
        "id": "f1",
        "type": FindingType.PAIN_POINT,
        "claim": "Support response time is a recurring complaint.",
        "confidence": Confidence.HIGH,
        "evidence_ids": ["a" * 16, "b" * 16],
    }
    fields.update(overrides)
    return Finding(**fields)  # type: ignore[arg-type]  # loosely typed test input


def test_finding_with_two_sources_keeps_its_confidence() -> None:
    result = finding()
    assert result.confidence is Confidence.HIGH
    assert result.caveats == []


@pytest.mark.parametrize("ids", [["a" * 16], ["a" * 16, "a" * 16], []])
def test_finding_with_fewer_than_two_distinct_sources_is_downgraded(ids: list[str]) -> None:
    result = finding(evidence_ids=ids, caveats=["small sample"])
    assert result.confidence is Confidence.LOW
    assert result.caveats == ["small sample", "single_source"]


def test_finding_dedupes_ids_in_order_and_downgrade_is_idempotent() -> None:
    result = finding(evidence_ids=["b" * 16, "a" * 16, "b" * 16])
    assert result.evidence_ids == ["b" * 16, "a" * 16]
    single = roundtrip(finding(evidence_ids=["a" * 16]))
    assert single.caveats == ["single_source"]


def test_finding_rejects_bad_input() -> None:
    with pytest.raises(ValidationError):
        finding(claim="")
    with pytest.raises(ValidationError):
        finding(type="rumor")
    with pytest.raises(ValidationError):
        finding(use_case_relevance=["chess"])


def test_finding_metrics_keep_numeric_types_through_json() -> None:
    result = finding(metrics={"count": 12, "share": 0.25, "trend": "rising"})
    again = roundtrip(result)
    assert isinstance(again.metrics["count"], int)
    assert isinstance(again.metrics["share"], float)
    assert again.metrics["trend"] == "rising"


def test_control_summary_headline_word_limit() -> None:
    fields = {
        "status": "partial",
        "overall_confidence": "low",
        "findings_count": 0,
        "evidence_count": 0,
    }
    ControlSummary(headline=" ".join(["word"] * 60), **fields)  # type: ignore[arg-type]  # loosely typed test input
    with pytest.raises(ValidationError, match="60 words"):
        ControlSummary(headline=" ".join(["word"] * 61), **fields)  # type: ignore[arg-type]  # loosely typed test input
    with pytest.raises(ValidationError):
        ControlSummary(headline="ok", **{**fields, "status": "done"})  # type: ignore[arg-type]  # loosely typed test input


def test_result_round_trip_is_lossless_and_versioned() -> None:
    result = make_result(
        findings=[finding(claim="يشتكي المستخدمون من بطء الدعم الفني")],
        theme_aggregates=[ThemeAggregate(theme_label="support", count=2, share=0.5)],
        trend_series=[
            TrendSeries(
                keyword="duo",
                timeframe="today 12-m",
                granularity="week",
                points=[(date(2026, 1, 4), 12.5)],
                source="apify",
                batch_id="b1",
            )
        ],
        gaps=["no_reviews"],
        provenance={"models": {"planner": "m"}, "note": "ملاحظة", "n": 3},
    )
    assert result.schema_version == "1.0"
    assert result.worker == "customer_trends"
    again = roundtrip(result)
    assert again.findings[0].claim == "يشتكي المستخدمون من بطء الدعم الفني"
    assert again.trend_series[0].points == [(date(2026, 1, 4), 12.5)]
    assert again.provenance == result.provenance
    with pytest.raises(ValidationError):
        CustomerTrendsResult.model_validate({**result.model_dump(mode="json"), "worker": "other"})
