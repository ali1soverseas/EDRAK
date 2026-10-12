"""Monitors (recurring runs) and alerts (what changed)."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import SessionOut
from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id, now_iso
from edrak.monitoring.scheduler import launch, next_run

router = APIRouter(tags=["monitors"])


class MonitorIn(BaseModel):
    name: str
    use_case: str = "competitive_intelligence"
    goal: Optional[str] = None
    frequency: str = "weekly"                 # daily | weekly | monthly | event_only
    day_of_week: Optional[int] = 1
    run_time: Optional[str] = "08:00"
    time_zone: str = "Africa/Cairo"
    focus: list[str] = Field(default_factory=lambda: ["pricing", "features"])
    entity_ids: list[str] = Field(default_factory=list)
    triggers: list[dict[str, Any]] = Field(default_factory=list)   # [{kind, config}]
    notify_email: bool = True
    notify_in_app: bool = True
    notify_min_importance: str = "medium"
    auto_approve_plans: bool = False
    status: str = "active"


async def _monitor(db, ws: str, monitor_id: str) -> dict[str, Any]:
    m = await fetch_one(db, "SELECT * FROM monitors WHERE id = ? AND workspace_id = ?", (monitor_id, ws))
    if not m:
        raise HTTPException(status_code=404, detail="not_found")
    m["focus"] = loads(m["focus"], [])
    m["entities"] = await fetch_all(db, """SELECT e.id, e.name, e.kind FROM monitor_entities me JOIN entities e ON e.id = me.entity_id
                                           WHERE me.monitor_id = ?""", (monitor_id,))
    m["triggers"] = await fetch_all(db, "SELECT * FROM monitor_triggers WHERE monitor_id = ?", (monitor_id,))
    m["last_brief"] = await fetch_one(db, """SELECT b.id, b.number, b.evidence_status, b.lifecycle FROM briefs b
                                             JOIN business_requests r ON r.request_id = b.request_id WHERE r.monitor_id = ?
                                             ORDER BY b.generated_at DESC LIMIT 1""", (monitor_id,))
    return m


async def _save(db, ws: str, uid: str, body: MonitorIn, monitor_id: Optional[str]) -> str:
    if body.frequency == "weekly" and not body.day_of_week:
        body.day_of_week = 1
    mid = monitor_id or new_id()
    values = dict(name=body.name.strip(), use_case=body.use_case, goal=body.goal, status=body.status,
                  frequency=body.frequency, day_of_week=body.day_of_week if body.frequency == "weekly" else None,
                  run_time=None if body.frequency == "event_only" else body.run_time, time_zone=body.time_zone,
                  focus=dumps(body.focus), notify_email=int(body.notify_email), notify_in_app=int(body.notify_in_app),
                  notify_min_importance=body.notify_min_importance, auto_approve_plans=int(body.auto_approve_plans))
    values["next_run_at"] = next_run(values) if body.status == "active" else None
    if monitor_id:
        await db.execute(f"UPDATE monitors SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ? AND workspace_id = ?",
                         [*values.values(), mid, ws])
    else:
        cols = ["id", "workspace_id", "created_by", *values]
        await db.execute(f"INSERT INTO monitors ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         [mid, ws, uid, *values.values()])
    await db.execute("DELETE FROM monitor_entities WHERE monitor_id = ?", (mid,))
    for eid in body.entity_ids:
        if await fetch_one(db, "SELECT 1 AS x FROM entities WHERE id = ? AND workspace_id = ?", (eid, ws)):
            await db.execute("INSERT OR IGNORE INTO monitor_entities (monitor_id, entity_id) VALUES (?, ?)", (mid, eid))
    await db.execute("DELETE FROM monitor_triggers WHERE monitor_id = ?", (mid,))
    for t in body.triggers:
        if t.get("kind") in ("document_uploaded", "news_signal", "competitor_announcement"):
            await db.execute("INSERT INTO monitor_triggers (monitor_id, kind, config, enabled) VALUES (?, ?, ?, ?)",
                             (mid, t["kind"], dumps(t.get("config") or {}), int(t.get("enabled", True))))
    await db.commit()
    return mid


@router.get("/monitors")
async def list_monitors(session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)):
    rows = await fetch_all(db, "SELECT id FROM monitors WHERE workspace_id = ? ORDER BY created_at DESC", (session.workspace.workspace_id,))
    return [await _monitor(db, session.workspace.workspace_id, r["id"]) for r in rows]


@router.post("/monitors")
async def create_monitor(body: MonitorIn, session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)):
    mid = await _save(db, session.workspace.workspace_id, session.user.user_id, body, None)
    return await _monitor(db, session.workspace.workspace_id, mid)


@router.put("/monitors/{monitor_id}")
async def update_monitor(monitor_id: str, body: MonitorIn, session: SessionOut = Depends(get_current_session),
                         db: aiosqlite.Connection = Depends(get_db_conn)):
    await _monitor(db, session.workspace.workspace_id, monitor_id)
    await _save(db, session.workspace.workspace_id, session.user.user_id, body, monitor_id)
    return await _monitor(db, session.workspace.workspace_id, monitor_id)


@router.delete("/monitors/{monitor_id}")
async def delete_monitor(monitor_id: str, session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)):
    await _monitor(db, session.workspace.workspace_id, monitor_id)
    await db.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
    await db.commit()
    return {"status": "deleted"}


@router.post("/monitors/{monitor_id}/run-now")
async def run_now(monitor_id: str, session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)):
    await _monitor(db, session.workspace.workspace_id, monitor_id)
    rid = await launch(monitor_id, manual=True)
    return {"analysis_id": rid}


# ------------------------------------------------------------------ alerts


@router.get("/alerts")
async def list_alerts(
    importance: Optional[str] = None, monitor_id: Optional[str] = None, unread_only: bool = False,
    session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn),
):
    sql = """SELECT a.*, m.name AS monitor_name, b.number AS brief_no FROM alerts a
             LEFT JOIN monitors m ON m.id = a.monitor_id LEFT JOIN briefs b ON b.id = a.brief_id
             WHERE a.workspace_id = ? AND a.status <> 'dismissed'"""
    params: list[Any] = [session.workspace.workspace_id]
    if importance:
        sql += " AND a.importance = ?"; params.append(importance)
    if monitor_id:
        sql += " AND a.monitor_id = ?"; params.append(monitor_id)
    if unread_only:
        sql += " AND a.status = 'unread'"
    rows = await fetch_all(db, sql + " ORDER BY a.created_at DESC LIMIT 200", params)
    for r in rows:
        r["evidence"] = await fetch_all(db, """SELECT e.uid, e.display_no, e.source_title, e.source_url FROM alert_evidence ae
                                               JOIN evidence e ON e.uid = ae.evidence_uid WHERE ae.alert_id = ?""", (r["id"],))
    digests = await fetch_all(db, "SELECT * FROM digests WHERE workspace_id = ? ORDER BY week_start DESC LIMIT 8", (session.workspace.workspace_id,))
    return {"alerts": rows, "digests": digests}


class AlertAction(BaseModel):
    action: str        # read | dismiss | escalate | recheck


@router.post("/alerts/{alert_id}")
async def act_on_alert(alert_id: str, body: AlertAction, session: SessionOut = Depends(get_current_session),
                       db: aiosqlite.Connection = Depends(get_db_conn)):
    ws, uid = session.workspace.workspace_id, session.user.user_id
    alert = await fetch_one(db, "SELECT * FROM alerts WHERE id = ? AND workspace_id = ?", (alert_id, ws))
    if not alert:
        raise HTTPException(status_code=404, detail="not_found")
    escalated = None
    if body.action == "read":
        status = "read"
    elif body.action == "dismiss":
        status = "dismissed"
    elif body.action in ("escalate", "recheck"):
        status = "escalated" if body.action == "escalate" else "recheck_started"
        from edrak.api.routers.analyses import draft
        from edrak.api.schemas import AnalysisForm

        form = AnalysisForm(use_case="competitive_intelligence",
                            goal=f"Investigate: {alert['title']}. {alert.get('what_changed') or ''}".strip())
        row = await draft(db, form=form, workspace_id=ws, user_id=uid)
        escalated = row["request_id"]
    else:
        raise HTTPException(status_code=422, detail="unknown_action")
    await db.execute("UPDATE alerts SET status = ?, acted_by = ?, acted_at = ?, read_at = coalesce(read_at, ?), escalated_request_id = coalesce(?, escalated_request_id) WHERE id = ?",
                     (status, uid, now_iso(), now_iso(), escalated, alert_id))
    await db.commit()
    return {"status": status, "analysis_id": escalated}


@router.post("/alerts/read-all")
async def read_all(session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)):
    await db.execute("UPDATE alerts SET status = 'read', read_at = ? WHERE workspace_id = ? AND status = 'unread'",
                     (now_iso(), session.workspace.workspace_id))
    await db.commit()
    return {"status": "ok"}
