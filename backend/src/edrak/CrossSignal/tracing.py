"""Dedicated Cross-Signal tracing into cross_signal_tracing.json.

Path resolution order:
1. EDRAK_CROSS_SIGNAL_TRACE_JSON environment variable.
2. The sibling of EDRAK_ORCHESTRATOR_PLANNING_JSON.
3. artifacts/runs/<research_run_id>/cross_signal_tracing.json.
4. A sibling of the most recently modified planning artifact.

The Cross-Signal trace is kept separate from planning and other run artifacts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()


def _jsonable(value: Any) -> Any:
    """Convert Pydantic models, enums, paths, and nested values to JSON-safe data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json"))
        except Exception:
            try:
                return _jsonable(value.model_dump())
            except Exception:
                pass
    if hasattr(value, "dict") and callable(value.dict):
        try:
            return _jsonable(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _jsonable(vars(value))
        except Exception:
            pass
    return str(value)


def _run_id_from(state: Any) -> str:
    if not isinstance(state, dict):
        return ""
    cross_input = state.get("input")
    candidates = [
        state.get("research_run_id"),
        state.get("request_id"),
        getattr(cross_input, "research_run_id", None),
    ]
    business_request = getattr(cross_input, "business_request", None)
    candidates.extend([
        getattr(business_request, "request_id", None),
        getattr(business_request, "run_id", None),
    ])
    for value in candidates:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _find_runs_root() -> Path:
    cwd = Path.cwd().resolve()
    for base in (cwd, *cwd.parents):
        candidate = base / "artifacts" / "runs"
        if candidate.exists() or base.name.lower() in {"edrak", "backend", "src"}:
            return candidate
    return cwd / "artifacts" / "runs"


def _resolve_path(state: Any = None) -> Path:
    explicit = os.getenv("EDRAK_CROSS_SIGNAL_TRACE_JSON", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    explicit_planning = os.getenv("EDRAK_ORCHESTRATOR_PLANNING_JSON", "").strip()
    if explicit_planning:
        return Path(explicit_planning).expanduser().resolve().with_name(
            "cross_signal_tracing.json"
        )

    runs_root = _find_runs_root()
    run_id = _run_id_from(state)
    if run_id:
        safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id)[:120]
        if safe_run_id:
            return runs_root / safe_run_id / "cross_signal_tracing.json"

    existing = (
        list(runs_root.glob("**/orchestrator_planning.json"))
        if runs_root.exists()
        else []
    )
    if existing:
        return max(existing, key=lambda p: p.stat().st_mtime).with_name(
            "cross_signal_tracing.json"
        )

    raise FileNotFoundError(
        "Cannot locate Cross-Signal trace path. Set "
        "EDRAK_CROSS_SIGNAL_TRACE_JSON or provide research_run_id in state."
    )


def trace_cross_signal(stage: str, state: Any = None, **payload: Any) -> None:
    """Append one Cross-Signal trace event to its own JSON artifact.

    Tracing failures are logged and swallowed so observability cannot fail the agent.
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "component": "cross_signal",
        "stage": str(stage),
        "payload": _jsonable(payload),
    }
    try:
        path = _resolve_path(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        data = {"existing_cross_signal_data": data}
                except json.JSONDecodeError:
                    logger.exception(
                        "Cannot append Cross-Signal trace: invalid JSON in %s", path
                    )
                    return
            else:
                data = {}

            # Keep the event structure consistent with other run trace artifacts.
            timeline = data.setdefault("trace_events", [])
            if not isinstance(timeline, list):
                timeline = data["trace_events"] = []
            timeline.append(event)

            section = data.setdefault("cross_signal_trace", {})
            if not isinstance(section, dict):
                section = data["cross_signal_trace"] = {"previous_value": section}
            history = section.setdefault("history", [])
            if not isinstance(history, list):
                history = section["history"] = []
            history.append(event)
            section["last_stage"] = event["stage"]
            section["last_updated_at"] = event["timestamp"]
            section["event_count"] = len(history)

            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(path)
    except Exception:
        logger.exception("Cross-Signal tracing failed at stage=%s", stage)