"""Company research runs: one row per crawl, with its counters and how it ended."""

from __future__ import annotations

from typing import Any, Optional, Sequence

import aiosqlite

from edrak.company_research.models import FINISHED_STATUSES, ResearchRun, ResearchRunStatus
from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id, now_iso


def _shape(row: dict[str, Any]) -> ResearchRun:
    row["input"] = loads(row.get("input"), {})
    row["warnings"] = loads(row.get("warnings"), [])
    return ResearchRun.model_validate(row)


async def create_research_run(
    db: aiosqlite.Connection,
    *,
    workspace_id: str,
    input: Optional[dict[str, Any]] = None,
    run_id: Optional[str] = None,
    commit: bool = True,
) -> ResearchRun:
    """A queued run. `input` is the seed URL and any options."""
    run_id = run_id or new_id()
    await db.execute(
        "INSERT INTO research_runs (id, workspace_id, input) VALUES (?, ?, ?)",
        (run_id, workspace_id, dumps(input or {})),
    )
    if commit:
        await db.commit()
    run = await get_research_run(db, workspace_id, run_id)
    assert run is not None
    return run


async def get_research_run(db: aiosqlite.Connection, workspace_id: str, run_id: str) -> Optional[ResearchRun]:
    row = await fetch_one(db, "SELECT * FROM research_runs WHERE id = ? AND workspace_id = ?", (run_id, workspace_id))
    return _shape(row) if row else None


async def list_research_runs(
    db: aiosqlite.Connection,
    workspace_id: str,
    *,
    status: Optional[ResearchRunStatus] = None,
    limit: Optional[int] = None,
) -> list[ResearchRun]:
    """Runs of one workspace, newest first."""
    sql = "SELECT * FROM research_runs WHERE workspace_id = ?"
    params: list[Any] = [workspace_id]
    if status is not None:
        sql += " AND status = ?"
        params.append(status.value)
    sql += " ORDER BY created_at DESC, id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [_shape(r) for r in await fetch_all(db, sql, params)]


async def update_research_run(
    db: aiosqlite.Connection, run_id: str, commit: bool = True, **fields: Any
) -> None:
    """Change progress fields. Moving a run to `running` stamps started_at the first time."""
    allowed = {"status", "pages_crawled", "pdfs_read", "facts_found", "warnings", "error"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"cannot update research_runs.{key}")
        if key == "status":
            value = ResearchRunStatus(value)
            if value in FINISHED_STATUSES:
                raise ValueError("finish a run with finish_research_run")
            if value == ResearchRunStatus.RUNNING:
                sets.append("started_at = coalesce(started_at, ?)")
                params.append(now_iso())
            value = value.value
        elif key == "warnings":
            value = dumps(list(value))
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(run_id)
    await db.execute(f"UPDATE research_runs SET {', '.join(sets)} WHERE id = ?", params)
    if commit:
        await db.commit()


async def finish_research_run(
    db: aiosqlite.Connection,
    run_id: str,
    *,
    status: ResearchRunStatus,
    error: Optional[str] = None,
    warnings: Optional[Sequence[str]] = None,
    commit: bool = True,
) -> None:
    """End a run as done, failed or cancelled. `warnings`, when given, replaces the run's warnings."""
    status = ResearchRunStatus(status)
    if status not in FINISHED_STATUSES:
        raise ValueError(f"{status.value} is not a finishing status")
    sets = ["status = ?", "finished_at = ?", "error = ?"]
    params: list[Any] = [status.value, now_iso(), error]
    if warnings is not None:
        sets.append("warnings = ?")
        params.append(dumps(list(warnings)))
    params.append(run_id)
    await db.execute(f"UPDATE research_runs SET {', '.join(sets)} WHERE id = ?", params)
    if commit:
        await db.commit()
