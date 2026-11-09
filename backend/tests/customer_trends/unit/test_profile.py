"""The evidence profile: findings written by code from the stored evidence and metrics."""

import pytest

from edrak.agents.customer_trends.gaps import Gap
from edrak.agents.customer_trends.profile import (
    MIN_VENDOR_ITEMS,
    SENTIMENT_OVERVIEW,
    profile_findings,
    store_sentiment_overview,
)
from edrak.agents.customer_trends.schemas.analysis import MetricResult
from edrak.agents.customer_trends.schemas.findings import Finding
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools.submit_findings import check_finding, load_verdict_phrases
from tests.customer_trends.factories import RUN_ID, load_brief

BRIEF = load_brief("competitive_intelligence").model_copy(
    update={
        "run_id": RUN_ID,
        "task_id": "task-test-001",
        "entity": "Alpha",
        "competitors": ["Beta"],
        "focus_terms": ["Nowhere"],
    }
)


def build(
    store: EvidenceStore,
    metrics: dict[str, MetricResult] | None = None,
    gaps: list[Gap] | None = None,
    brief: TaskBrief = BRIEF,
    others: list[Finding] | None = None,
) -> dict[str, Finding]:
    ids = {}
    for name, record in (metrics or {}).items():
        store.save_metric(RUN_ID, record)
        ids[name] = record.metric_id
    found = profile_findings(store, brief, ids, gaps or [], others or [], min_language_items=10)
    return {f.id: f for f in found}


def metric(name: str, values: dict) -> MetricResult:
    return MetricResult(metric_id=f"m_{name}", metric=name, values=values)


def test_nothing_is_written_for_a_run_without_evidence(store: EvidenceStore) -> None:
    store.create_run(RUN_ID, "task-test-001")
    assert build(store) == {}


def test_the_scope_names_the_terms_that_no_item_mentions(loaded_store: EvidenceStore) -> None:
    findings = build(loaded_store)
    scope = findings["p_scope"]
    assert "No collected item mentions" in scope.claim and "Nowhere" in scope.claim
    record = loaded_store.get_metric(RUN_ID, str(scope.metrics["scope_coverage_metric_id"]))
    assert record is not None and record.values["items"] == 40
    assert record.values["mentions"]["Nowhere"] == 0


def test_the_sentiment_split_is_stated_as_a_sample_and_not_a_population(
    loaded_store: EvidenceStore,
) -> None:
    overview = metric(
        SENTIMENT_OVERVIEW,
        {
            "analyzed": 34,
            "total_items": 34,
            "counts": {"positive": 24, "neutral": 7, "negative": 2, "mixed": 1},
            "percent": {"positive": 70.6, "neutral": 20.6, "negative": 5.9, "mixed": 2.9},
        },
    )
    sentiment = build(loaded_store, {SENTIMENT_OVERVIEW: overview})["p_sentiment"]
    assert "24 positive (70.6%)" in sentiment.claim and "1 mixed (2.9%)" in sentiment.claim
    assert "not of the whole customer population" in sentiment.claim
    assert sentiment.metrics["sentiment_overview_metric_id"] == "m_sentiment_overview"


def test_zero_engagement_is_not_read_as_no_engagement(loaded_store: EvidenceStore) -> None:
    zero = {"items": 40, "mean": 0.0, "median": 0.0, "p90": 0.0}
    findings = build(loaded_store, {"engagement_stats": metric("engagement_stats", {"all": zero})})
    assert "does not mean zero real-world engagement" in findings["p_engagement"].claim


def test_a_company_with_few_items_makes_the_comparison_insufficient(
    loaded_store: EvidenceStore,
) -> None:
    voice = metric(
        "share_of_voice",
        {"items": 40, "mentions": {"Alpha": 25, "Beta": 3}, "items_without_a_mention": 12},
    )
    findings = build(loaded_store, {"share_of_voice": voice})
    assert "12 items name none of them" in findings["p_voice"].claim
    assert "not market share" in findings["p_voice"].claim
    thin = findings["p_comparability"]
    assert "Beta (3)" in thin.claim and "Alpha" not in thin.claim
    assert thin.metrics["min_items"] == MIN_VENDOR_ITEMS


def test_enough_items_for_every_company_writes_no_comparability_finding(
    loaded_store: EvidenceStore,
) -> None:
    voice = metric(
        "share_of_voice",
        {"items": 40, "mentions": {"Alpha": 25, "Beta": 15}, "items_without_a_mention": 0},
    )
    assert "p_comparability" not in build(loaded_store, {"share_of_voice": voice})


def test_a_language_with_too_few_items_is_called_out(loaded_store: EvidenceStore) -> None:
    mix = metric("language_mix", {"total": 34, "counts": {"en": 33, "ar": 1}, "percent": {}})
    claim = build(loaded_store, {"language_mix": mix})["p_language"].claim
    assert "33 in en and 1 in ar" in claim and "not be robust" in claim


def test_the_last_finding_gives_the_status_confidence_and_each_open_gap(
    loaded_store: EvidenceStore,
) -> None:
    gap = Gap(
        id="branch_social",
        severity="critical",
        description="the social branch reported an error: all 4 tool call(s) failed",
        suggested_action="run again",
    )
    last = build(loaded_store, gaps=[gap])["p_gaps"]
    assert last.claim.startswith("The result is partial with low overall confidence")
    assert "the social branch reported an error" in last.claim
    assert last.related_gaps == ["branch_social"]
    clean = build(loaded_store)["p_gaps"]
    assert "no open coverage gap" in clean.claim


@pytest.mark.parametrize("with_metrics", [False, True])
def test_every_profile_finding_passes_the_checks_of_a_written_one(
    loaded_store: EvidenceStore, with_metrics: bool
) -> None:
    metrics = {}
    if with_metrics:
        metrics = {
            "volume_over_time": metric(
                "volume_over_time",
                {
                    "total": 40,
                    "dated": 39,
                    "undated": 1,
                    "buckets": {"2026-03-02": 7},
                    "peak_bucket": "2026-03-02",
                    "peak_count": 7,
                },
            ),
            "engagement_stats": metric(
                "engagement_stats",
                {"all": {"items": 40, "mean": 12.5, "median": 9.0, "p90": 31.4}},
            ),
        }
    findings = build(loaded_store, metrics, [])
    assert {"p_scope", "p_composition", "p_gaps"} <= set(findings)
    phrases = load_verdict_phrases()
    for finding in findings.values():
        assert check_finding(finding, loaded_store, RUN_ID, phrases) == [], finding.claim


def test_the_sentiment_overview_is_stored_for_findings_to_cite(
    loaded_store: EvidenceStore,
) -> None:
    answer = {
        "analyzed": 4,
        "total_items": 5,
        "sentiment": {"counts": {"positive": 3, "negative": 1}, "percent": {"positive": 75.0}},
    }
    metric_id = store_sentiment_overview(loaded_store, RUN_ID, answer, ["b_1"])
    assert metric_id is not None
    record = loaded_store.get_metric(RUN_ID, metric_id)
    assert record is not None and record.values["analyzed"] == 4
    assert store_sentiment_overview(loaded_store, RUN_ID, {"analyzed": 4}, ["b_1"]) is None
