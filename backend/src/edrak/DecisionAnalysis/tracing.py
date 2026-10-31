"""Non-fatal JSON tracing for EDRAK Decision Analysis.

Events are written to decision_analysis_tracing.json beside the run's
orchestrator_planning.json. The destination can be overridden with
EDRAK_DECISION_ANALYSIS_TRACE_JSON.
Tracing errors are intentionally swallowed so observability cannot fail a run.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()


def _jsonable(value: Any) -> Any:
    """Convert nested Pydantic models/enums/containers to JSON-safe values."""
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json"))
        except Exception:
            try:
                return _jsonable(value.model_dump())
            except Exception:
                return str(value)
    if hasattr(value, "dict") and callable(value.dict):
        try:
            return _jsonable(value.dict())
        except Exception:
            pass
    if hasattr(value, "value"):
        return _jsonable(value.value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _find_trace_file(research_run_id: Any = None) -> Path | None:
    configured = os.getenv("EDRAK_DECISION_ANALYSIS_TRACE_JSON")
    if configured:
        return Path(configured).expanduser()

    configured_planning = os.getenv("EDRAK_ORCHESTRATOR_PLANNING_JSON")
    if configured_planning:
        return Path(configured_planning).expanduser().with_name(
            "decision_analysis_tracing.json"
        )

    root = Path.cwd() / "artifacts" / "runs"
    run_id = str(research_run_id).strip() if research_run_id else ""
    if run_id:
        safe_run_id = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id).strip("-._")
        if safe_run_id:
            return root / safe_run_id / "decision_analysis_tracing.json"
    if root.exists():
        matches = list(root.glob("**/orchestrator_planning.json"))
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime).with_name(
                "decision_analysis_tracing.json"
            )
    return None


def trace_decision_analysis(
    stage: str,
    *,
    research_run_id: Any = None,
    **payload: Any,
) -> None:
    """Append a Decision Analysis event to its own JSON artifact.

    Each event is stored in both ``trace_events`` and
    ``decision_analysis_trace.history`` for convenient inspection. Any tracing
    exception is logged and ignored.
    """
    try:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": "decision_analysis",
            "stage": stage,
            "research_run_id": _jsonable(research_run_id),
            "payload": _jsonable(payload),
        }
        path = _find_trace_file(research_run_id)
        if path is None:
            logger.debug("Decision Analysis trace skipped: no run folder found")
            return

        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as stream:
                        document = json.load(stream)
                    if not isinstance(document, dict):
                        document = {"decision_analysis_trace_data": document}
                except (OSError, json.JSONDecodeError):
                    # Do not overwrite a corrupt/unreadable existing trace file.
                    logger.warning(
                        "Cannot read trace file %s; event was not written", path
                    )
                    return
            else:
                document = {}

            events = document.setdefault("trace_events", [])
            if not isinstance(events, list):
                events = document["trace_events"] = []
            events.append(event)

            component_trace = document.setdefault("decision_analysis_trace", {})
            if not isinstance(component_trace, dict):
                component_trace = document["decision_analysis_trace"] = {}
            history = component_trace.setdefault("history", [])
            if not isinstance(history, list):
                history = component_trace["history"] = []
            history.append(event)
            component_trace["last_stage"] = stage
            component_trace["last_updated"] = event["timestamp"]

            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(
                        document, stream, ensure_ascii=False, indent=2, default=str
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass
    except Exception:
        logger.exception("Decision Analysis tracing failed at stage %s", stage)
