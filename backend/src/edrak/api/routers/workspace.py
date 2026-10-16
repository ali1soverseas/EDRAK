"""Workspace-wide screens: Home, Reports, Decision log, Notifications, Search, Settings."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import SessionOut
from edrak.db.repositories import auth as auth_repo
from edrak.db.repositories import briefs as briefs_repo
from edrak.db.repositories import notifications as notifications_repo
from edrak.db.util import fetch_all, fetch_one, loads

router = APIRouter(tags=["workspace"])


@router.get("/home")
async def home(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    """What needs the person: plans to approve, runs to review, briefs to open, gaps; plus alerts and monitors."""
    ws = session.workspace.workspace_id
    needs = await fetch_all(
        db,
        """SELECT r.request_id, r.title, r.use_case, r.status, r.updated_at, b.id AS brief_id, b.number AS brief_no,
                  b.evidence_status, b.lifecycle,
                  (SELECT count(*) FROM information_gaps g WHERE g.request_id = r.request_id AND g.status = 'open') AS open_gaps,
                  (SELECT count(*) FROM research_tasks t JOIN research_plans p ON p.plan_id = t.plan_id
                     WHERE p.request_id = r.request_id AND p.status IN ('awaiting_approval','approved') AND t.enabled = 1) AS tasks
           FROM business_requests r LEFT JOIN briefs b ON b.request_id = r.request_id
           WHERE r.workspace_id = ? AND (r.status IN ('awaiting_approval','awaiting_review','running','paused','planning')
                 OR (b.id IS NOT NULL AND b.lifecycle = 'ready'))
           ORDER BY CASE r.status WHEN 'awaiting_review' THEN 0 WHEN 'awaiting_approval' THEN 1 ELSE 2 END, r.updated_at DESC
           LIMIT 20""",
        (ws,),
    )
    alerts = await fetch_all(
        db, "SELECT * FROM alerts WHERE workspace_id = ? AND status IN ('unread','read') ORDER BY created_at DESC LIMIT 5", (ws,)
    )
    monitors = await fetch_all(
        db, "SELECT id, name, status, frequency, day_of_week, run_time, next_run_at, last_run_at FROM monitors "
            "WHERE workspace_id = ? ORDER BY status, next_run_at LIMIT 5", (ws,)
    )
    counts = await fetch_one(
        db,
        """SELECT (SELECT count(*) FROM business_requests WHERE workspace_id = ?) AS analyses,
                  (SELECT count(*) FROM business_requests WHERE workspace_id = ? AND status = 'awaiting_approval') AS plans_waiting,
                  (SELECT count(*) FROM business_requests WHERE workspace_id = ? AND status = 'awaiting_review') AS reviews_waiting,
                  (SELECT count(*) FROM briefs WHERE workspace_id = ? AND lifecycle = 'ready') AS briefs_to_review,
                  (SELECT count(*) FROM alerts WHERE workspace_id = ? AND status = 'unread') AS unread_alerts,
                  (SELECT count(*) FROM documents WHERE workspace_id = ?) AS documents,
                  (SELECT count(*) FROM entities WHERE workspace_id = ? AND kind = 'competitor' AND is_tracked = 1) AS competitors""",
        (ws,) * 7,
    )
    return {"needs_you": needs, "alerts": alerts, "monitors": monitors, "counts": counts,
            "first_time": (counts or {}).get("analyses", 0) == 0}


@router.get("/reports")
async def reports(
    use_case: Optional[str] = None,
    status: Optional[str] = None,
    days: Optional[int] = None,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    rows = await briefs_repo.list_briefs(db, session.workspace.workspace_id)
    if use_case:
        rows = [r for r in rows if r["use_case"] == use_case]
    if status:
        rows = [r for r in rows if r["evidence_status"] == status or r["lifecycle"] == status]
    if days:
        from edrak.db.util import now_iso
        from datetime import timedelta

        cutoff = now_iso(timedelta(days=-days))
        rows = [r for r in rows if r["generated_at"] >= cutoff]
    return rows


@router.get("/reports/compare")
async def compare_runs(
    a: str,
    b: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    """What changed between two briefs' runs: findings added, changed or removed."""
    from edrak.monitoring.diff import diff_runs

    ws = session.workspace.workspace_id
    ba, bb = await briefs_repo.get_brief(db, a, ws), await briefs_repo.get_brief(db, b, ws)
    if not ba or not bb:
        raise HTTPException(status_code=404, detail="not_found")
    changes = await diff_runs(db, previous_request_id=ba["request_id"], request_id=bb["request_id"])
    return {"from": {"id": a, "number": ba["number"]}, "to": {"id": b, "number": bb["number"]}, "changes": changes}


