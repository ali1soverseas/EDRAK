"""Company research runs: one row per crawl, with its counters and how it ended."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional, Sequence

import aiosqlite

from edrak.company_research.models import FINISHED_STATUSES, ResearchRun, ResearchRunStatus
from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id, now_iso


def _shape(row: dict[str, Any]) -> ResearchRun:
    row["input"] = loads(row.get("input"), {})
    row["warnings"] = loads(row.get("warnings"), [])
    row["stats"] = loads(row.get("stats"), {})
    return ResearchRun.model_validate(row)


class ResearchRunActive(Exception):
    """The workspace already has a queued or running research run."""


async def create_research_run(
    db: aiosqlite.Connection,
    *,
    workspace_id: str,
    input: Optional[dict[str, Any]] = None,
    run_id: Optional[str] = None,
    commit: bool = True,
) -> ResearchRun:
    """A queued run. `input` is the seed URL and any options.

    Raises ResearchRunActive when the workspace already has a queued or running run: the database
    allows one at a time.
    """
    run_id = run_id or new_id()
    try:
        await db.execute(
            "INSERT INTO research_runs (id, workspace_id, input) VALUES (?, ?, ?)",
            (run_id, workspace_id, dumps(input or {})),
        )
    except sqlite3.IntegrityError as exc:
        if "research_runs.workspace_id" not in str(exc):
            raise
        raise ResearchRunActive(workspace_id) from exc
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
    """Change progress fields of a queued or running run. Moving a run to `running` stamps started_at the first time."""
    allowed = {"status", "pages_crawled", "pdfs_read", "facts_found", "warnings", "error", "stage", "stats"}
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
        elif key == "stats":
            value = dumps(dict(value))
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(run_id)
    # A finished run is left as it ended: a cancel and the run's own last writes can race.
    await db.execute(f"UPDATE research_runs SET {', '.join(sets)} WHERE id = ? AND status IN ('queued','running')", params)
    if commit:
        await db.commit()


async def finish_research_run(
    db: aiosqlite.Connection,
    run_id: str,
    *,
    status: ResearchRunStatus,
    error: Optional[str] = None,
    warnings: Optional[Sequence[str]] = None,
    stats: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> None:
    """End a run as done, failed or cancelled. `warnings` and `stats`, when given, replace the run's."""
    status = ResearchRunStatus(status)
    if status not in FINISHED_STATUSES:
        raise ValueError(f"{status.value} is not a finishing status")
    sets = ["status = ?", "finished_at = ?", "error = ?", "stage = NULL"]
    params: list[Any] = [status.value, now_iso(), error]
    if warnings is not None:
        sets.append("warnings = ?")
        params.append(dumps(list(warnings)))
    if stats is not None:
        sets.append("stats = ?")
        params.append(dumps(dict(stats)))
    params.append(run_id)
    await db.execute(
        f"UPDATE research_runs SET {', '.join(sets)} WHERE id = ? AND status IN ('queued','running')", params
    )
    if commit:
        await db.commit()


async def active_research_run(db: aiosqlite.Connection, workspace_id: str) -> Optional[ResearchRun]:
    """The workspace's queued or running run, if any."""
    row = await fetch_one(
        db, "SELECT * FROM research_runs WHERE workspace_id = ? AND status IN ('queued','running')", (workspace_id,)
    )
    return _shape(row) if row else None


async def fail_orphaned_research_runs(db: aiosqlite.Connection) -> list[str]:
    """Fail runs that were executing when the server stopped: nothing runs them after a restart."""
    rows = await fetch_all(db, "SELECT id FROM research_runs WHERE status IN ('queued','running')")
    ids = [r["id"] for r in rows]
    for run_id in ids:
        await finish_research_run(
            db, run_id, status=ResearchRunStatus.FAILED, error="The server restarted while this run was in progress.",
            commit=False,
        )
    await db.commit()
    return ids
