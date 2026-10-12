"""Typed metrics, their conflicts and the per-worker requirement outcomes."""

from __future__ import annotations

from typing import Any, Iterable, Optional

import aiosqlite

from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id
from edrak.metrics.contracts import MetricObservation, RequirementOutcome


async def evidence_uid(db: aiosqlite.Connection, task_id: str, attempt: int, evidence_id: str) -> Optional[str]:
    row = await fetch_one(
        db, "SELECT uid FROM evidence WHERE task_id = ? AND attempt = ? AND evidence_id = ?",
        (task_id, attempt, evidence_id),
    )
    return row["uid"] if row else None


async def insert_metric(
    db: aiosqlite.Connection, request_id: str, obs: MetricObservation, *, worker: Optional[str] = None,
    task_id: Optional[str] = None, attempt: int = 1, status: str = "unverified", method: str = "worker",
    inputs: Optional[list[str]] = None, entity_id: Optional[str] = None, commit: bool = True,
) -> str:
    uid = new_id()
    ev_uid = await evidence_uid(db, task_id, attempt, obs.evidence_id) if task_id else None
    await db.execute(
        "INSERT INTO metrics (uid, request_id, metric_id, entity, entity_id, value, value_text, unit, period, definition,"
        " evidence_uid, source_span, scope, sample_size, status, derived, formula, inputs, worker, method)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid, request_id, obs.metric_id, obs.entity, entity_id, obs.value, obs.value_text, obs.unit, obs.period,
         obs.definition, ev_uid, obs.source_span, obs.scope.value, obs.sample_size, status, int(obs.derived),
         obs.formula, dumps(inputs or []), worker, method),
    )
    if commit:
        await db.commit()
    return uid


async def list_metrics(db: aiosqlite.Connection, request_id: str, *, include_unverified: bool = False) -> list[dict[str, Any]]:
    sql = ("SELECT m.*, e.display_no AS display_no FROM metrics m LEFT JOIN evidence e ON e.uid = m.evidence_uid"
           " WHERE m.request_id = ?")
    if not include_unverified:
        sql += " AND m.status <> 'unverified'"
    rows = await fetch_all(db, sql + " ORDER BY m.metric_id, m.entity, m.created_at", (request_id,))
    for r in rows:
        r["inputs"] = loads(r.get("inputs"), [])
    return rows


async def get_metric(db: aiosqlite.Connection, uid: str) -> Optional[dict[str, Any]]:
    row = await fetch_one(
        db, "SELECT m.*, e.display_no AS display_no FROM metrics m LEFT JOIN evidence e ON e.uid = m.evidence_uid WHERE m.uid = ?",
        (uid,),
    )
    if row:
        row["inputs"] = loads(row.get("inputs"), [])
    return row


async def set_status(db: aiosqlite.Connection, uid: str, status: str, *, commit: bool = True) -> None:
    await db.execute("UPDATE metrics SET status = ? WHERE uid = ?", (status, uid))
    if commit:
        await db.commit()


async def clear_for_request(db: aiosqlite.Connection, request_id: str, methods: Iterable[str] = ("derived",)) -> None:
    marks = ",".join("?" for _ in methods)
    await db.execute(f"DELETE FROM metrics WHERE request_id = ? AND method IN ({marks})", (request_id, *methods))
    await db.execute("DELETE FROM metric_conflicts WHERE request_id = ?", (request_id,))
    await db.commit()


async def write_conflict(db: aiosqlite.Connection, request_id: str, metric_id: Optional[str], entity: str,
                         period: Optional[str], low: float, high: float, uids: list[str]) -> None:
    await db.execute(
        "INSERT INTO metric_conflicts (request_id, metric_id, entity, period, low, high, metric_uids) VALUES (?,?,?,?,?,?,?)",
        (request_id, metric_id, entity, period, low, high, dumps(uids)),
    )
    await db.commit()


async def list_conflicts(db: aiosqlite.Connection, request_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(db, "SELECT * FROM metric_conflicts WHERE request_id = ?", (request_id,))
    for r in rows:
        r["metric_uids"] = loads(r.get("metric_uids"), [])
    return rows


async def write_outcomes(db: aiosqlite.Connection, request_id: str, task_id: str,
                         outcomes: Iterable[tuple[RequirementOutcome, bool]], *, commit: bool = True) -> None:
    for o, required in outcomes:
        await db.execute(
            "INSERT INTO requirement_outcomes (request_id, task_id, metric_id, entity, status, reason, attempts, required)"
            " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (request_id, task_id, metric_id, entity) DO UPDATE SET"
            " status = excluded.status, reason = excluded.reason, attempts = excluded.attempts,"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (request_id, task_id, o.metric_id, o.entity, o.status.value, o.reason.value if o.reason else None,
             o.attempts, int(required)),
        )
    if commit:
        await db.commit()


async def list_outcomes(db: aiosqlite.Connection, request_id: str) -> list[dict[str, Any]]:
    return await fetch_all(db, "SELECT * FROM requirement_outcomes WHERE request_id = ?", (request_id,))


async def write_metric_points(db: aiosqlite.Connection, request_id: str) -> None:
    """Verified, dated numbers feed the entity timeline charts."""
    await db.execute(
        "INSERT OR IGNORE INTO entity_metric_points (entity_id, metric, observed_on, value, unit, request_id, evidence_uid)"
        " SELECT entity_id, metric_id, substr(COALESCE(period, created_at), 1, 10), value, unit, request_id, evidence_uid"
        " FROM metrics WHERE request_id = ? AND status = 'verified' AND entity_id IS NOT NULL AND metric_id IS NOT NULL"
        " AND value IS NOT NULL",
        (request_id,),
    )
    await db.commit()
