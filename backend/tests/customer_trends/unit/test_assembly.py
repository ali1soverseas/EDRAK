from datetime import timedelta
from pathlib import Path

import pytest

from edrak.agents.customer_trends.assembly import (
    build_result,
    compact_values,
    fallback_headline,
    overall_confidence,
    provenance,
    writer_context,
)
from edrak.agents.customer_trends.gaps import Gap
from edrak.agents.customer_trends.schemas.analysis import MetricResult, ThemeAggregate
from edrak.agents.customer_trends.schemas.common import Confidence, SourceType
from edrak.agents.customer_trends.schemas.findings import Finding
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from tests.customer_trends.factories import BASE_DAY, RUN_ID, TASK_ID, load_brief, make_evidence
from tests.customer_trends.graph_helpers import worker_deps
from tests.customer_trends.tool_helpers import trend_series


def finding(confidence: str, fid: str = "f") -> Finding:
    return Finding(
        id=fid,
        type="pain_point",
        claim="A claim.",
        confidence=confidence,
        evidence_ids=["a" * 16, "b" * 16],
    )


def gap(severity: str, gap_id: str = "g") -> Gap:
    return Gap(id=gap_id, severity=severity, description=f"{gap_id} is open", suggested_action="a")  # type: ignore[arg-type]  # literal in tests


@pytest.mark.parametrize(
    ("levels", "gaps", "expected"),
    [
        ([], [], Confidence.LOW),
        (["high", "high", "low"], [], Confidence.HIGH),
        (["high", "low", "low"], [], Confidence.LOW),
        (["high", "high"], [gap("critical")], Confidence.MEDIUM),
        (["high", "high"], [gap("minor")], Confidence.HIGH),
        (["medium", "medium", "low"], [], Confidence.MEDIUM),
        (["medium", "low", "low", "low"], [], Confidence.LOW),
        (["low"], [], Confidence.LOW),
    ],
)
def test_overall_confidence_follows_the_findings_and_the_critical_gaps(
    levels: list[str], gaps: list[Gap], expected: Confidence
) -> None:
    findings = [finding(level, f"f{n}") for n, level in enumerate(levels)]
    assert overall_confidence(findings, gaps) is expected


def test_the_counted_headline_states_counts_and_stays_short() -> None:
    text = fallback_headline(
        106, 3, [gap("critical", "a"), gap("minor", "b"), gap("minor", "c")], 3
    )
    assert text == (
        "106 evidence items were collected from 3 platform(s) and 3 finding(s) were written; "
        "1 critical and 2 minor coverage gap(s) remain."
    )
    assert len(text.split()) <= 60


def test_a_long_volume_series_keeps_its_newest_buckets_for_the_writer() -> None:
    buckets = {f"2026-{month:02d}-01": month for month in range(1, 13)}
    values = {"total": 78, "buckets": buckets}
    compact = compact_values("volume_over_time", values)
    assert list(compact["buckets"]) == list(buckets)[-8:]
    assert compact["buckets_shown"] == "newest 8 of 12" and compact["total"] == 78
    assert compact_values("platform_mix", values) is values


def test_the_provenance_summarizes_the_events_of_a_run(
    tmp_path: Path, store: EvidenceStore
) -> None:
    deps = worker_deps(load_brief(), tmp_path, store)
    deps.model("writer")
    for event in (
        {"type": "node_finished", "node": "social", "duration_ms": 120},
        {"type": "node_finished", "node": "social", "duration_ms": 30},
        {"type": "tool_called", "tool": "social_search", "provider": "apify", "status": "ok"},
        {
            "type": "tool_called",
            "tool": "social_search",
            "provider": "socialcrawl",
            "status": "error",
            "fallback_used": True,
        },
        {"type": "tool_called", "tool": "analyze_text", "provider": None, "status": "ok"},
        {"type": "replan"},
    ):
        deps.bus.emit(event)
    assert provenance(deps) == {
        "models": {"writer": "scripted-chat"},
        "provider_mode": "live",
        "providers_used": {"apify": 1, "socialcrawl": 1},
        "fallbacks": [{"tool": "social_search", "provider": "socialcrawl"}],
        "tool_calls": {
            "social_search": {"calls": 2, "errors": 1, "cost_usd": 0.0},
            "analyze_text": {"calls": 1, "errors": 0, "cost_usd": 0.0},
        },
        "provider_calls": 2,
        "cost_usd": 0.0,
        "budget": {"tool_calls": 0, "cost_usd": 0.0},
        "node_timings_ms": {"social": 150},
        "replans": 1,
    }


