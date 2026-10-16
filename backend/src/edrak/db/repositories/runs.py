"""Runs, their live event feed, and the post-verification review checkpoint."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id, now_iso

ACTIVE = ("queued", "running", "awaiting_review", "paused")

# The run screen's event kinds and the schema's run_events.kind values.
VIEW_TO_DB_KIND = {
    "info": "info",
    "gap": "gap_reported",
    "replan": "replan",
    "failure": "failure",
    "decision": "approval",
    "review": "review",
    "conflict": "conflict",
}
DB_TO_VIEW_KIND = {v: k for k, v in VIEW_TO_DB_KIND.items()}
DB_TO_VIEW_KIND.update({"dispatch": "info", "progress": "info", "search": "info", "complete": "info"})

ACTORS = {
    "supervisor", "internal_intelligence", "competitor_intelligence", "market_intelligence", "customer_trends",
    "verification", "synthesis", "system", "user",
}


async def create_run(
    db: aiosqlite.Connection, *, request_id: str, plan_id: str, triggered_by: Optional[str], commit: bool = True
) -> None:
    now = now_iso()
    await db.execute(
        """
        INSERT INTO runs (request_id, plan_id, status, current_stage, triggered_by, started_at)
        VALUES (?, ?, 'running', 'plan', ?, ?)
        """,
        (request_id, plan_id, triggered_by, now),
    )
    if commit:
        await db.commit()


async def get_run(db: aiosqlite.Connection, request_id: str) -> Optional[dict[str, Any]]:
    return await fetch_one(db, "SELECT * FROM runs WHERE request_id = ?", (request_id,))


async def update_run(db: aiosqlite.Connection, request_id: str, commit: bool = True, **fields: Any) -> None:
    allowed = {
        "status", "current_stage", "error", "completed_at", "verification_state", "verification_progress",
        "verification_activity", "notify_instead", "pause_requested", "plan_id",
    }
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"cannot update runs.{key}")
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(request_id)
    await db.execute(f"UPDATE runs SET {', '.join(sets)} WHERE request_id = ?", params)
    if commit:
        await db.commit()


async def add_event(
    db: aiosqlite.Connection,
    request_id: str,
    *,
    channel: str,
    actor: str,
    kind: str,
    message: str,
    task_id: Optional[str] = None,
    callout: Optional[dict[str, Any]] = None,
    occurred_at: Optional[str] = None,
    commit: bool = True,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO run_events (request_id, task_id, occurred_at, channel, actor, kind, message, callout)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id, task_id, occurred_at or now_iso(), channel,
            actor if actor in ACTORS else "system",
            VIEW_TO_DB_KIND.get(kind, kind if kind in DB_TO_VIEW_KIND else "info"),
            message, dumps(callout) if callout else None,
        ),
    )
    if commit:
        await db.commit()
    return int(cursor.lastrowid or 0)


async def list_events(db: aiosqlite.Connection, request_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(db, "SELECT * FROM run_events WHERE request_id = ? ORDER BY id", (request_id,))
    for r in rows:
        r["callout"] = loads(r["callout"])
    return rows


async def fail_orphaned_runs(db: aiosqlite.Connection) -> list[str]:
    """Mark runs that were executing when the server stopped as failed.

    A run executes in memory. After a restart nothing is running it, but the database would still
    say `running` and the screen would wait forever. A run waiting at the review checkpoint is
    different: its graph state is checkpointed, so it stays `awaiting_review` and can be resumed.
    """
    rows = await fetch_all(db, "SELECT request_id FROM runs WHERE status IN ('queued','running')")
    ids = [r["request_id"] for r in rows]
    now = now_iso()
    for request_id in ids:
        await db.execute(
            "UPDATE runs SET status = 'failed', completed_at = ?, error = coalesce(error, ?) WHERE request_id = ?",
            (now, "The server restarted while this run was in progress.", request_id),
        )
        await db.execute(
            "UPDATE business_requests SET status = 'failed', completed_at = ?, failure_reason = coalesce(failure_reason, ?) WHERE request_id = ?",
            (now, "The server restarted while this run was in progress.", request_id),
        )
        await db.execute(
            "UPDATE research_tasks SET state = 'failed', error = coalesce(error, 'Interrupted by a restart') "
            "WHERE parent_request_id = ? AND state IN ('queued','working')",
            (request_id,),
        )
    await db.commit()
    return ids


# ------------------------------------------------------------------ checkpoint 2: review


async def open_review(
    db: aiosqlite.Connection, request_id: str, *, verification_id: Optional[str], payload: dict[str, Any]
) -> dict[str, Any]:
    existing = await pending_review(db, request_id)
    if existing:
        return existing
    round_no = int((await fetch_one(
        db, "SELECT coalesce(max(round), 0) + 1 AS n FROM run_reviews WHERE request_id = ?", (request_id,)
    ) or {"n": 1})["n"])
    review_id = new_id()
    await db.execute(
        "INSERT INTO run_reviews (id, request_id, verification_id, round, payload) VALUES (?, ?, ?, ?, ?)",
        (review_id, request_id, verification_id, round_no, dumps(payload)),
    )
    await db.commit()
    review = await fetch_one(db, "SELECT * FROM run_reviews WHERE id = ?", (review_id,))
    assert review is not None
    return _review(review)


async def pending_review(db: aiosqlite.Connection, request_id: str) -> Optional[dict[str, Any]]:
    row = await fetch_one(
        db, "SELECT * FROM run_reviews WHERE request_id = ? AND status = 'pending'", (request_id,)
    )
    return _review(row) if row else None


async def resolve_review(
    db: aiosqlite.Connection,
    review_id: str,
    *,
    decision: str,
    edited_tasks: list[dict[str, Any]],
    note: Optional[str],
    decided_by: Optional[str],
    auto: bool = False,
) -> bool:
    cursor = await db.execute(
        """
        UPDATE run_reviews SET status = 'resolved', decision = ?, edited_tasks = ?, note = ?, decided_by = ?,
               decided_at = ?, auto_resolved = ?
        WHERE id = ? AND status = 'pending'
        """,
        (decision, dumps(edited_tasks), note, decided_by, now_iso(), 1 if auto else 0, review_id),
    )
    await db.commit()
    return cursor.rowcount == 1


async def list_reviews(db: aiosqlite.Connection, request_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(db, "SELECT * FROM run_reviews WHERE request_id = ? ORDER BY round", (request_id,))
    return [_review(r) for r in rows]


def _review(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = loads(row["payload"], {})
    row["edited_tasks"] = loads(row["edited_tasks"], [])
    row["auto_resolved"] = bool(row["auto_resolved"])
    return row
