"""Company research: start and follow a research run, and review the facts it found.

A run reads the company's own website and PDFs (and, when enabled, other sources) and saves what it
finds as suggested facts. Nothing is used until a person approves it here.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import SessionOut
from edrak.company_profile.url_import import UrlBlocked
from edrak.company_research import CATEGORIES, CompanyFact, FactStatus, ResearchRunStatus, SourceTier, ValueType, is_category
from edrak.company_research.agent import NoWebsite, research_manager
from edrak.db.repositories import audit as audit_repo
from edrak.db.repositories import company_facts as facts_repo
from edrak.db.repositories import fact_keys as keys_repo
from edrak.db.repositories import research_runs as runs_repo

router = APIRouter(tags=["company-research"])

BULK_LIMIT = 500


# ------------------------------------------------------------------ shapes


class ResearchRunOut(BaseModel):
    id: str
    status: ResearchRunStatus
    stage: Optional[str] = Field(default=None, description="The step the run is in while it runs.")
    input: dict[str, Any] = Field(default_factory=dict, description="The address researched.")
    pages_crawled: int = 0
    pdfs_read: int = 0
    facts_found: int = 0
    stats: dict[str, Any] = Field(default_factory=dict, description="Counts from each step.")
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class ResearchRunIn(BaseModel):
    seed_url: Optional[str] = Field(default=None, description="The website to research. The company's saved website when not given.")


class FactOut(BaseModel):
    id: str
    category: str
    proposed_category: Optional[str] = None
    key: str
    key_label_en: Optional[str] = Field(default=None, description="Set when the key is a canonical key.")
    key_label_ar: Optional[str] = None
    value: str
    value_type: ValueType
    source_tier: SourceTier
    source_url: str
    pdf_page: Optional[int] = None
    quote: str
    observed_at: str
    confidence: str
    status: FactStatus
    conflict_group: Optional[str] = None
    run_id: Optional[str] = None
    edited_by_user: bool
    created_at: str
    updated_at: str


class ConflictOut(BaseModel):
    conflict_group: str
    key: str
    facts: list[FactOut]


class CategoryFactsOut(BaseModel):
    category: str
    label_en: str
    label_ar: str
    facts: list[FactOut] = Field(description="Facts that agree with every other fact of their key.")
    conflicts: list[ConflictOut] = Field(description="Facts of one key with different values, shown together.")


class FactsOut(BaseModel):
    total: int
    categories: list[CategoryFactsOut]


class CategoryOut(BaseModel):
    key: str
    label_en: str
    label_ar: str


class FactPatch(BaseModel):
    """Approve or reject a fact, or edit its value. An edit also approves the fact."""

    status: Optional[Literal["approved", "rejected"]] = None
    value: Optional[str] = None
    value_type: Optional[ValueType] = Field(default=None, description="Only with a value.")


class BulkIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=BULK_LIMIT)
    action: Literal["approve", "reject"]


class BulkOut(BaseModel):
    action: str
    updated: list[str]
    not_found: list[str]


def _run_out(run: Any) -> ResearchRunOut:
    return ResearchRunOut.model_validate(run.model_dump(exclude={"workspace_id"}))


def _fact_out(fact: CompanyFact, labels: dict[tuple[str, str], tuple[str, str]]) -> FactOut:
    label_en, label_ar = labels.get((fact.category, fact.key), (None, None))
    return FactOut.model_validate({**fact.model_dump(), "key_label_en": label_en, "key_label_ar": label_ar})


async def _labels(db: aiosqlite.Connection) -> dict[tuple[str, str], tuple[str, str]]:
    return {(k.category, k.key): (k.label_en, k.label_ar) for k in await keys_repo.list_fact_keys(db)}


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": f"{what}_not_found", "message": f"No such {what.replace('_', ' ')}."})


# ------------------------------------------------------------------ runs


@router.post("/company/research/runs", response_model=ResearchRunOut, status_code=202)
async def start_research_run(
    body: Optional[ResearchRunIn] = None,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> ResearchRunOut:
    ws = session.workspace.workspace_id
    try:
        run = await research_manager.start(ws, seed_url=body.seed_url if body else None)
    except NoWebsite as exc:
        raise HTTPException(status_code=422, detail={"code": "no_website", "message": str(exc)}) from exc
    except UrlBlocked as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_url", "message": str(exc)}) from exc
    except runs_repo.ResearchRunActive as exc:
        active = await runs_repo.active_research_run(db, ws)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "run_active", "message": "A research run is already in progress for this company.",
                "run_id": active.id if active else None,
            },
        ) from exc
    await audit_repo.record(
        db, workspace_id=ws, action="company_research.started", target_type="research_run", target_id=run.id,
        actor_id=session.user.user_id,
    )
    return _run_out(run)


@router.get("/company/research/runs", response_model=list[ResearchRunOut])
async def list_research_runs(
    limit: int = Query(default=10, ge=1, le=50),
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[ResearchRunOut]:
    """The company's runs, newest first. A screen opens on the first one to show what is running or what the last run found."""
    runs = await runs_repo.list_research_runs(db, session.workspace.workspace_id, limit=limit)
    return [_run_out(run) for run in runs]


