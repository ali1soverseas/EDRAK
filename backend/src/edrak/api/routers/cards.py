"""Cards of a brief: version history, and challenging or adding to a card."""

from __future__ import annotations

from typing import Any, Literal, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.routers import briefs as briefs_router
from edrak.api.schemas import SessionOut
from edrak.briefing.challenge import ChallengeError, challenge_card
from edrak.db.repositories import audit as audit_repo
from edrak.db.repositories import briefs as briefs_repo
from edrak.db.repositories import cards as cards_repo

router = APIRouter(prefix="/briefs", tags=["cards"])


class ChallengeIn(BaseModel):
    source: Literal["user", "web"] = "user"
    text: str = Field(min_length=3, max_length=4000)
    url: Optional[str] = Field(default=None, max_length=1000)
    title: Optional[str] = Field(default=None, max_length=300)
    entity: Optional[str] = Field(default=None, max_length=200, description="Which company or market, for a card that covers several.")


async def _doc(db: aiosqlite.Connection, session: SessionOut, brief_id: str) -> tuple[dict[str, Any], str]:
    row = await briefs_repo.get_brief(db, brief_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    doc = dict(row["document"])
    await briefs_router.merge_live(db, brief_id, doc)
    return doc, row["request_id"]


@router.get("/{brief_id}/cards/{card_key}/versions")
async def versions(brief_id: str, card_key: str, session: SessionOut = Depends(get_current_session),
                   db: aiosqlite.Connection = Depends(get_db_conn)) -> list[dict[str, Any]]:
    await _doc(db, session, brief_id)
    return [{"version": v["version"], "origin": v["origin"], "status": v["status"], "headline": v["headline"], "note": v["note"],
             "created_at": v["created_at"], "slots": v["slots"]} for v in await cards_repo.versions(db, brief_id, card_key)]


@router.post("/{brief_id}/cards/{card_key}/challenge")
async def challenge(brief_id: str, card_key: str, body: ChallengeIn, session: SessionOut = Depends(get_current_session),
                    db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    doc, request_id = await _doc(db, session, brief_id)
    try:
        result = await challenge_card(db, brief_id=brief_id, request_id=request_id, document=doc, card_key=card_key, source=body.source,
                                      text=body.text, entity=body.entity, url=body.url, title=body.title, user_id=session.user.user_id)
    except ChallengeError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message})
    await audit_repo.record(db, workspace_id=session.workspace.workspace_id, action="card.challenged", target_type="brief", target_id=brief_id,
                            actor_id=session.user.user_id, metadata={"card": card_key, "changed": result.get("changed"), "source": body.source})
    return result
