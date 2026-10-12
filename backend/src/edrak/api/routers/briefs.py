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
