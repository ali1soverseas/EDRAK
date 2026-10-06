from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from edrak.agents.customer_trends.schemas.analysis import ThemeAggregate
from edrak.agents.customer_trends.schemas.common import (
    Confidence,
    Platform,
    SourceType,
    UseCase,
)
from edrak.agents.customer_trends.schemas.evidence import to_shared_evidence
from edrak.agents.customer_trends.schemas.findings import (
    ControlSummary,
    CustomerTrendsResult,
    EvidenceRef,
    Finding,
    to_worker_result,
    worker_status,
)
from edrak.agents.customer_trends.schemas.task import brief_from_task, run_id_for
from edrak.contracts import (
    SourceType as SharedSourceType,
)
from edrak.contracts import (
    UseCase as SharedUseCase,
)
from edrak.contracts import (
    WorkerResult,
    WorkerStatus,
)
from tests.customer_trends.contract_helpers import research_task
from tests.customer_trends.factories import BASE_DAY, load_brief, make_evidence

TODAY = date(2026, 10, 7)


# the task becomes a brief


def test_a_competitive_intelligence_task_becomes_the_pilot_brief() -> None:
    brief = brief_from_task(research_task(), today=TODAY)
    assert brief.task_id == "task-ct-0001" and brief.run_id == "task-ct-0001.a1"
    assert brief.use_case is UseCase.COMPETITIVE_INTELLIGENCE
    assert brief.entity == "GitLab"
    assert brief.question == "How do customers perceive GitLab Duo against GitHub Copilot?"
    assert brief.competitors == ["GitHub Copilot", "Atlassian", "Microsoft Azure DevOps"]
    assert brief.since == TODAY - timedelta(days=90) and brief.until is None
    assert brief.focus == ["pain_points", "sentiment"]
    assert brief.market is None and brief.geo is None
    assert brief.languages == ["ar", "en"] and brief.depth.value == "standard"
    assert brief.notes == "Focus: Customer sentiment and pain points around AI-assisted development"


def test_the_market_entry_value_of_the_shared_enum_maps_and_targets_describe_the_market() -> None:
    task = research_task(
        goal="Is there demand for specialty coffee delivery in Saudi Arabia?",
        focus="Demand trend and local sentiment",
        context={
            "use_case": SharedUseCase.MARKET_ENTRY_EXPANSION,
            "targets": ["Saudi Arabia", "urban consumers"],
            "focus_areas": [],
            "time_window_days": None,
        },
    )
    brief = brief_from_task(task, today=TODAY)
    assert brief.use_case is UseCase.MARKET_ENTRY
    assert brief.market == "Saudi Arabia, urban consumers" and brief.competitors == []
    assert brief.geo == "SA" and brief.since is None
    assert brief.focus == ["demand", "sentiment"]


def test_the_product_launch_task_keeps_its_targets_as_competitors() -> None:
    task = research_task(context={"use_case": SharedUseCase.PRODUCT_LAUNCH, "targets": ["YNAB"]})
    brief = brief_from_task(task, today=TODAY)
    assert brief.use_case is UseCase.PRODUCT_LAUNCH and brief.competitors == ["YNAB"]


@pytest.mark.parametrize(
    ("text", "geo"),
    [
        ("customers in Egypt", "EG"),
        ("the UAE market", "AE"),
        ("United Arab Emirates", "AE"),
        ("Saudi consumers", "SA"),
        ("Egypt and Jordan", "EG"),
        ("GitLab in Cairo", None),
        ("a hot-take about nothing", None),
    ],
)
def test_the_country_is_read_from_the_words_of_the_task(text: str, geo: str | None) -> None:
    task = research_task(focus=text, context={"targets": [], "focus_areas": []})
    assert brief_from_task(task, today=TODAY).geo == geo


def test_a_focus_nothing_matches_is_left_empty_for_the_use_case_defaults() -> None:
    task = research_task(focus="General overview", context={"focus_areas": ["context"]})
    assert brief_from_task(task, today=TODAY).focus == []


def test_a_focus_can_name_several_dimensions() -> None:
    task = research_task(
        focus="pain points, rising demand and competitor weaknesses",
        context={"focus_areas": []},
    )
    assert brief_from_task(task, today=TODAY).focus == ["pain_points", "demand", "competitor_gaps"]


