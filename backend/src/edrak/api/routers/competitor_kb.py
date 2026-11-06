"""Competitor knowledge base: start builds, poll progress, read what was collected.

The onboarding screen calls PUT /onboarding (which queues builds for the saved competitors), then
polls GET /competitor-kb/status to show "Loading competitors..." until every build has finished.
"""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import SessionOut
from edrak.db.repositories import audit as audit_repo
from edrak.db.repositories import competitor_kb as kb_repo
from edrak.db.repositories import entities as entities_repo

router = APIRouter(prefix="/competitor-kb", tags=["competitor knowledge"])


class BuildIn(BaseModel):
    entity_ids: Optional[list[str]] = None   # default: every tracked competitor
    force: bool = False                      # rebuild even when the shared profile is fresh


class KbStatus(BaseModel):
    entity_id: str
    name: str
    website_domain: Optional[str] = None
    status: str                              # missing | queued | building | ready | partial | failed
    stage: Optional[str] = None
    progress: int = 0
    error_message: Optional[str] = None
    page_count: int = 0
    chunk_count: int = 0
    built_at: Optional[str] = None
    updated_at: Optional[str] = None


def _out(rows: list[dict[str, Any]]) -> list[KbStatus]:
    return [KbStatus(**{k: r.get(k) for k in KbStatus.model_fields if k in r}) for r in rows]


@router.post("/build", response_model=list[KbStatus])
async def build(
    body: BuildIn,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[KbStatus]:
    """Queue builds and return their status at once; the work runs in the background."""
    from edrak.knowledge.competitor_kb import queue_builds

    ws = session.workspace.workspace_id
    rows = await queue_builds(db, ws, body.entity_ids, force=body.force, retry_failed=True)
    await audit_repo.record(
        db, workspace_id=ws, action="competitor_kb.build", target_type="workspace", target_id=ws,
        actor_id=session.user.user_id, metadata={"entities": [r["entity_id"] for r in rows], "force": body.force},
    )
    return _out(rows)


@router.get("/status", response_model=list[KbStatus])
async def status(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[KbStatus]:
    return _out(await kb_repo.list_status(db, session.workspace.workspace_id))


@router.get("/{entity_id}")
async def knowledge(
    entity_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    """The verified profile and the list of pages stored for one tracked competitor."""
    ws = session.workspace.workspace_id
    if not await entities_repo.get_entity(db, ws, entity_id):
        raise HTTPException(status_code=404, detail="not_found")
    data = await kb_repo.knowledge_for_entity(db, ws, entity_id)
    if data is None:
        raise HTTPException(status_code=404, detail="not_built")
    return data


async def _kb_key(db: aiosqlite.Connection, session: SessionOut, entity_id: str) -> str:
    ws = session.workspace.workspace_id
    if not await entities_repo.get_entity(db, ws, entity_id):
        raise HTTPException(status_code=404, detail="not_found")
    key = await kb_repo.kb_key_for_entity(db, ws, entity_id)
    if not key:
        raise HTTPException(status_code=404, detail="not_built")
    return key


@router.get("/{entity_id}/passages")
async def passages(
    entity_id: str,
    url: str = Query(..., description="The source page whose stored passages to show."),
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    """What was stored from one page: the passages the competitor worker retrieves from."""
    return await kb_repo.list_passages(db, await _kb_key(db, session, entity_id), url)


@router.get("/{entity_id}/search")
async def search(
    entity_id: str,
    q: str = Query(..., min_length=2, max_length=300),
    k: int = Query(8, ge=1, le=20),
    market: Optional[str] = None,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    """Search one competitor's knowledge base the way the competitor worker will."""
    from edrak.knowledge.competitor_kb import search_competitor_kb

    key = await _kb_key(db, session, entity_id)
    hits = await asyncio.to_thread(search_competitor_kb, key, q, market=market, k=k, kb_key=key)
    return [
        {"text": h.get("text", ""), "score": h.get("score"), "source_url": h.get("source_url"),
         "title": h.get("title"), "source_type": h.get("source_type"), "page_type": h.get("page_type"),
         "market": h.get("market"), "fetched_at": h.get("fetched_at")}
        for h in hits
    ]
