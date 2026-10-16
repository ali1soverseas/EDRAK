"""Watchlist entities: competitors, markets and topics, and competitor discovery."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import SessionOut
from edrak.db.repositories import audit as audit_repo
from edrak.db.repositories import company as company_repo
from edrak.db.repositories import entities as entities_repo
from edrak.db.util import fetch_all

router = APIRouter(prefix="/entities", tags=["watchlist"])


class EntityIn(BaseModel):
    kind: str = "competitor"
    name: str
    website: Optional[str] = None
    sector: Optional[str] = None
    country: Optional[str] = None
    links: list[dict[str, Any]] = Field(default_factory=list)
    discovered_by: str = "user"
    discovery_reason: Optional[str] = None
    discovery_confidence: Optional[float] = None


class DiscoverIn(BaseModel):
    limit: int = 5
    markets: Optional[list[str]] = None   # defaults to the company's markets


class Candidate(BaseModel):
    name: str
    website: Optional[str] = None
    reason: str = ""
    confidence: float = 0.5
    links: list[dict[str, Any]] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)
    already_tracked: bool = False


async def _require(db: aiosqlite.Connection, session: SessionOut, entity_id: str) -> dict[str, Any]:
    entity = await entities_repo.get_entity(db, session.workspace.workspace_id, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="not_found")
    return entity


@router.get("")
async def list_entities(
    kind: Optional[str] = None,
    include_untracked: bool = False,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    rows = await entities_repo.list_entities(
        db, session.workspace.workspace_id, kind=kind, tracked_only=not include_untracked
    )
    counts = {
        r["entity_id"]: r["n"]
        for r in await fetch_all(
            db,
            "SELECT be.entity_id, count(*) AS n FROM brief_entities be JOIN briefs b ON b.id = be.brief_id "
            "WHERE b.workspace_id = ? GROUP BY be.entity_id",
            (session.workspace.workspace_id,),
        )
    }
    for r in rows:
        r["appears_in_reports"] = counts.get(r["id"], 0)
        r["is_tracked"] = bool(r["is_tracked"])
    return rows


@router.post("")
async def create_entity(
    body: EntityIn,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    if body.kind not in entities_repo.ENTITY_KINDS or not body.name.strip():
        raise HTTPException(status_code=422, detail="invalid_entity")
    entity = await entities_repo.upsert_entity(
        db, session.workspace.workspace_id, kind=body.kind, name=body.name, created_by=session.user.user_id,
        website=body.website, sector=body.sector, country=body.country, discovered_by=body.discovered_by,
        discovery_reason=body.discovery_reason, discovery_confidence=body.discovery_confidence,
    )
    for link in body.links:
        await entities_repo.add_entity_link(
            db, entity["id"], link.get("platform", ""), link.get("url", ""),
            verified=bool(link.get("verified")), found_on_url=link.get("found_on_url"),
        )
    await db.commit()
    links = await entities_repo.entity_links(db, [entity["id"]])
    return {**entity, "links": links[entity["id"]]}


@router.post("/{entity_id}/track")
async def track(
    entity_id: str,
    tracked: bool = True,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    await _require(db, session, entity_id)
    await entities_repo.set_tracked(db, session.workspace.workspace_id, entity_id, tracked)
    await db.commit()
    return {"status": "tracked" if tracked else "untracked"}


@router.post("/discover", response_model=list[Candidate])
async def discover(
    body: DiscoverIn,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[Candidate]:
    """Find competitors from the company profile. Nothing is saved: the person picks which to keep."""
    from edrak.agents.competitor_intelligence.discovery import discover_competitors

    company = await company_repo.get_company(db, session.workspace.workspace_id)
    if not company["name"].strip():
        raise HTTPException(status_code=422, detail={"code": "profile_incomplete", "missing": ["name"]})
    tracked = {c["name"].lower() for c in company["competitors"]}
    found = await asyncio.to_thread(
        discover_competitors,
        company=company["name"],
        description=company["description"] or None,
        offerings=company["offerings"],
        industry=company_repo.industry_text(company) or None,
        markets=body.markets or [m["name"] for m in company["markets"]],
        target_customers=company["target_customers"] or None,
        exclude=company["aliases"],
        limit=max(1, min(body.limit, 8)),
    )
    await audit_repo.record(
        db, workspace_id=session.workspace.workspace_id, action="competitors.discovered", target_type="workspace",
        target_id=session.workspace.workspace_id, actor_id=session.user.user_id,
        metadata={"found": [c["name"] for c in found]},
    )
    return [Candidate(**c, already_tracked=c["name"].lower() in tracked) for c in found]


@router.post("/{entity_id}/links/refresh")
async def refresh_links(
    entity_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, Any]:
    """Read the entity's official social accounts again from its website."""
    from edrak.agents.competitor_intelligence.socials import find_official_socials

    entity = await _require(db, session, entity_id)
    if not entity.get("website_domain"):
        raise HTTPException(status_code=422, detail="no_website")
    links = await asyncio.to_thread(find_official_socials, f"https://{entity['website_domain']}")
    for link in links:
        await entities_repo.add_entity_link(
            db, entity_id, link["platform"], link["url"], verified=True, found_on_url=link.get("found_on_url"),
        )
    await db.commit()
    return {"links": (await entities_repo.entity_links(db, [entity_id]))[entity_id]}