def test_the_constraints_are_kept_in_the_notes() -> None:
    task = research_task(context={"constraints": ["public sources only", "last quarter"]})
    assert brief_from_task(task, today=TODAY).notes.endswith("; public sources only; last quarter")  # type: ignore[union-attr]  # notes is set


@pytest.mark.parametrize(
    ("task_id", "expected"),
    [
        ("task-1", "task-1.a1"),
        ("3f2c8e1a-5b6d-4e0f-9a7c-0123456789ab", "3f2c8e1a-5b6d-4e0f-9a7c-0123456789ab.a1"),
        ("a task / with: odd chars", "a-task-with-odd-chars.a1"),
        ("///", "task.a1"),
    ],
)
def test_the_run_id_is_valid_and_stable_for_one_attempt(task_id: str, expected: str) -> None:
    assert run_id_for(research_task(task_id=task_id)) == expected


def test_a_retry_is_a_new_run_and_a_long_id_stays_valid() -> None:
    first, retry = research_task(attempt=1), research_task(attempt=2)
    assert run_id_for(first) != run_id_for(retry) and run_id_for(retry).endswith(".a2")
    long_task = research_task(task_id="x" * 300)
    run_id = run_id_for(long_task)
    assert len(run_id) <= 128 and run_id.endswith(".a1")
    assert brief_from_task(long_task, today=TODAY).run_id == run_id
    assert run_id_for(research_task(task_id="x" * 300, attempt=2)) != run_id


# evidence becomes shared evidence


def test_an_item_becomes_a_compact_shared_record() -> None:
    item = make_evidence(
        "Support is slow",
        platform=Platform.REDDIT,
        language="en",
        engagement={"likes": 4},
        url="https://www.reddit.com/r/x/comments/1",
    )
    shared = to_shared_evidence(item)
    assert shared.evidence_id == item.id and shared.extracted_fact == "Support is slow"
    assert shared.excerpt is None and shared.is_synthetic is False
    assert shared.source_type is SharedSourceType.OTHER and shared.publisher == "reddit"
    assert shared.source_url == item.url and shared.retrieved_at == item.collected_at
    assert shared.metadata == {
        "kind": "social_post",
        "platform": "reddit",
        "language": "en",
        "published_at": item.published_at.isoformat(),  # type: ignore[union-attr]  # dated item
        "engagement": {"likes": 4},
        "provider": "fixture",
    }


def test_a_long_text_gives_a_short_fact_and_a_bounded_excerpt() -> None:
    shared = to_shared_evidence(make_evidence("word " * 2000, platform=Platform.X))
    assert len(shared.extracted_fact) <= 280 and shared.extracted_fact.endswith("…")
    assert shared.excerpt is not None and len(shared.excerpt) <= 500


@pytest.mark.parametrize(
    ("source_type", "snippet", "platform", "expected"),
    [
        (SourceType.WEB, False, None, SharedSourceType.WEB_PAGE),
        (SourceType.WEB, True, None, SharedSourceType.SEARCH_RESULT),
        (SourceType.NEWS, True, None, SharedSourceType.NEWS_ARTICLE),
        (SourceType.REVIEW, False, None, SharedSourceType.REVIEW_SITE),
        (SourceType.SOCIAL_COMMENT, False, Platform.YOUTUBE, SharedSourceType.OTHER),
        (SourceType.TREND_POINT, False, None, SharedSourceType.OTHER),
    ],
)
def test_the_source_types_map_to_the_shared_ones(
    source_type: SourceType, snippet: bool, platform: Platform | None, expected: SharedSourceType
) -> None:
    item = make_evidence(
        "an item", source_type=source_type, snippet_only=snippet, platform=platform
    )
    assert to_shared_evidence(item).source_type is expected


def test_a_page_without_a_platform_is_published_by_its_host_and_synthetic_data_is_flagged() -> None:
    item = make_evidence(
        "Press release",
        platform=None,
        source_type=SourceType.NEWS,
        url="https://news.example.test/a",
    )
    shared = to_shared_evidence(item, synthetic=True)
    assert shared.publisher == "news.example.test" and shared.is_synthetic is True
    assert "platform" not in shared.metadata


