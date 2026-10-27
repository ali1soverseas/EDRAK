"""JSON tracing helpers for Edrak orchestrator verification/replanning.

Place this file beside orchestration/graph.py and orchestration/nodes.py.
Merges timestamped trace events into artifacts/runs/<request_id>/orchestrator_planning.json, preserving planner fields.
Tracing failures are intentionally swallowed so observability never breaks the pipeline.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _jsonable(value: Any, *, depth: int = 0) -> Any:
    """Convert Pydantic models, enums and nested values into JSON-safe values."""
    if depth > 12:
        return f"<max-depth:{type(value).__name__}>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json"), depth=depth + 1)
        except Exception:
            pass
    if hasattr(value, "dict") and callable(value.dict):
        try:
            return _jsonable(value.dict(), depth=depth + 1)
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth=depth + 1) for v in value]
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _request_id(context: Any) -> str:
    request = context.get("request") if isinstance(context, dict) else None
    raw = getattr(request, "request_id", None)
    if raw is None and isinstance(request, dict):
        raw = request.get("request_id")
    # Avoid path traversal and filesystem-unfriendly IDs.
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw or "unknown_run"))[:120]
    return safe or "unknown_run"


def _artifact_path(context: Any) -> Path:
    # tracing.py is expected at backend/src/edrak/orchestration/tracing.py.
    repo_root = Path(__file__).resolve().parents[4]
    run_dir = repo_root / "artifacts" / "runs" / _request_id(context)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / "orchestrator_planning.json"


def _trace_bucket(stage: str) -> tuple[str, str]:
    """Map events to stable, easy-to-consume fields in orchestrator_planning.json."""
    if stage.startswith("verification_"):
        suffix = stage.removeprefix("verification_")
        return "verification_trace", suffix
    if stage.startswith("replanning_"):
        suffix = stage.removeprefix("replanning_")
        return "replanning_trace", suffix
    if stage.startswith("post_verification_routing"):
        return "routing_trace", "error" if stage.endswith("_error") else "result"
    return "trace_summary", stage


def trace_event(stage: str, context: Any = None, **fields: Any) -> None:
    """Merge a timestamped event into orchestrator_planning.json without erasing its plan."""
    try:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "request_id": _request_id(context),
            **{key: _jsonable(value) for key, value in fields.items()},
        }
        path = _artifact_path(context or {})
        with _LOCK:
            try:
                existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except (OSError, json.JSONDecodeError):
                existing = {}

            # Keep any existing planner fields intact. If an older artifact is not an
            # object, preserve it rather than silently discarding its content.
            if not isinstance(existing, dict):
                existing = {"previous_planning_content": existing}
            existing.setdefault("request_id", event["request_id"])
            events = existing.setdefault("trace_events", [])
            if not isinstance(events, list):
                events = [events]
                existing["trace_events"] = events
            events.append(event)

            bucket, key = _trace_bucket(stage)
            stage_data = {k: v for k, v in event.items() if k not in ("stage", "request_id")}
            target = existing.setdefault(bucket, {})
            if not isinstance(target, dict):
                target = {"previous_content": target}
                existing[bucket] = target
            # Keep history for repeatable stages, while also exposing the latest result.
            target.setdefault("history", []).append({"stage": stage, **stage_data})
            target[key] = stage_data

            fd, tmp_name = tempfile.mkstemp(prefix="orchestrator_planning_", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(existing, handle, ensure_ascii=False, indent=2, default=str)
                    handle.write("\n")
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
    except Exception:
        # Observability must never change orchestration behavior.
        return


def summarize_verification(raw: Any) -> dict[str, Any]:
    """Summarize verification while preserving the full JSON-serializable result."""
    data = _jsonable(raw)
    if not isinstance(data, dict):
        return {"raw": data}
    findings = data.get("findings") or []
    counts: dict[str, int] = {}
    contradiction_count = 0
    for item in findings if isinstance(findings, list) else []:
        if not isinstance(item, dict):
            continue
        status = item.get("verification_status") or item.get("status") or "unknown"
        counts[str(status)] = counts.get(str(status), 0) + 1
        contradictions = item.get("contradictions") or []
        if isinstance(contradictions, list):
            contradiction_count += len(contradictions)
        elif contradictions:
            contradiction_count += 1
    decision = data.get("decision")
    if isinstance(decision, dict):
        status = decision.get("status")
        summary = decision.get("summary")
    else:
        status = getattr(decision, "status", None)
        summary = getattr(decision, "summary", None)
    return {
        "decision_status": _jsonable(status),
        "decision_summary": _jsonable(summary),
        "finding_count": len(findings) if isinstance(findings, list) else None,
        "finding_status_counts": counts,
        "contradiction_count": contradiction_count,
        "payload": data,
    }
