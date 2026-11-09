"""The trace of a finished run, in the shape `Evaluation/generic/evaluator.py` reads.

The evaluator takes `results` (the worker's own result) and a few named spans. It finds the
evidence the findings could cite in the `customer_trends.evidence_query` span, the headline in
`customer_trends.submit` and the status in `customer_trends.run_worker`. This module builds that
trace after the run from the result and the evidence store, so the trace holds the whole
evidence set and not only what one tool call returned.

Writing the trace is opt-in (`eval_trace_enabled`) and never fails a run.
"""

import json
import os
from pathlib import Path
from typing import Any

from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore

log = get_logger(__name__)

TRACE_FILE = "eval_trace.json"
SCHEMA_VERSION = 1
SPAN_PREFIX = "customer_trends"
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


def build_trace(result: CustomerTrendsResult, store: EvidenceStore) -> dict[str, Any]:
    """The trace document of one run."""
    cited = list(dict.fromkeys(i for f in result.findings for i in f.evidence_ids))
    items = _ordered_items(store, result.run_id, cited)
    summary = result.control_summary
    return {
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
                "output": {"items": [_item(item) for item in items]},
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