# the result becomes a worker result


def a_result(**overrides: Any) -> CustomerTrendsResult:
    brief = load_brief().model_copy(update={"task_id": "task-ct-0001", "run_id": "task-ct-0001.a1"})
    items = [make_evidence(f"evidence number {n}", platform=Platform.REDDIT) for n in range(3)]
    finding = Finding(
        id="f1",
        type="pain_point",
        claim="Support is slow in 12% of items.",
        confidence="medium",
        evidence_ids=[i.id for i in items[:2]],
        metrics={"share_pct": 12},
        caveats=["Reddit only"],
        related_gaps=["language_ar"],
    )
    fields: dict[str, Any] = {
        "task_id": brief.task_id,
        "run_id": brief.run_id,
        "brief": brief,
        "findings": [finding],
        "theme_aggregates": [
            ThemeAggregate(
                theme_label="slow support",
                count=3,
                share=0.5,
                evidence_ids=[i.id for i in items],
                representative_quotes=["evidence number 0"],
            )
        ],
        "evidence_ref": EvidenceRef(
            store_path="/data/evidence.db", run_id=brief.run_id, count=3, batch_ids=["b1"]
        ),
        "gaps": ["1 item(s) in 'ar'; at least 10 are needed"],
        "control_summary": ControlSummary(
            status="complete",
            headline="Plain headline.",
            overall_confidence=Confidence.MEDIUM,
            findings_count=1,
            evidence_count=3,
            budget_used={"tool_calls": 4.0, "cost_usd": 0.0, "seconds": 30.0},
        ),
        "provenance": {"ending": "finished"},
        "created_at": BASE_DAY,
    }
    fields.update(overrides)
    result = CustomerTrendsResult(**fields)
    result.__dict__["_items"] = items
    return result


def items_of(result: CustomerTrendsResult) -> list[Any]:
    items: list[Any] = result.__dict__["_items"]
    return items


def test_a_result_becomes_a_valid_compact_worker_result() -> None:
    result = a_result()
    shared = to_worker_result(
        result, items_of(result), task=research_task(attempt=2), location="/artifacts/result.json"
    )
    assert WorkerResult.model_validate(shared.model_dump()) == shared
    assert (shared.task_id, shared.worker.value, shared.attempt) == (
        "task-ct-0001",
        "customer_trends",
        2,
    )
    assert shared.status is WorkerStatus.COMPLETED and shared.error is None
    [finding] = shared.findings
    assert finding.finding_id == "task-ct-0001:f1" and finding.statement.startswith(
        "Support is slow"
    )
    assert finding.category.value == "customer_sentiment" and finding.confidence == 0.6
    assert finding.limitations == ["Reddit only", "gap: language_ar"]
    assert finding.evidence_ids == [i.id for i in items_of(result)[:2]]
    assert [e.evidence_id for e in shared.evidence] == finding.evidence_ids, "only cited evidence"
    assert shared.gaps == result.gaps and shared.confidence == 0.6
    assert shared.completed_at == BASE_DAY and shared.started_at == BASE_DAY - timedelta(seconds=30)


def test_the_metrics_and_the_pointer_to_the_artifact_ride_in_the_metadata() -> None:
    result = a_result()
    shared = to_worker_result(
        result, items_of(result), task=research_task(), location="/a/result.json"
    )
    assert shared.metadata["finding_metrics"] == {"task-ct-0001:f1": {"share_pct": 12}}
    assert shared.metadata["artifact"] == {
        "result_location": "/a/result.json",
        "evidence_store": "/data/evidence.db",
        "evidence_count": 3,
        "batch_ids": ["b1"],
    }
    assert shared.metadata["run_id"] == "task-ct-0001.a1"
    assert shared.metadata["control_summary"]["headline"] == "Plain headline."
    assert shared.metadata["themes"] == [
        {
            "theme_label": "slow support",
            "count": 3,
            "share": 0.5,
            "sentiment_mix": {},
            "recent_growth": None,
        }
    ]
    assert "evidence number" not in str(shared.metadata["themes"]), "no quotes or text"


