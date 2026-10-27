"""DeepEval span metadata and local JSON persistence for the internal-intelligence worker."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar, cast
from uuid import uuid4

from deepeval.tracing import observe

from edrak.contracts.task import ResearchTask
from edrak.core.config import settings

_context: ContextVar[tuple[ResearchTask, str, Path] | None] = ContextVar(
    "internal_intelligence_trace_context",
    default=None,
)
_write_lock = threading.Lock()

F = TypeVar("F", bound=Callable[..., Any])


def bind_trace_context(
    task: ResearchTask,
    request_id: str | None = None,
    artifacts_dir: Path | None = None,
) -> Token[tuple[ResearchTask, str, Path] | None]:
    """Bind task identity and artifact root while one internal-worker run is active.

    By default, traces are written under ``artifacts/runs/<request_id>/``.
    The request folder comes from the originating request, while each span keeps
    a task-attempt run ID for distinguishing retries.
    """
    task_id = str(getattr(task, "task_id", "internal-task"))
    attempt = getattr(task, "attempt", None)
    parent_request_id = getattr(task, "parent_request_id", None)
    request_folder_id = request_id or parent_request_id
    if not request_folder_id:
        request_folder_id = f"{task_id}.a{attempt}" if attempt is not None else task_id
    root = artifacts_dir or settings.ARTIFACTS_PATH
    if not root.is_absolute():
        root = settings.BASE_DIR / root
    return _context.set((task, str(request_folder_id), root))


def reset_trace_context(
    token: Token[tuple[ResearchTask, str, Path] | None],
) -> None:
    """Restore the trace context that was active before ``bind_trace_context``."""
    _context.reset(token)


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Convert values to JSON-safe data while limiting trace size and recursion."""
    if depth >= 6:
        return "<max depth>"
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > 4000:
            return value[:4000] + f"... <truncated {len(value) - 4000} chars>"
        return value
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, depth=depth + 1)
            for key, item in value.items()
            if key != "events"
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        bounded_items = [_bounded(item, depth=depth + 1) for item in items[:50]]
        if len(items) > 50:
            bounded_items.append(f"<{len(items) - 50} more items>")
        return bounded_items
    if hasattr(value, "model_dump"):
        try:
            return _bounded(value.model_dump(mode="json"), depth=depth + 1)
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _bounded(value.dict(), depth=depth + 1)
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _bounded(vars(value), depth=depth + 1)
        except Exception:
            pass
    return str(value)


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", value).strip("-. _")
    return safe or "internal-task"


def _attempt_of(run_id: str, task: ResearchTask) -> int | None:
    match = re.search(r"\.a(\d+)$", run_id)
    if match:
        return int(match.group(1))
    attempt = getattr(task, "attempt", None)
    return int(attempt) if isinstance(attempt, int) else None


def save_internal_span(
    name: str,
    *,
    input: Any,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
    status: str = "ok",
    error: str | None = None,
) -> Path | None:
    """Append one bounded span record to the current run's JSON trace file.

    Returns the destination path, or ``None`` when no trace context is bound.
    """
    context = _context.get()
    if context is None:
        return None

    task, request_id, artifacts_dir = context
    task_id = str(getattr(task, "task_id", "internal-task"))
    attempt = getattr(task, "attempt", None)
    run_id = f"{task_id}.a{attempt}" if attempt is not None else task_id
    safe_request_id = _safe_id(request_id)
    directory = artifacts_dir / "runs" / safe_request_id
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "internal_intelligence_tracing.json"

    with _write_lock:
        if destination.exists():
            try:
                record = json.loads(destination.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or not isinstance(record.get("spans"), list):
                    record = {}
            except (OSError, json.JSONDecodeError):
                record = {}
        else:
            record = {}

        if not record:
            record = {
                "schema_version": 1,
                "request_id": safe_request_id,
                "run_id": run_id,
                "worker": "internal_intelligence",
                "spans": [],
            }

        span: dict[str, Any] = {
            "span_id": str(uuid4()),
            "name": name,
            "task_id": task_id,
            "run_id": run_id,
            "attempt": _attempt_of(request_id, task),
            "timestamp": datetime.now(UTC).isoformat(),
            "status": status,
            "input": _bounded(input),
            "output": _bounded(output),
            "metadata": _bounded(metadata or {}),
        }
        if error is not None:
            span["error"] = error[:1000]
        record["spans"].append(span)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=".internal-intelligence-tracing.",
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

    return destination


def trace_internal_node(name: str) -> Callable[[F], F]:
    """Trace a worker node with DeepEval and append its input/output to local JSON.

    The decorator binds task context for the duration of this node call, writes
    a local span on success or failure, and then restores the prior context.
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            state = args[0] if args else kwargs.get("state")
            task = state.get("task") if isinstance(state, dict) else None
            token = bind_trace_context(task) if task is not None else None
            started = datetime.now(UTC)
            node_input = {
                "state_keys": list(state.keys()) if isinstance(state, dict) else [],
                "task_id": getattr(task, "task_id", None),
                "task_goal": getattr(task, "goal", None),
            }

            try:
                result = func(*args, **kwargs)
                save_internal_span(
                    name,
                    input=node_input,
                    output=result,
                    metadata={
                        "duration_seconds": (datetime.now(UTC) - started).total_seconds(),
                        "function": func.__name__,
                        "deepeval_observe_enabled": True,
                    },
                    status="ok",
                )
                return result
            except Exception as exc:
                save_internal_span(
                    name,
                    input=node_input,
                    metadata={
                        "duration_seconds": (datetime.now(UTC) - started).total_seconds(),
                        "function": func.__name__,
                        "deepeval_observe_enabled": True,
                    },
                    status="error",
                    error=str(exc),
                )
                raise
            finally:
                if token is not None:
                    reset_trace_context(token)

        # DeepEval's @observe wraps the original node; our wrapper manages local persistence.
        observed = observe(name=name, type="agent")(cast(F, wrapper))
        return cast(F, observed)

    return decorator