def test_the_writer_context_holds_aggregates_metrics_trends_samples_and_gaps(
    tmp_path: Path, loaded_store: EvidenceStore
) -> None:
    brief = load_brief().model_copy(update={"run_id": RUN_ID, "task_id": TASK_ID})
    deps = worker_deps(brief, tmp_path, loaded_store)
    ids = [item.id for item in loaded_store.query(RUN_ID, limit=40).items]
    loaded_store.save_aggregates(
        RUN_ID,
        [
            ThemeAggregate(
                theme_label=f"theme {n}",
                count=20 - n,
                share=0.1,
                evidence_ids=ids[: 15 + n],
                representative_quotes=["a quote"],
            )
            for n in range(10)
        ],
    )
    loaded_store.save_metric(
        RUN_ID,
        MetricResult(
            metric_id="m_1",
            metric="platform_mix",
            values={"total": 40, "percent": {"reddit": 25.0}},
        ),
    )
    trend_item = make_evidence(
        "Search interest for gitlab duo is up",
        platform=None,
        source_type=SourceType.TREND_POINT,
        metadata={"keyword": "gitlab duo"},
    )
    batch_id, _, _ = loaded_store.add_batch(RUN_ID, TASK_ID, "search_interest", [trend_item])
    loaded_store.save_trend_series(
        RUN_ID, [trend_series("gitlab duo").model_copy(update={"batch_id": batch_id})]
    )
    long_text = "x" * 600
    samples = {"top": [{"id": ids[0], "text": long_text, "platform": "reddit"}]}
    context = writer_context(
        deps, brief, {"mix": "m_1", "gone": "m_missing"}, samples, [gap("critical", "platforms")]
    )

    assert len(context["themes"]) == 8
    assert context["themes"][0]["label"] == "theme 0"
    assert len(context["themes"][0]["evidence_ids"]) == 10, "ten ids per theme are enough to cite"
    assert context["themes"][0]["quotes"] == ["a quote"]
    assert context["metrics"] == [
        {"name": "mix", "metric_id": "m_1", "values": {"total": 40, "percent": {"reddit": 25.0}}}
    ]
    [trend] = context["trends"]
    assert trend["keyword"] == "gitlab duo" and trend["evidence_id"] == trend_item.id
    assert trend["first"] == 10.0 and trend["last"] == 90.0 and trend["direction"] == "up"
    assert len(context["samples"]["top"][0]["text"]) == 280
    assert context["gaps"] == [{"id": "platforms", "description": "platforms is open"}]
    assert context["coverage"]["evidence_items"] == 41
    assert context["coverage"]["by_platform"]["reddit"] == 10


def test_a_result_is_built_from_what_is_stored(tmp_path: Path, loaded_store: EvidenceStore) -> None:
    brief = load_brief().model_copy(update={"run_id": RUN_ID, "task_id": TASK_ID})
    deps = worker_deps(brief, tmp_path, loaded_store)
    ids = [item.id for item in loaded_store.query(RUN_ID, limit=3).items]
    loaded_store.save_findings(
        RUN_ID, [finding("medium").model_copy(update={"evidence_ids": ids[:2]})]
    )
    result = build_result(
        deps, brief, gaps=[gap("minor", "language_ar")], warnings=["w1"], headline="Plain headline."
    )
    summary = result.control_summary
    assert (summary.status, summary.headline, summary.findings_count) == (
        "complete",
        "Plain headline.",
        1,
    )
    assert summary.evidence_count == 40 == result.evidence_ref.count
    assert summary.gaps == ["language_ar is open"] == result.gaps and summary.warnings == ["w1"]
    assert summary.overall_confidence is Confidence.MEDIUM
    assert set(summary.budget_used) == {"tool_calls", "cost_usd", "seconds"}
    assert result.evidence_ref.store_path == str(loaded_store.path)
    assert result.evidence_ref.batch_ids == loaded_store.run_summary(RUN_ID).batch_ids
    assert result.brief == brief and result.created_at - BASE_DAY > timedelta(days=0)


def test_a_result_without_a_headline_gets_the_counted_one_and_a_critical_gap_makes_it_partial(
    tmp_path: Path, loaded_store: EvidenceStore
) -> None:
    brief = load_brief().model_copy(update={"run_id": RUN_ID, "task_id": TASK_ID})
    deps = worker_deps(brief, tmp_path, loaded_store)
    partial = build_result(deps, brief, gaps=[gap("critical", "platforms")], warnings=[])
    assert partial.control_summary.status == "partial"
    assert partial.control_summary.headline.startswith(
        "40 evidence items were collected from 6 platform(s)"
    )
    thin = build_result(deps, brief, gaps=[gap("critical", "evidence_total")], warnings=[])
    assert thin.control_summary.status == "insufficient"