def test_the_shared_result_never_carries_the_whole_stored_text() -> None:
    huge = make_evidence("long text " * 1000, platform=Platform.REDDIT)
    result = a_result(
        findings=[
            Finding(
                id="f1",
                type="trend",
                claim="Interest is up.",
                confidence="low",
                evidence_ids=[huge.id],
            )
        ]
    )
    shared = to_worker_result(result, [huge], task=research_task())
    [evidence] = shared.evidence
    assert len(evidence.extracted_fact) <= 280 and len(evidence.excerpt or "") <= 500
    assert len(shared.model_dump_json()) < 8_000


def test_a_reference_to_an_item_that_was_not_given_is_left_out() -> None:
    result = a_result()
    shared = to_worker_result(result, items_of(result)[:1], task=research_task())
    assert [r.evidence_id for r in shared.findings[0].evidence_refs] == [items_of(result)[0].id]


def summary(status: str, **overrides: Any) -> ControlSummary:
    fields: dict[str, Any] = {
        "status": status,
        "headline": "h",
        "overall_confidence": Confidence.LOW,
        "findings_count": 0,
        "evidence_count": 0,
    }
    fields.update(overrides)
    return ControlSummary(**fields)


def test_no_evidence_is_a_valid_no_evidence_result_without_confidence() -> None:
    result = a_result(findings=[], control_summary=summary("insufficient"), gaps=["only 0 items"])
    shared = to_worker_result(result, [], task=research_task())
    assert WorkerResult.model_validate(shared.model_dump()) == shared
    assert shared.status is WorkerStatus.NO_EVIDENCE and shared.confidence is None
    assert shared.findings == [] and shared.evidence == [] and shared.gaps == ["only 0 items"]


@pytest.mark.parametrize(
    ("status", "evidence", "findings", "ending", "expected"),
    [
        ("complete", 90, 3, "finished", WorkerStatus.COMPLETED),
        ("partial", 90, 3, "finished", WorkerStatus.PARTIAL),
        ("insufficient", 12, 0, "finished", WorkerStatus.PARTIAL),
        ("insufficient", 0, 0, "finished", WorkerStatus.NO_EVIDENCE),
        ("insufficient", 0, 0, "time_limit", WorkerStatus.NO_EVIDENCE),
        ("partial", 90, 3, "run_failed", WorkerStatus.PARTIAL),
        ("partial", 90, 0, "run_failed", WorkerStatus.FAILED),
    ],
)
def test_the_shared_status_follows_the_summary_the_evidence_and_the_ending(
    status: str, evidence: int, findings: int, ending: str, expected: WorkerStatus
) -> None:
    base = a_result()
    kept = base.findings[:1] if findings else []
    result = base.model_copy(
        update={
            "findings": kept,
            "control_summary": summary(status, evidence_count=evidence, findings_count=len(kept)),
            "provenance": {"ending": ending},
            "gaps": ["the run failed with OSError: disk full"] if ending == "run_failed" else [],
        }
    )
    assert worker_status(result)[0] is expected


def test_a_failed_run_without_findings_is_a_failed_result_with_the_error() -> None:
    result = a_result(
        findings=[],
        control_summary=summary("partial", evidence_count=90),
        provenance={"ending": "run_failed"},
        gaps=["the run failed with OSError: disk full"],
    )
    shared = to_worker_result(result, [], task=research_task())
    assert shared.status is WorkerStatus.FAILED
    assert shared.error == "the run failed with OSError: disk full" and shared.confidence is None
    assert WorkerResult.model_validate(shared.model_dump()) == shared


@pytest.mark.parametrize(("level", "value"), [("low", 0.3), ("medium", 0.6), ("high", 0.85)])
def test_the_confidence_levels_become_numbers(level: str, value: float) -> None:
    result = a_result(
        control_summary=summary("complete", overall_confidence=level, findings_count=1)
    )
    shared = to_worker_result(result, items_of(a_result()), task=research_task())
    assert shared.confidence == value


def test_the_adapter_uses_utc_times() -> None:
    result = a_result(created_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC))
    shared = to_worker_result(result, items_of(a_result()), task=research_task())
    assert shared.completed_at is not None and shared.completed_at.utcoffset() == timedelta(0)
