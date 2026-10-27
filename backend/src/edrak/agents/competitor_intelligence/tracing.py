"""Local JSON persistence for competitor-intelligence DeepEval spans."""

from __future__ import annotations

import json
import os
import tempfile
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...contracts.task import ResearchTask

_task_context: ContextVar[ResearchTask | None] = ContextVar(
    "competitor_intelligence_trace_task",
    default=None,
)


def bind_trace_task(task: ResearchTask) -> Token[ResearchTask | None]:
    """Set the task context so graph nodes can write spans beside the plan."""
    return _task_context.set(task)


def reset_trace_task(token: Token[ResearchTask | None]) -> None:
    _task_context.reset(token)


def save_competitor_span(
    name: str,
    *,
    input: dict[str, Any],
    output: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    status: str = "ok",
    error: str | None = None,
) -> Path:
    task = _task_context.get()
    if task is None:
        raise RuntimeError(
            "Cannot persist competitor-intelligence span outside run_research."
        )

    directory = Path("artifacts") / "runs" / str(task.parent_request_id)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "competitor_intelligence_tracing.json"

    if destination.exists():
        record = json.loads(destination.read_text(encoding="utf-8"))
    else:
        record = {
            "schema_version": 1,
            "request_id": str(task.parent_request_id),
            "spans": [],
        }

    span: dict[str, Any] = {
        "name": name,
        "task_id": str(task.task_id),
        "attempt": task.attempt,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": input,
        "output": output,
        "metadata": metadata or {},
    }
    if error is not None:
        span["error"] = error
    record["spans"].append(span)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".competitor-intelligence-tracing.",
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