@router.get("/decisions")
async def decisions(
    status: Optional[str] = None,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    sql = """SELECT d.*, b.number AS brief_no, b.title AS brief_title, b.use_case, u.full_name AS owner_name,
                    s.source_count, s.worker_count, s.created_at AS snapshot_at
             FROM decisions d JOIN briefs b ON b.id = d.brief_id
             LEFT JOIN users u ON u.id = d.owner_id LEFT JOIN evidence_snapshots s ON s.decision_id = d.id
             WHERE d.workspace_id = ?"""
    params: list[Any] = [session.workspace.workspace_id]
    if status:
        sql += " AND d.status = ?"
        params.append(status)
    rows = await fetch_all(db, sql + " ORDER BY d.recorded_at DESC", params)
    for r in rows:
        r["next_steps"] = await fetch_all(db, "SELECT * FROM decision_next_steps WHERE decision_id = ? ORDER BY position", (r["id"],))
        doc = await fetch_one(db, "SELECT document FROM briefs WHERE id = ?", (r["brief_id"],))
        r["options_considered"] = (loads(doc["document"], {}) if doc else {}).get("options", [])
    return rows


@router.get("/notifications")
async def notifications(
    unread_only: bool = False,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    uid, ws = session.user.user_id, session.workspace.workspace_id
    return {
        "items": await notifications_repo.list_notifications(db, uid, ws, unread_only=unread_only),
        "unread": await notifications_repo.unread_count(db, uid, ws),
    }


@router.post("/notifications/read")
async def read_notifications(
    notification_id: Optional[str] = None,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    await notifications_repo.mark_read(db, session.user.user_id, session.workspace.workspace_id, notification_id)
    return {"status": "ok"}


@router.get("/search")
async def search(
    q: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, list[dict[str, Any]]]:
    """Global search (Cmd+K): briefs, analyses, entities, documents, decisions, sources."""
    ws = session.workspace.workspace_id
    like = f"%{q.strip()}%"
    if len(q.strip()) < 2:
        return {}
    return {
        "briefs": await fetch_all(db, "SELECT id, number, title, use_case FROM briefs WHERE workspace_id = ? AND (title LIKE ? OR summary LIKE ?) ORDER BY generated_at DESC LIMIT 5", (ws, like, like)),
        "analyses": await fetch_all(db, "SELECT request_id, title, status FROM business_requests WHERE workspace_id = ? AND (title LIKE ? OR goal LIKE ?) ORDER BY updated_at DESC LIMIT 5", (ws, like, like)),
        "entities": await fetch_all(db, "SELECT id, kind, name FROM entities WHERE workspace_id = ? AND name LIKE ? LIMIT 5", (ws, like)),
        "documents": await fetch_all(db, "SELECT id, name, status FROM documents WHERE workspace_id = ? AND name LIKE ? LIMIT 5", (ws, like)),
        "decisions": await fetch_all(db, "SELECT id, title, brief_id FROM decisions WHERE workspace_id = ? AND title LIKE ? LIMIT 5", (ws, like)),
        "sources": await fetch_all(db, """SELECT e.uid, e.display_no, e.source_title, b.id AS brief_id FROM evidence e
                                          JOIN briefs b ON b.request_id = e.request_id
                                          WHERE b.workspace_id = ? AND (e.source_title LIKE ? OR e.extracted_fact LIKE ?) LIMIT 5""", (ws, like, like)),
    }


class SettingsIn(BaseModel):
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    workspace_name: Optional[str] = None
    language: Optional[str] = None
    time_zone: Optional[str] = None
    auto_approve_scheduled_plans: Optional[bool] = None
    notify_when_run_finishes: Optional[bool] = None
    export_format: Optional[str] = None
    export_include_sources: Optional[bool] = None
    export_include_limitations: Optional[bool] = None
    notifications: Optional[dict[str, dict[str, bool]]] = None   # event -> {email, in_app}


async def _settings(db: aiosqlite.Connection, session: SessionOut) -> dict[str, Any]:
    uid, ws = session.user.user_id, session.workspace.workspace_id
    user = await fetch_one(db, "SELECT id, email, full_name, job_title FROM users WHERE id = ?", (uid,))
    prefs = await fetch_one(db, "SELECT * FROM user_preferences WHERE user_id = ?", (uid,)) or {}
    wset = await fetch_one(db, "SELECT * FROM workspace_settings WHERE workspace_id = ?", (ws,)) or {}
    notif = await fetch_all(db, "SELECT event, email, in_app FROM notification_preferences WHERE user_id = ? AND workspace_id = ?", (uid, ws))
    return {
        "account": user,
        "workspace": {"id": ws, "name": session.workspace.name},
        "preferences": prefs,
        "export": wset,
        "notifications": {n["event"]: {"email": bool(n["email"]), "in_app": bool(n["in_app"])} for n in notif},
    }


@router.get("/settings")
async def get_settings(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    return await _settings(db, session)


@router.put("/settings")
async def put_settings(
    body: SettingsIn,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    uid, ws = session.user.user_id, session.workspace.workspace_id
    await auth_repo.update_user(db, uid, full_name=body.full_name, job_title=body.job_title)
    if body.workspace_name:
        await auth_repo.rename_workspace(db, ws, body.workspace_name)
    for col in ("language", "time_zone", "auto_approve_scheduled_plans", "notify_when_run_finishes"):
        val = getattr(body, col)
        if val is not None:
            await db.execute(f"UPDATE user_preferences SET {col} = ? WHERE user_id = ?", (int(val) if isinstance(val, bool) else val, uid))
    for col in ("export_format", "export_include_sources", "export_include_limitations"):
        val = getattr(body, col)
        if val is not None:
            await db.execute(f"UPDATE workspace_settings SET {col} = ? WHERE workspace_id = ?", (int(val) if isinstance(val, bool) else val, ws))
    for event, flags in (body.notifications or {}).items():
        await db.execute(
            "UPDATE notification_preferences SET email = ?, in_app = ? WHERE user_id = ? AND workspace_id = ? AND event = ?",
            (int(flags.get("email", True)), int(flags.get("in_app", True)), uid, ws, event),
        )
    await db.commit()
    return await _settings(db, session)