@router.get("/company/research/runs/{run_id}", response_model=ResearchRunOut)
async def get_research_run(
    run_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> ResearchRunOut:
    run = await runs_repo.get_research_run(db, session.workspace.workspace_id, run_id)
    if run is None:
        raise _not_found("research_run")
    return _run_out(run)


@router.post("/company/research/runs/{run_id}/cancel", response_model=ResearchRunOut)
async def cancel_research_run(
    run_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> ResearchRunOut:
    ws = session.workspace.workspace_id
    if await runs_repo.get_research_run(db, ws, run_id) is None:
        raise _not_found("research_run")
    if not await research_manager.cancel(ws, run_id):
        raise HTTPException(status_code=409, detail={"code": "wrong_state", "message": "This run has already ended."})
    await audit_repo.record(
        db, workspace_id=ws, action="company_research.cancelled", target_type="research_run", target_id=run_id,
        actor_id=session.user.user_id,
    )
    run = await runs_repo.get_research_run(db, ws, run_id)
    assert run is not None
    return _run_out(run)


# ------------------------------------------------------------------ facts


@router.get("/company/fact-categories", response_model=list[CategoryOut])
async def list_fact_categories(session: SessionOut = Depends(get_current_session)) -> list[CategoryOut]:
    return [CategoryOut(key=c.key, label_en=c.label_en, label_ar=c.label_ar) for c in CATEGORIES]


@router.get("/company/facts", response_model=FactsOut)
async def list_company_facts(
    status: Optional[list[FactStatus]] = Query(default=None, description="Repeat to allow several."),
    category: Optional[list[str]] = Query(default=None, description="Repeat to allow several."),
    source_tier: Optional[list[SourceTier]] = Query(default=None, description="Repeat to allow several."),
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> FactsOut:
    """Facts grouped by category, in the category list's order. Facts of one key that disagree are
    returned together under `conflicts` (a group needs two facts that pass the filters)."""
    for name in category or []:
        if not is_category(name):
            raise HTTPException(status_code=422, detail={"code": "unknown_category", "message": f"Unknown category: {name}"})
    facts = await facts_repo.list_facts(
        db, session.workspace.workspace_id, category=category, status=status, source_tier=source_tier
    )
    labels = await _labels(db)
    by_category: dict[str, list[CompanyFact]] = {}
    for fact in facts:
        by_category.setdefault(fact.category, []).append(fact)
    categories = []
    for cat in CATEGORIES:
        members = by_category.get(cat.key)
        if not members:
            continue
        groups: dict[str, list[CompanyFact]] = {}
        for fact in members:
            if fact.conflict_group:
                groups.setdefault(fact.conflict_group, []).append(fact)
        conflicts = {g: fs for g, fs in groups.items() if len(fs) > 1}
        categories.append(CategoryFactsOut(
            category=cat.key, label_en=cat.label_en, label_ar=cat.label_ar,
            facts=[_fact_out(f, labels) for f in members if f.conflict_group not in conflicts],
            conflicts=[
                ConflictOut(conflict_group=g, key=fs[0].key, facts=[_fact_out(f, labels) for f in fs])
                for g, fs in conflicts.items()
            ],
        ))
    return FactsOut(total=len(facts), categories=categories)


@router.patch("/company/facts/{fact_id}", response_model=FactOut)
async def update_company_fact(
    fact_id: str,
    body: FactPatch,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> FactOut:
    ws = session.workspace.workspace_id
    if body.value is None and body.value_type is not None:
        raise HTTPException(status_code=422, detail={"code": "type_without_value", "message": "A value type goes with a new value."})
    if body.value is None and body.status is None:
        raise HTTPException(status_code=422, detail={"code": "nothing_to_change", "message": "Send a status or a value."})
    if body.value is not None and body.status == "rejected":
        raise HTTPException(status_code=422, detail={"code": "edit_and_reject", "message": "An edit approves the fact; it cannot also reject it."})
    if await facts_repo.get_fact(db, ws, fact_id) is None:
        raise _not_found("fact")
    try:
        if body.value is not None:
            await facts_repo.edit_value(db, ws, fact_id, body.value, value_type=body.value_type, commit=False)
            await facts_repo.update_status(db, ws, fact_id, FactStatus.APPROVED, commit=False)
            action = "edited"
        else:
            await facts_repo.update_status(db, ws, fact_id, FactStatus(body.status), commit=False)
            action = body.status
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"code": "invalid_value", "message": str(exc)}) from exc
    await audit_repo.record(
        db, workspace_id=ws, action=f"company_fact.{action}", target_type="company_fact", target_id=fact_id,
        actor_id=session.user.user_id, commit=False,
    )
    await db.commit()
    fact = await facts_repo.get_fact(db, ws, fact_id)
    assert fact is not None
    return _fact_out(fact, await _labels(db))


@router.post("/company/facts/bulk", response_model=BulkOut)
async def bulk_update_company_facts(
    body: BulkIn,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> BulkOut:
    """Approve or reject many facts in one transaction. Ids that are not this company's facts are reported, not an error."""
    ws = session.workspace.workspace_id
    status = FactStatus.APPROVED if body.action == "approve" else FactStatus.REJECTED
    updated, missing = [], []
    for fact_id in dict.fromkeys(body.ids):
        if await facts_repo.update_status(db, ws, fact_id, status, commit=False) is None:
            missing.append(fact_id)
        else:
            updated.append(fact_id)
    if updated:
        await audit_repo.record(
            db, workspace_id=ws, action=f"company_fact.bulk_{body.action}", target_type="company_fact", target_id=None,
            actor_id=session.user.user_id, metadata={"count": len(updated)}, commit=False,
        )
    await db.commit()
    return BulkOut(action=body.action, updated=updated, not_found=missing)
