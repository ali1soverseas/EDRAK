"""Reading a brief."""

from __future__ import annotations

from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import SessionOut
from edrak.db.repositories import briefs as briefs_repo

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
    return document


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
