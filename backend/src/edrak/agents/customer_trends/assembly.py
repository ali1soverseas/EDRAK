"""What the writer reads and what the worker returns, built from the stores.

`writer_context` is the one input of the findings call. `build_result` makes the
`CustomerTrendsResult` of a run, whether it finished, ran out of time or failed, so every ending
leaves the same kind of artifact for the stages after this worker.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from edrak.agents.customer_trends.deps import WorkerDeps
from edrak.agents.customer_trends.gaps import Gap, critical_gaps, run_status
from edrak.agents.customer_trends.schemas.common import Confidence, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters
from edrak.agents.customer_trends.schemas.findings import (
    ControlSummary,
    CustomerTrendsResult,
    EvidenceRef,
    Finding,
)
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.tools.registry import COLLECTION_TOOL_NAMES, UNBUDGETED_ERRORS
from edrak.agents.customer_trends.tools.search_interest import summarize_series
from edrak.agents.customer_trends.utils.text import truncate

THEMES_IN_CONTEXT = 8
EVIDENCE_IDS_PER_THEME = 10
SAMPLE_TEXT_CHARS = 280
VOLUME_BUCKETS_IN_CONTEXT = 8
MAX_TREND_ITEMS = 50
MAX_CALLS_LISTED = 200
CALL_FIELDS = (
    "branch",
    "tool",
    "args",
    "provider",
    "fallback_used",
    "status",
    "count",
    "latency_ms",
    "cost",
    "error_code",
    "message",
)
_HIGH_SHARE = 0.5


def compact_values(metric: str, values: dict[str, Any]) -> dict[str, Any]:
    """A metric's numbers for the writer: a long volume series keeps its newest buckets."""
    if metric != "volume_over_time":
        return values
    buckets = values.get("buckets", {})
    kept = dict(list(buckets.items())[-VOLUME_BUCKETS_IN_CONTEXT:])
    return {**values, "buckets": kept, "buckets_shown": f"newest {len(kept)} of {len(buckets)}"}


def writer_context(
    deps: WorkerDeps,
    brief: TaskBrief,
    metric_ids: dict[str, str],
    samples: dict[str, list[dict[str, Any]]],
    gaps: Sequence[Gap],
) -> dict[str, Any]:
    """Theme aggregates, metrics, trend summaries, evidence samples and open gaps, as JSON data."""
    store, run_id = deps.store, brief.run_id
    themes = [
        {
            "label": a.theme_label,
            "count": a.count,
            "share": a.share,
            "sentiment_mix": a.sentiment_mix,
            "recent_growth": a.recent_growth,
            "by_platform": a.by_platform,
            "by_language": a.by_language,
            "evidence_ids": a.evidence_ids[:EVIDENCE_IDS_PER_THEME],
            "quotes": a.representative_quotes,
        }
        for a in store.get_aggregates(run_id)[:THEMES_IN_CONTEXT]
    ]
    metrics = []
    for name, metric_id in metric_ids.items():
        record = store.get_metric(run_id, metric_id)
        if record is not None:
            values = compact_values(record.metric, record.values)
            metrics.append({"name": name, "metric_id": metric_id, "values": values})
    summary = store.run_summary(run_id)
    trend_items = store.query(
        run_id, EvidenceFilters(source_type=SourceType.TREND_POINT), limit=MAX_TREND_ITEMS
    ).items
    trend_ids = {item.metadata.get("keyword"): item.id for item in trend_items}
    return {
        "coverage": {
            "evidence_items": summary.evidence_count,
            "by_platform": summary.by_platform,
            "by_source_type": summary.by_source_type,
            "by_language": summary.by_language,
        },
        "themes": themes,
        "metrics": metrics,
        "trends": [
            {**summarize_series(series), "evidence_id": trend_ids.get(series.keyword)}
            for series in store.get_trend_series(run_id)
        ],
        "samples": {
            label: [
                {**item, "text": truncate(str(item.get("text", "")), SAMPLE_TEXT_CHARS)}
                for item in items
            ]
            for label, items in samples.items()
        },
        "gaps": [{"id": gap.id, "description": gap.description} for gap in gaps],
    }


def overall_confidence(findings: Iterable[Finding], gaps: Iterable[Gap]) -> Confidence:
    """High when at least half the findings are high and no critical gap remains, medium when at
    least half are medium or better, else low (and low without findings)."""
    levels = [finding.confidence for finding in findings]
    if not levels:
        return Confidence.LOW
    high = sum(level is Confidence.HIGH for level in levels) / len(levels)
    medium_up = sum(level is not Confidence.LOW for level in levels) / len(levels)
    if high >= _HIGH_SHARE and not critical_gaps(gaps):
        return Confidence.HIGH
    return Confidence.MEDIUM if medium_up >= _HIGH_SHARE else Confidence.LOW


