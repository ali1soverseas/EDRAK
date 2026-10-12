"""Reading a brief."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import SessionOut
from edrak.db.repositories import briefs as briefs_repo
from edrak.db.repositories import cards as cards_repo
from edrak.db.util import loads

router = APIRouter(prefix="/briefs", tags=["briefs"])


@router.get("/{brief_id}")
async def get_brief(
    brief_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    row = await briefs_repo.get_brief(db, brief_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    document = dict(row["document"])
    document["brief_id"] = row["id"]
    document["brief_no"] = row["number"]
    document["analysis_id"] = row["request_id"]
    document["lifecycle"] = row["lifecycle"]
    document["evidence_status"] = row["evidence_status"]
    document["document_version"] = row["document_version"]
    await merge_live(db, row["id"], document)
    if document.get("cards") is not None and not document.get("deck"):
        from edrak.briefing.deck import build_deck

        try:  # briefs frozen before the five-slide deck existed get one built from their own content
            document["deck"] = build_deck(document)
        except Exception:  # noqa: BLE001 - the frontend falls back to the document's own fields
            pass
    return document


async def merge_live(db: aiosqlite.Connection, brief_id: str, document: dict[str, Any]) -> dict[str, Any]:
    """Overlay what changed since the brief was frozen: newer card versions and the figures they use."""
    if not document.get("cards"):
        return document
    document["cards"] = await merge_card_versions(db, brief_id, dict(document["cards"]))
    metrics = dict(document.get("metrics") or {})
    missing = {u for c in document["cards"].values() for u in c.get("metric_uids") or [] if u not in metrics}
    if missing:
        from edrak.db.repositories import metrics as metrics_repo

        for uid in missing:
            m = await metrics_repo.get_metric(db, uid)
            if m and m["status"] != "unverified":
                metrics[uid] = {k: m.get(k) for k in ("uid", "metric_id", "entity", "value", "value_text", "unit", "period", "label", "status", "scope",
                                                      "sample_size", "derived", "formula", "source_span", "display_no", "inputs", "worker")}
        document["metrics"] = metrics
    return document


async def merge_card_versions(db: aiosqlite.Connection, brief_id: str, cards: dict[str, Any]) -> dict[str, Any]:
    """The brief document is frozen; a challenged card has newer versions in brief_cards. Show the latest."""
    for key, row in (await cards_repo.latest(db, brief_id)).items():
        base = cards.get(key, {})
        labels = row["labels"] or {}
        cards[key] = {**base, "card_key": key, "component": row["component"], "headline": row["headline"] or base.get("headline"),
                      "why_visual": row["why_visual"], "slots": row["slots"], "status": row["status"],
                      "title": labels.get("title") or base.get("title"), "caveat": labels.get("caveat"),
                      "refs": labels.get("refs") or base.get("refs") or [], "metric_uids": labels.get("metric_uids") or base.get("metric_uids") or [],
                      "version": row["version"], "origin": row["origin"]}
    return cards


@router.get("/{brief_id}/quality")
async def brief_quality(
    brief_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    """The run's quality scores (evaluation) and what the guardrails found, for the brief header."""
    from edrak.db.repositories import guardrails as guardrails_repo
    from edrak.db.util import fetch_all, loads

    row = await briefs_repo.get_brief(db, brief_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    metrics = await fetch_all(
        db, "SELECT metric, value, detail, evaluated_at FROM run_evaluations WHERE request_id = ? ORDER BY metric",
        (row["request_id"],),
    )
    events = await guardrails_repo.list_events(db, session.workspace.workspace_id, request_id=row["request_id"])
    return {
        "metrics": {m["metric"]: {"value": m["value"], "detail": loads(m["detail"], {}), "evaluated_at": m["evaluated_at"]} for m in metrics},
        "guardrails": events,
        "evaluated": bool(metrics),
    }


# ------------------------------------------------------------------ export, review, decision

from datetime import date as _date  # noqa: E402
from urllib.parse import quote as _quote  # noqa: E402

from fastapi.responses import Response  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from edrak.db.repositories import audit as audit_repo  # noqa: E402
from edrak.db.util import fetch_all as _fetch_all, fetch_one as _fetch_one, new_id as _new_id, now_iso as _now  # noqa: E402

MEDIA = {
    "pdf": ("application/pdf", "pdf"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "markdown": ("text/markdown; charset=utf-8", "md"),
}


async def _brief(db, session, brief_id):
    row = await briefs_repo.get_brief(db, brief_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    return row


@router.get("/{brief_id}/export")
async def export_brief(
    brief_id: str,
    format: str = "pdf",
    sources: Optional[bool] = None,
    limitations: Optional[bool] = None,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> Response:
    from edrak.exports import brief as exporter

    fmt = "markdown" if format in ("md", "markdown") else format
    if fmt not in MEDIA:
        raise HTTPException(status_code=422, detail="unknown_format")
    row = await _brief(db, session, brief_id)
    defaults = await _fetch_one(db, "SELECT * FROM workspace_settings WHERE workspace_id = ?", (session.workspace.workspace_id,)) or {}
    inc_sources = bool(defaults.get("export_include_sources", 1)) if sources is None else sources
    inc_limits = bool(defaults.get("export_include_limitations", 1)) if limitations is None else limitations
    doc = {**row["document"], "brief_no": row["number"], "evidence_status": row["evidence_status"]}
    if doc.get("cards") is not None:
        await merge_live(db, brief_id, doc)
        from edrak.db.repositories import action_plans as plans_repo

        plans = [p for p in await plans_repo.for_brief(db, brief_id) if p["status"] in ("approved", "draft")]
        plans.sort(key=lambda p: (p["status"] != "approved", -p["version"]))  # an approved plan first, else the newest draft
        if plans:
            doc["action_plan"] = plans[0]["document"]
            doc["action_plan_status"] = plans[0]["status"]
    try:
        if fmt == "pdf":
            body = await exporter.to_pdf(doc, include_sources=inc_sources, include_limitations=inc_limits)
        elif fmt == "docx":
            body = exporter.to_docx(doc, include_sources=inc_sources, include_limitations=inc_limits)
        else:
            body = exporter.to_markdown(doc, include_sources=inc_sources, include_limitations=inc_limits)
    except exporter.ExportUnavailable as exc:
        raise HTTPException(status_code=501, detail={"code": "pdf_unavailable", "message": str(exc)})
    await db.execute(
        "INSERT INTO brief_exports (id, brief_id, format, include_sources, include_limitations, exported_by) VALUES (?, ?, ?, ?, ?, ?)",
        (_new_id(), brief_id, fmt, int(inc_sources), int(inc_limits), session.user.user_id),
    )
    await db.commit()
    media, ext = MEDIA[fmt]
    name = f"edrak-brief-{row['number']:04d}.{ext}"
    return Response(body, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(name)}"})


class ReviewActionIn(BaseModel):
    action: str                 # accepted_as_reviewed | requested_investigation
    note: Optional[str] = None


@router.post("/{brief_id}/review")
async def review_brief(
    brief_id: str,
    body: ReviewActionIn,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    """Accept as reviewed, or send back for more investigation (creates a follow-up analysis draft)."""
    row = await _brief(db, session, brief_id)
    follow_up = None
    if body.action == "accepted_as_reviewed":
        await briefs_repo.set_lifecycle(db, brief_id, "reviewed")
    elif body.action == "requested_investigation":
        req = await _fetch_one(db, "SELECT * FROM business_requests WHERE request_id = ?", (row["request_id"],))
        follow_up = _new_id()
        await db.execute(
            """INSERT INTO business_requests (request_id, workspace_id, created_by, title, goal, company_profile, use_case,
                 targets, competitors, markets, offering, target_customers, focus_areas, form, launched_by,
                 parent_request_id, follow_up_reason, allowed_source_types, status)
               SELECT ?, workspace_id, ?, 'Follow-up: ' || title, goal, company_profile, use_case, targets, competitors,
                 markets, offering, target_customers, focus_areas, form, 'follow_up', request_id, ?, allowed_source_types, 'draft'
               FROM business_requests WHERE request_id = ?""",
            (follow_up, session.user.user_id, body.note or "More investigation requested from the brief", req["request_id"]),
        )
    else:
        raise HTTPException(status_code=422, detail="unknown_action")
    await db.execute(
        "INSERT INTO brief_reviews (id, brief_id, reviewer_id, action, note, follow_up_request_id) VALUES (?, ?, ?, ?, ?, ?)",
        (_new_id(), brief_id, session.user.user_id, body.action, body.note, follow_up),
    )
    await db.commit()
    await audit_repo.record(db, workspace_id=session.workspace.workspace_id, action=f"brief.{body.action}",
                            target_type="brief", target_id=brief_id, actor_id=session.user.user_id)
    return {"status": "ok", "follow_up_analysis_id": follow_up}


class DecisionIn(BaseModel):
    option_label: Optional[str] = None
    title: str
    rationale: Optional[str] = None
    review_on: Optional[_date] = None
    next_steps: list[str] = Field(default_factory=list)


@router.post("/{brief_id}/decision")
async def record_decision(
    brief_id: str,
    body: DecisionIn,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    """Record the decision and lock a snapshot of the brief and every source behind it."""
    return await create_decision(db, session, brief_id, body)


async def create_decision(db: aiosqlite.Connection, session: SessionOut, brief_id: str, body: DecisionIn,
                          action_plan_id: Optional[str] = None) -> dict[str, Any]:
    row = await _brief(db, session, brief_id)
    if await _fetch_one(db, "SELECT id FROM decisions WHERE brief_id = ? AND status = 'decided'", (brief_id,)):
        raise HTTPException(status_code=409, detail="already_decided")
    decision_id, snapshot_id = _new_id(), _new_id()
    uid = session.user.user_id
    await db.execute(
        """INSERT INTO decisions (id, workspace_id, brief_id, option_label, title, rationale, owner_id, review_on, recorded_by, action_plan_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (decision_id, session.workspace.workspace_id, brief_id, body.option_label, body.title.strip(), body.rationale,
         uid, body.review_on.isoformat() if body.review_on else None, uid, action_plan_id),
    )
    for i, step in enumerate(s for s in body.next_steps if s.strip()):
        await db.execute("INSERT INTO decision_next_steps (id, decision_id, position, body, owner_id) VALUES (?, ?, ?, ?, ?)",
                         (_new_id(), decision_id, i, step.strip(), uid))
    evidence = await _fetch_all(db, "SELECT uid FROM evidence WHERE request_id = ?", (row["request_id"],))
    from edrak.db.util import dumps as _dumps

    await db.execute(
        "INSERT INTO evidence_snapshots (id, decision_id, brief_id, source_count, worker_count, brief_content) VALUES (?, ?, ?, ?, ?, ?)",
        (snapshot_id, decision_id, brief_id, len(evidence), row["worker_count"], _dumps(row["document"])),
    )
    for e in evidence:
        await db.execute("INSERT INTO evidence_snapshot_items (snapshot_id, evidence_uid) VALUES (?, ?)", (snapshot_id, e["uid"]))
    await db.execute(
        "INSERT INTO brief_reviews (id, brief_id, reviewer_id, action, decision_id) VALUES (?, ?, ?, 'decision_recorded', ?)",
        (_new_id(), brief_id, uid, decision_id),
    )
    await db.execute("UPDATE briefs SET lifecycle = 'decided' WHERE id = ?", (brief_id,))
    await db.commit()
    await audit_repo.record(db, workspace_id=session.workspace.workspace_id, action="decision.recorded",
                            target_type="decision", target_id=decision_id, actor_id=uid,
                            metadata={"option": body.option_label, "sources_locked": len(evidence)})
    return {"decision_id": decision_id, "sources_locked": len(evidence)}


@router.delete("/{brief_id}/decision")
async def undo_decision(
    brief_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    """Undo: the decision is kept in the log as superseded; its snapshot stays locked."""
    await _brief(db, session, brief_id)
    d = await _fetch_one(db, "SELECT id FROM decisions WHERE brief_id = ? AND status = 'decided'", (brief_id,))
    if not d:
        raise HTTPException(status_code=404, detail="no_decision")
    await db.execute("UPDATE decisions SET status = 'superseded' WHERE id = ?", (d["id"],))
    await db.execute("UPDATE action_plans SET status = 'superseded' WHERE brief_id = ? AND status = 'approved'", (brief_id,))
    await db.execute("UPDATE briefs SET lifecycle = 'reviewed' WHERE id = ?", (brief_id,))
    await db.execute(
        "INSERT INTO brief_reviews (id, brief_id, reviewer_id, action, decision_id) VALUES (?, ?, ?, 'decision_undone', ?)",
        (_new_id(), brief_id, session.user.user_id, d["id"]),
    )
    await db.commit()
    await audit_repo.record(db, workspace_id=session.workspace.workspace_id, action="decision.undone",
                            target_type="decision", target_id=d["id"], actor_id=session.user.user_id)
    return {"status": "undone"}


@router.get("/{brief_id}/evidence")
async def brief_evidence(
    brief_id: str,
    worker: Optional[str] = None,
    origin: Optional[str] = None,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    """Evidence explorer: every source behind a brief, with the claims that depend on it."""
    row = await _brief(db, session, brief_id)
    sql = """SELECT e.*, r.worker,
               (SELECT count(*) FROM finding_evidence fe WHERE fe.task_id = e.task_id AND fe.attempt = e.attempt AND fe.evidence_id = e.evidence_id) AS claims,
               (SELECT count(*) FROM annotations a WHERE a.evidence_uid = e.uid AND a.kind LIKE 'flag%') AS flags
             FROM evidence e JOIN worker_results r ON r.task_id = e.task_id AND r.attempt = e.attempt
             WHERE e.request_id = ?"""
    params: list[Any] = [row["request_id"]]
    if worker:
        sql += " AND r.worker = ?"
        params.append(worker)
    if origin in ("internal", "external"):
        sql += " AND e.origin = ?"
        params.append(origin)
    rows = await _fetch_all(db, sql + " ORDER BY e.display_no", params)
    for r in rows:
        r["metadata"] = loads(r["metadata"], {})
        r["used_in"] = await _fetch_all(
            db, """SELECT f.statement, f.category, fe.relation FROM finding_evidence fe JOIN findings f
                   ON f.task_id = fe.task_id AND f.attempt = fe.attempt AND f.finding_id = fe.finding_id
                   WHERE fe.task_id = ? AND fe.attempt = ? AND fe.evidence_id = ?""",
            (r["task_id"], r["attempt"], r["evidence_id"]))
    return rows


class AnnotationIn(BaseModel):
    evidence_uid: Optional[str] = None
    finding_uid: Optional[str] = None
    kind: str = "note"          # flag_wrong | flag_low_quality | note
    body: Optional[str] = None


@router.post("/{brief_id}/annotations")
async def annotate(
    brief_id: str,
    body: AnnotationIn,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    await _brief(db, session, brief_id)
    if (body.evidence_uid is None) == (body.finding_uid is None) or body.kind not in ("flag_wrong", "flag_low_quality", "note"):
        raise HTTPException(status_code=422, detail="invalid_annotation")
    aid = _new_id()
    await db.execute(
        "INSERT INTO annotations (id, workspace_id, user_id, finding_uid, evidence_uid, kind, body) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (aid, session.workspace.workspace_id, session.user.user_id, body.finding_uid, body.evidence_uid, body.kind, body.body),
    )
    await db.commit()
    await audit_repo.record(db, workspace_id=session.workspace.workspace_id, action=f"evidence.{body.kind}",
                            target_type="evidence" if body.evidence_uid else "finding",
                            target_id=body.evidence_uid or body.finding_uid, actor_id=session.user.user_id)
    return {"annotation_id": aid}
