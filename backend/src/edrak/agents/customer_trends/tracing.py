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