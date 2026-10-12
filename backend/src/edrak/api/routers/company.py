"""The company profile and onboarding.

GET/PUT /company keeps the shape of the MVP's company screen. /onboarding is the step-by-step
flow: every step saves only its own fields, and /onboarding/complete checks that the profile has
what the workers need before the rest of the app opens.
"""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.routers.documents import document_out
from edrak.api.schemas import CompanyFields, CompanyLink, CompanySetupOut, SessionOut
from edrak.db.repositories import audit as audit_repo
from edrak.db.repositories import company as company_repo
from edrak.db.repositories import documents as documents_repo

router = APIRouter(tags=["company"])


async def _setup_out(db: aiosqlite.Connection, workspace_id: str) -> CompanySetupOut:
    company = await company_repo.get_company(db, workspace_id)
    docs = await documents_repo.list_documents(db, workspace_id)
    return CompanySetupOut(
        name=company["name"],
        aliases=company["aliases"],
        industry=company["industry"],
        description=company["description"],
        offerings=company["offerings"],
        markets=[m["name"] for m in company["markets"]],
        strategic_goals="\n".join(company["goals"]),
        website=company["website"],
        socials=[
            CompanyLink(link_id=l["id"], platform=l["platform"], url=l["url"])
            for l in company["links"] if l["platform"] != "website"
        ],
        documents=[document_out(d) for d in docs],
        completed=company["completed"],
    )


@router.get("/company", response_model=CompanySetupOut)
async def get_company(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> CompanySetupOut:
    return await _setup_out(db, session.workspace.workspace_id)


@router.put("/company", response_model=CompanySetupOut)
async def save_company(
    fields: CompanyFields,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> CompanySetupOut:
    ws = session.workspace.workspace_id
    current = await company_repo.get_company(db, ws)
    relation = {m["name"].strip().lower(): m.get("relation") or "serves" for m in current["markets"]}
    await company_repo.save_company(
        db,
        ws,
        {
            "name": fields.name,
            "aliases": fields.aliases,
            # One industry from the older form: keep the full list when it is already one of them.
            **({} if fields.industry in current["industries"] else {"industry": fields.industry}),
            "description": fields.description,
            "offerings": fields.offerings,
            # Keep each market's relation: a market the person is only considering must not become "serves".
            "markets": [{"name": m, "relation": relation.get(m.strip().lower(), "serves")} for m in fields.markets],
            "goals": fields.strategic_goals,
            "links": [{"platform": "website", "url": fields.website}] + [
                {"platform": s.platform, "url": s.url} for s in fields.socials
            ],
        },
        user_id=session.user.user_id,
    )
    if fields.name.strip():
        await company_repo.mark_completed(db, ws)
    return await _setup_out(db, ws)


# ------------------------------------------------------------------ onboarding


class CompetitorIn(BaseModel):
    name: str
    website: Optional[str] = None
    discovered_by: str = "user"
    discovery_reason: Optional[str] = None
    discovery_confidence: Optional[float] = None
    links: list[dict[str, Any]] = Field(default_factory=list)


class MarketIn(BaseModel):
    name: str
    relation: str = "serves"


class LinkIn(BaseModel):
    platform: str
    url: str


class OnboardingUpdate(BaseModel):
    """Any subset of the profile. Only the fields sent are saved."""

    step: Optional[int] = None
    name: Optional[str] = None
    aliases: Optional[list[str]] = None
    industry: Optional[str] = None
    industries: Optional[list[str]] = None
    industry_other: Optional[str] = None
    description: Optional[str] = None
    offerings: Optional[list[str]] = None
    target_customers: Optional[str] = None
    target_customer_groups: Optional[list[str]] = None
    markets: Optional[list[MarketIn]] = None
    competitors: Optional[list[CompetitorIn]] = None
    website: Optional[str] = None
    links: Optional[list[LinkIn]] = None
    goals: Optional[list[str]] = None


class OnboardingOut(BaseModel):
    step: int
    total_steps: int
    completed: bool
    missing: list[str]
    profile: dict[str, Any]


def _missing(company: dict[str, Any]) -> list[str]:
    """What the workers need and the profile does not have yet."""
    missing = []
    if not company["name"].strip():
        missing.append("name")
    if not company["description"].strip():
        missing.append("description")
    if not company["offerings"]:
        missing.append("offerings")
    if not company["markets"]:
        missing.append("markets")
    return missing


async def _onboarding_out(db: aiosqlite.Connection, workspace_id: str) -> OnboardingOut:
    company = await company_repo.get_company(db, workspace_id)
    return OnboardingOut(
        step=company["onboarding_step"],
        total_steps=company_repo.TOTAL_ONBOARDING_STEPS,
        completed=company["completed"],
        missing=_missing(company),
        profile=company,
    )


@router.get("/onboarding", response_model=OnboardingOut)
async def get_onboarding(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> OnboardingOut:
    return await _onboarding_out(db, session.workspace.workspace_id)


@router.put("/onboarding", response_model=OnboardingOut)
async def save_onboarding(
    update: OnboardingUpdate,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> OnboardingOut:
    fields = update.model_dump(exclude_none=True)
    if "step" in fields:
        fields["onboarding_step"] = fields.pop("step")
    if "markets" in fields:
        fields["markets"] = [m for m in fields["markets"] if m["relation"] in ("serves", "considers")]
    await company_repo.save_company(db, session.workspace.workspace_id, fields, user_id=session.user.user_id)
    return await _onboarding_out(db, session.workspace.workspace_id)


@router.post("/onboarding/complete", response_model=OnboardingOut)
async def complete_onboarding(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> OnboardingOut:
    ws = session.workspace.workspace_id
    company = await company_repo.get_company(db, ws)
    missing = _missing(company)
    if missing:
        raise HTTPException(status_code=422, detail={"code": "profile_incomplete", "missing": missing})
    await company_repo.mark_completed(db, ws)
    await audit_repo.record(
        db, workspace_id=ws, action="onboarding.completed", target_type="workspace", target_id=ws,
        actor_id=session.user.user_id,
    )
    return await _onboarding_out(db, ws)
