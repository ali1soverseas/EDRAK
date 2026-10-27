from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

try:
    from deepeval.tracing import observe, update_current_span
except ImportError:  # Keep tracing usable when DeepEval is not installed.
    def observe(*args: Any, **kwargs: Any):
        def decorator(fn: Callable):
            return fn
        return decorator

    def update_current_span(**kwargs: Any) -> None:
        return None


_TRACE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "verification_trace_context", default=None
)
_FILE_LOCK = threading.Lock()
_MAX_DEPTH = 12
_MAX_ITEMS = 80
_MAX_TEXT = 12_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return "<max-depth>"
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > _MAX_TEXT:
            return value[:_MAX_TEXT] + "...<truncated>"
        return value
    if hasattr(value, "model_dump"):
        try:
            # Serializing a model changes its representation, not its nesting depth.
            return _json_safe(value.model_dump(mode="json"), depth)
        except Exception:
            pass
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_ITEMS]
        result = {str(k): _json_safe(v, depth + 1) for k, v in items}
        if len(value) > _MAX_ITEMS:
            result["__truncated_items__"] = len(value) - _MAX_ITEMS
        return result
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        result = [_json_safe(v, depth + 1) for v in values[:_MAX_ITEMS]]
        if len(values) > _MAX_ITEMS:
            result.append(f"<... {len(values) - _MAX_ITEMS} more items>")
        return result
    try:
        return str(value)[:_MAX_TEXT]
    except Exception:
        return f"<{type(value).__name__}>"


def bind_trace_context(payload: Any, artifacts_dir: str | Path | None = None):
    """Bind one verification request to the current execution context."""
    request = getattr(payload, "request", None)
    request_id = getattr(request, "request_id", None) or str(uuid.uuid4())
    root = Path(artifacts_dir or os.getenv("EDRAK_ARTIFACTS_DIR", "artifacts/runs"))
    context = {
        "request_id": str(request_id),
        "trace_id": str(request_id),
        "artifacts_dir": str(root),
        "worker": "verification",
    }
    return _TRACE_CONTEXT.set(context)


def reset_trace_context(token: Any) -> None:
    try:
        _TRACE_CONTEXT.reset(token)
    except (ValueError, RuntimeError):
        _TRACE_CONTEXT.set(None)


def _trace_file(context: dict[str, Any]) -> Path:
    root = Path(context["artifacts_dir"])
    # Keep per-request files grouped beneath the run directory.
    return root / context["request_id"] / "verification_tracing.json"


def save_verification_span(
    name: str,
    *,
    input: Any = None,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
    status: str = "ok",
    error: str | None = None,
    span_type: str = "function",
) -> dict[str, Any]:
    """Append a JSON-safe span to the current run's trace file."""
    context = _TRACE_CONTEXT.get() or {
        "request_id": "unbound",
        "trace_id": "unbound",
        "artifacts_dir": os.getenv("EDRAK_ARTIFACTS_DIR", "artifacts/runs"),
        "worker": "verification",
    }
    span = {
        "span_id": str(uuid.uuid4()),
        "trace_id": context["trace_id"],
        "request_id": context["request_id"],
        "worker": context["worker"],
        "name": name,
        "span_type": span_type,
        "started_at": _now(),
        "status": status,
        "input": _json_safe(input),
        "output": _json_safe(output),
        "metadata": _json_safe(metadata or {}),
        "error": error,
    }
    path = _trace_file(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FILE_LOCK:
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as handle:
                    document = json.load(handle)
                if not isinstance(document, dict):
                    document = {"trace_id": context["trace_id"], "spans": []}
            else:
                document = {"trace_id": context["trace_id"], "request_id": context["request_id"], "spans": []}
            document.setdefault("spans", []).append(span)
            document["updated_at"] = _now()
            fd, temp_name = tempfile.mkstemp(prefix="verification-trace-", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(document, handle, ensure_ascii=False, indent=2)
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        except Exception as exc:
            # Trace persistence must not break the verification pipeline.
            print(f"[verification][tracing] Could not persist span '{name}': {exc}")
    return span


def trace_node(name: str, *, span_type: str = "node"):
    """Trace a state-dict node's input/output and bind its request context."""
    def decorator(fn: Callable):
        @observe(type=span_type)
        @wraps(fn)
        def wrapped(state: dict[str, Any], *args: Any, **kwargs: Any):
            payload = state.get("payload") if isinstance(state, dict) else None
            token = bind_trace_context(payload) if payload is not None else None
            trace_input = {
                "state_keys": list(state.keys()) if isinstance(state, dict) else [],
                "payload": payload,
                "assessments": state.get("assessments") if isinstance(state, dict) else None,
            }
            try:
                update_current_span(input=_json_safe(trace_input), metadata={"node": name, "worker": "verification"})
                output = fn(state, *args, **kwargs)
                update_current_span(output=_json_safe(output))
                save_verification_span(name, input=trace_input, output=output, metadata={"node": name}, span_type=span_type)
                return output
            except Exception as exc:
                save_verification_span(name, input=trace_input, metadata={"node": name}, status="error", error=f"{type(exc).__name__}: {exc}", span_type=span_type)
                raise
            finally:
                if token is not None:
                    reset_trace_context(token)
        return wrapped
    return decorator