def fallback_headline(evidence: int, findings: int, gaps: Sequence[Gap], platforms: int) -> str:
    """A plain sentence from counts, for when the writer model cannot produce a headline."""
    critical = len(critical_gaps(gaps))
    return (
        f"{evidence} evidence items were collected from {platforms} platform(s) and "
        f"{findings} finding(s) were written; {critical} critical and {len(gaps) - critical} "
        "minor coverage gap(s) remain."
    )


def call_record(event: Mapping[str, Any]) -> dict[str, Any]:
    """One tool call as the provenance keeps it: what was asked, who answered, what came back."""
    return {key: event[key] for key in CALL_FIELDS if event.get(key) is not None}


def provenance(deps: WorkerDeps) -> dict[str, Any]:
    """Models, providers, fallbacks, node timings and a tool call summary, from the run's events.

    `provider_calls` and `cost_usd` are what the events say the provider calls were and cost; they
    equal the budget tracker's snapshot, which `budget` repeats.
    """
    events = deps.bus.events
    tool_events = [e for e in events if e.get("type") == "tool_called"]
    calls: dict[str, dict[str, Any]] = {}
    for event in tool_events:
        entry = calls.setdefault(str(event.get("tool")), {"calls": 0, "errors": 0, "cost_usd": 0.0})
        entry["calls"] += 1
        entry["errors"] += event.get("status") == "error"
        entry["cost_usd"] = round(entry["cost_usd"] + float(event.get("cost") or 0.0), 6)
    timings: Counter[str] = Counter()
    for event in events:
        if event.get("type") == "node_finished":
            timings[str(event.get("node"))] += int(event.get("duration_ms", 0))
    budgeted = [
        e
        for e in tool_events
        if e.get("tool") in COLLECTION_TOOL_NAMES and e.get("error_code") not in UNBUDGETED_ERRORS
    ]
    snapshot = deps.budget.snapshot()
    return {
        "models": deps.models_used,
        "provider_mode": deps.settings.edrak_provider_mode,
        "providers_used": dict(
            Counter(str(e["provider"]) for e in tool_events if e.get("provider"))
        ),
        "fallbacks": [
            {"tool": e.get("tool"), "provider": e.get("provider")}
            for e in tool_events
            if e.get("fallback_used")
        ],
        "tool_calls": calls,
        "calls": [call_record(e) for e in tool_events[:MAX_CALLS_LISTED]],
        "provider_calls": len(budgeted),
        "cost_usd": round(sum(float(e.get("cost") or 0.0) for e in budgeted), 6),
        "budget": {"tool_calls": snapshot.tool_calls, "cost_usd": round(snapshot.cost_usd, 6)},
        "node_timings_ms": dict(timings),
        "replans": sum(1 for e in events if e.get("type") == "replan"),
        "provider_health": deps.providers.health(),
    }


def build_result(
    deps: WorkerDeps,
    brief: TaskBrief,
    *,
    gaps: Sequence[Gap],
    warnings: Sequence[str],
    headline: str | None = None,
    ending: str = "finished",
) -> CustomerTrendsResult:
    """The run's result from what is stored now. Without a headline a counted one is written.
    `ending` records how the run ended: finished, time_limit or run_failed."""
    store, run_id = deps.store, brief.run_id
    findings = store.get_findings(run_id)
    summary = store.run_summary(run_id)
    budget = deps.budget.snapshot()
    platforms = sum(1 for platform in summary.by_platform if platform != "unknown")
    control = ControlSummary(
        status=run_status(gaps),
        headline=headline
        or fallback_headline(summary.evidence_count, len(findings), gaps, platforms),
        overall_confidence=overall_confidence(findings, gaps),
        findings_count=len(findings),
        evidence_count=summary.evidence_count,
        coverage={
            "by_platform": summary.by_platform,
            "by_source_type": summary.by_source_type,
            "by_language": summary.by_language,
        },
        gaps=[gap.description for gap in gaps],
        warnings=list(warnings),
        budget_used={
            "tool_calls": float(budget.tool_calls),
            "cost_usd": budget.cost_usd,
            "seconds": round(budget.seconds, 2),
        },
    )
    return CustomerTrendsResult(
        task_id=brief.task_id,
        run_id=run_id,
        brief=brief,
        findings=findings,
        theme_aggregates=store.get_aggregates(run_id),
        trend_series=store.get_trend_series(run_id),
        evidence_ref=EvidenceRef(
            store_path=str(store.path),
            run_id=run_id,
            count=summary.evidence_count,
            batch_ids=summary.batch_ids,
        ),
        gaps=control.gaps,
        control_summary=control,
        provenance={
            **provenance(deps),
            "ending": ending,
            "open_gaps": [gap.model_dump() for gap in gaps],
        },
        created_at=datetime.now(UTC),
    )
