"""DeepEval spans and local JSON persistence for the customer-trends worker."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
    from edrak.agents.customer_trends.store.evidence_store import EvidenceStore

from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.schemas.task import TaskBrief

_context: ContextVar[tuple[TaskBrief, str, Path] | None] = ContextVar(
    "customer_trends_trace_context",
    default=None,
)
_write_lock = threading.Lock()


def bind_trace_context(
    brief: TaskBrief,
    request_id: str | None = None,
    artifacts_dir: Path | None = None,
) -> Token[tuple[TaskBrief, str, Path] | None]:
    """Bind run identity and artifact root while one worker run is active."""
    return _context.set(
        (brief, request_id or brief.run_id, artifacts_dir or Path("artifacts"))
    )


def reset_trace_context(token: Token[tuple[TaskBrief, str, Path] | None]) -> None:
    _context.reset(token)


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth >= 6:
        return "<max depth>"
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, depth=depth + 1)
            for key, item in value.items()
            if key != "events"
        }
    if isinstance(value, (list, tuple)):
        items = [_bounded(item, depth=depth + 1) for item in value[:50]]
        if len(value) > 50:
            items.append(f"<{len(value) - 50} more items>")
        return items
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + f"... <truncated {len(value) - 4000} chars>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _request_folder(artifacts_dir: Path, request_id: str, fallback: str) -> Path:
    safe_request_id = re.sub(r"[^A-Za-z0-9_.-]", "-", request_id).strip("-._")
    if not safe_request_id:
        safe_request_id = re.sub(r"[^A-Za-z0-9_.-]", "-", fallback).strip("-._")
    return artifacts_dir / "runs" / safe_request_id


def _read_or_create_record(
    destination: Path, *, request_id: str, run_id: str
) -> dict[str, Any]:
    if destination.exists():
        return json.loads(destination.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "request_id": request_id,
        "run_id": run_id,
        "worker": "customer_trends",
        "spans": [],
    }


def _write_record(directory: Path, destination: Path, record: dict[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".customer-trends-tracing.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(record, temporary, indent=2, ensure_ascii=False, default=str)
            temporary.write("\n")
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def save_customer_result(
    result: CustomerTrendsResult,
    *,
    request_id: str,
    artifacts_dir: Path,
) -> Path:
    """Persist an attempt's result in the shared customer-trends trace artifact."""
    data = result.model_dump(mode="json")
    run_id = str(data["run_id"])
    directory = _request_folder(artifacts_dir, request_id, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "customer_trends_tracing.json"
    with _write_lock:
        record = _read_or_create_record(
            destination,
            request_id=directory.name,
            run_id=run_id,
        )
        record.setdefault("results", {})[run_id] = data
        _write_record(directory, destination, record)
    return destination


def save_customer_span(
    name: str,
    *,
    input: Any,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
    status: str = "ok",
    error: str | None = None,
) -> Path | None:
    context = _context.get()
    if context is None:
        return None

    brief, request_id, artifacts_dir = context
    directory = _request_folder(artifacts_dir, request_id, brief.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "customer_trends_tracing.json"
    with _write_lock:
        record = _read_or_create_record(
            destination,
            request_id=directory.name,
            run_id=brief.run_id,
        )

        span: dict[str, Any] = {
            "name": name,
            "task_id": brief.task_id,
            "run_id": brief.run_id,
            "attempt": _attempt_of(brief.run_id),
            "timestamp": datetime.now(UTC).isoformat(),
            "status": status,
            "input": _bounded(input),
            "output": _bounded(output),
            "metadata": _bounded(metadata or {}),
        }
        if error is not None:
            span["error"] = error[:1000]
        record["spans"].append(span)
        _write_record(directory, destination, record)
    return destination


def _attempt_of(run_id: str) -> int | None:
    match = re.search(r"\.a(\d+)$", run_id)
    return int(match.group(1)) if match else None


# The trace the evaluation suite (Evaluation/generic/evaluator.py) reads, built after a run from
# the result and the evidence store. Written only when EVAL_TRACE_ENABLED is set; it never fails
# a run. It is separate from the span file above (customer_trends_tracing.json).

log = get_logger(__name__)


def _cited_metric_ids(metrics: dict[str, Any]) -> list[str]:
    """The ids of the metrics a finding cites; imported late because the sink imports this module."""
    from edrak.agents.customer_trends.tools.submit_findings import cited_metric_ids

    return cited_metric_ids(metrics)


TRACE_FILE = "eval_trace.json"
SCHEMA_VERSION = 1
SPAN_PREFIX = "customer_trends"
COMPUTED_METRIC = "computed_metric"
ITEM_TEXT_CHARS = 1500  # the evaluator cuts each item again; this only bounds the file


def _item(item: EvidenceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "platform": item.platform.value if item.platform else item.source_type.value,
        "published": item.published_at.date().isoformat() if item.published_at else None,
        "url": item.url,
        "language": item.language,
        "snippet_only": item.snippet_only,
        "text": item.text[:ITEM_TEXT_CHARS],
    }


def _ordered_items(store: EvidenceStore, run_id: str, cited: list[str]) -> list[EvidenceItem]:
    """Every stored item, those the findings cite first: the judge reads a bounded amount."""
    first = store.get_items(run_id, cited) if cited else []
    seen = {item.id for item in first}
    total = store.run_summary(run_id).evidence_count  # `query` returns at most `limit` items
    rest = store.query(run_id, EvidenceFilters(), limit=max(total, 1)).items
    return [*first, *(item for item in rest if item.id not in seen)]


def _computed(result: CustomerTrendsResult, store: EvidenceStore) -> list[dict[str, Any]]:
    """The metrics the findings cite and the theme aggregates, as evidence items.

    A finding's numbers (sentiment mix, share of voice, engagement) come from these computed
    records, not from one stored item, so the judge needs them to check the claim.
    """
    items: list[dict[str, Any]] = []
    metric_ids = dict.fromkeys(m for f in result.findings for m in _cited_metric_ids(f.metrics))
    for metric_id in metric_ids:
        record = store.get_metric(result.run_id, metric_id)
        if record is not None:
            text = f"Computed metric {record.metric} {json.dumps(record.params)}: "
            text += json.dumps(record.values, ensure_ascii=False, default=str)
            items.append(
                {"id": metric_id, "platform": COMPUTED_METRIC, "text": text[: ITEM_TEXT_CHARS * 2]}
            )
    if result.theme_aggregates:
        themes = [
            {
                "theme": a.theme_label,
                "count": a.count,
                "share": a.share,
                "sentiment_mix": a.sentiment_mix,
                "by_platform": a.by_platform,
                "by_language": a.by_language,
            }
            for a in result.theme_aggregates
        ]
        text = "Theme aggregates: " + json.dumps(themes, ensure_ascii=False, default=str)
        items.append(
            {"id": "themes", "platform": COMPUTED_METRIC, "text": text[: ITEM_TEXT_CHARS * 2]}
        )
    return items


def add_metric_support(trace: dict[str, Any]) -> dict[str, Any]:
    """In the trace's copy of each finding, list the metrics it cites among its evidence ids.

    A finding reaches its numbers through a computed metric, not through one stored item. The
    evaluator judges a claim against the evidence ids the finding lists, so without this a
    correct figure reads as unsupported. The result itself is not changed. Safe to repeat.
    """

    def patch(findings: Any) -> None:
        for finding in findings or []:
            ids = finding.setdefault("evidence_ids", [])
            for metric_id in _cited_metric_ids(finding.get("metrics") or {}):
                if metric_id not in ids:
                    ids.append(metric_id)

    for result in (trace.get("results") or {}).values():
        patch(result.get("findings"))
    for span in trace.get("spans") or []:
        if span.get("name") == f"{SPAN_PREFIX}.run_worker":
            patch((span.get("output") or {}).get("findings"))
    return trace


def build_trace(result: CustomerTrendsResult, store: EvidenceStore) -> dict[str, Any]:
    """The trace document of one run."""
    cited = list(dict.fromkeys(i for f in result.findings for i in f.evidence_ids))
    items = _ordered_items(store, result.run_id, cited)
    summary = result.control_summary
    return add_metric_support(
        {
            "schema_version": SCHEMA_VERSION,
            "worker": "customer_trends",
            "run_id": result.run_id,
            "task_goal": result.brief.question,
            "results": {result.run_id: result.model_dump(mode="json")},
            "spans": [
                {
                    "name": f"{SPAN_PREFIX}.run_worker",
                    "output": {
                        "status": summary.status,
                        "confidence": summary.overall_confidence,
                        "findings": [f.model_dump(mode="json") for f in result.findings],
                        "gaps": result.gaps,
                    },
                },
                {
                    "name": f"{SPAN_PREFIX}.evidence_query",
                    "output": {
                        "items": [*_computed(result, store), *(_item(item) for item in items)]
                    },
                },
                {
                    "name": f"{SPAN_PREFIX}.submit",
                    "output": {
                        "headline": summary.headline,
                        "accepted_count": summary.findings_count,
                        "rejected_count": sum(
                            "was rejected" in w or "was dropped" in w for w in summary.warnings
                        ),
                    },
                },
            ],
        }
    )


def trace_path(artifacts_dir: Path, run_id: str) -> Path:
    return artifacts_dir / "runs" / run_id / "customer_trends" / TRACE_FILE


def write_trace(
    result: CustomerTrendsResult, store: EvidenceStore, artifacts_dir: Path
) -> Path | None:
    """Write the trace next to the result; the path, or None when it could not be written."""
    try:
        path = trace_path(artifacts_dir, result.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(build_trace(result, store), ensure_ascii=False, indent=2, default=str)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(text + "\n", encoding="utf-8")
        os.replace(temp, path)
        return path
    except Exception as exc:
        log.warning("trace_write_failed", run_id=result.run_id, error=type(exc).__name__)
        return None
