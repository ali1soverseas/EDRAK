"""Analyses: draft a plan, review it (checkpoint 1), run it."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from edrak.api.analysis_service import (
    TOGGLE_TO_SOURCE_TYPES,
    discover_for,
    allowed_source_types,
    build_request,
    derive_title,
    write_title,
    draft_plan as plan_for,
    estimate_minutes,
    workers_for,
)
from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.run_manager import run_manager
from edrak.api.schemas import (
    AnalysisDetailOut,
    AnalysisForm,
    AnalysisSummaryOut,
    DraftPlanRequest,
    RejectPlanRequest,
    SessionOut,
)
from edrak.contracts import UseCase, WorkerType
from edrak.db.repositories import entities as entities_repo
from edrak.db.repositories import guardrails as guardrails_repo
from edrak.core.config import settings
from edrak.guardrails.query import check_query
from edrak.db.repositories import analyses as analyses_repo
from edrak.db.repositories import audit as audit_repo
from edrak.db.repositories import company as company_repo
from edrak.db.util import new_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analyses", tags=["analyses"])


def _toggles(source_types: list[str]) -> list[str]:
    allowed = set(source_types)
    return [toggle for toggle, types in TOGGLE_TO_SOURCE_TYPES.items() if allowed & set(types)]


async def _detail(db: aiosqlite.Connection, row: dict[str, Any]) -> AnalysisDetailOut:
    request = analyses_repo.request_contract(row)
    plan_row = await analyses_repo.current_plan(db, row["request_id"])
    plan = None
    if plan_row and plan_row["status"] in ("awaiting_approval", "approved"):
        plan = await analyses_repo.plan_contract(db, request, plan_row, enabled_only=False)
    form = row["form"] or {}
    form.setdefault("use_case", row["use_case"])
    form.setdefault("goal", row["goal"])
    return AnalysisDetailOut(
        analysis_id=row["request_id"],
        title=row["title"],
        status=row["status"],
        request=request,
        plan=plan,
        form=AnalysisForm(**form),
        rejection_reason=row.get("rejection_reason"),
        allowed_sources=_toggles(row["allowed_source_types"]),
    )


async def _require(db: aiosqlite.Connection, session: SessionOut, analysis_id: str) -> dict[str, Any]:
    row = await analyses_repo.get_request(db, analysis_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    return row


@router.get("", response_model=list[AnalysisSummaryOut])
async def list_analyses(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[AnalysisSummaryOut]:
    rows = await analyses_repo.list_requests(db, session.workspace.workspace_id)
    return [
        AnalysisSummaryOut(
            analysis_id=r["request_id"], title=r["title"], use_case=UseCase(r["use_case"]), status=r["status"],
            updated_at=r["updated_at"], repeat_weekly=r["repeat_weekly"], tasks_total=r["tasks_total"],
            tasks_done=r["tasks_done"], tasks_failed=r["tasks_failed"], brief_id=r["brief_id"],
            brief_no=r["brief_no"], verified=r["verified"],
        )
        for r in rows
    ]


@router.get("/{analysis_id}", response_model=AnalysisDetailOut)
async def get_analysis(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> AnalysisDetailOut:
    return await _detail(db, await _require(db, session, analysis_id))


async def draft(
    db: aiosqlite.Connection,
    *,
    form: AnalysisForm,
    workspace_id: str,
    user_id: Optional[str],
    analysis_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create or re-draft an analysis and write a new plan version awaiting approval."""
    company = await company_repo.get_company(db, workspace_id)
    existing = await analyses_repo.get_request(db, analysis_id, workspace_id) if analysis_id else None
    # Guardrail: the question is checked before anything is planned or searched.
    verdict = check_query(
        form.goal,
        use_model=settings.GUARDRAIL_SCOPE_CHECK,
        context=" ".join([*form.competitors, form.market, form.offering, form.target_customers or ""]),
    )
    if verdict.blocked:
        await guardrails_repo.record_checks(db, workspace_id=workspace_id, stage="query", checks=verdict.checks,
                                            request_id=existing["request_id"] if existing else None)
        raise HTTPException(status_code=422, detail={"code": verdict.code, "message": verdict.reason})
    if verdict.action == "redacted":
        form = form.model_copy(update={"goal": verdict.text})
    if existing and existing["status"] not in ("draft", "awaiting_approval", "planning"):
        raise HTTPException(status_code=409, detail="wrong_state")
    request_id = existing["request_id"] if existing else new_id()
    source_types = allowed_source_types(form)
    request, inputs = build_request(
        request_id=request_id, form=form, company=company, workspace_id=workspace_id,
        rejection_feedback=existing.get("rejection_reason") if existing else None,
    )
    needs_competitors = form.use_case in (UseCase.COMPETITIVE_INTELLIGENCE, UseCase.PRODUCT_LAUNCH)
    if needs_competitors and not inputs["competitors"] and WorkerType.COMPETITOR_INTELLIGENCE in workers_for(source_types):
        # Nobody was named or tracked: find them now, so the plan shows who will be researched.
        found = await asyncio.to_thread(discover_for, company, inputs["markets"], inputs["offering"])
        if found:
            for c in found:
                entity = await entities_repo.upsert_entity(
                    db, workspace_id, kind="competitor", name=c["name"], created_by=user_id, website=c["website"],
                    discovered_by="agent", discovery_reason=c["discovery_reason"],
                    discovery_confidence=c["discovery_confidence"],
                )
                c["entity_id"] = entity["id"]
                for link in c["links"]:
                    await entities_repo.add_entity_link(
                        db, entity["id"], link["platform"], link["url"], verified=True, found_on_url=link.get("found_on_url"),
                    )
            await db.commit()
            company = {**company, "competitors": [
                {"entity_id": c["entity_id"], "name": c["name"], "website": c["website"], "links": c["links"],
                 "discovered_by": "agent"} for c in found
            ]}
            request, inputs = build_request(
                request_id=request_id, form=form, company=company, workspace_id=workspace_id,
                rejection_feedback=existing.get("rejection_reason") if existing else None,
            )
    title = await asyncio.to_thread(write_title, form, (company or {}).get("name") or "")
    app_fields = dict(
        title=title, form=form.model_dump(mode="json"), competitors=inputs["competitors"], markets=inputs["markets"],
        offering=inputs["offering"], target_customers=inputs["target_customers"],
        allowed_source_types=source_types, repeat_weekly=1 if form.repeat_weekly else 0,
    )
    if existing:
        await analyses_repo.update_request_contract(db, request, **app_fields)
    else:
        await analyses_repo.create_request(
            db, request=request, workspace_id=workspace_id, created_by=user_id, title=title,
            form=form.model_dump(mode="json"), competitors=inputs["competitors"], markets=inputs["markets"],
            offering=inputs["offering"], target_customers=inputs["target_customers"],
            allowed_source_types=source_types, repeat_weekly=form.repeat_weekly,
        )

    await guardrails_repo.record_checks(db, workspace_id=workspace_id, stage="query", checks=verdict.checks,
                                        request_id=request_id)
    plan = plan_for(request, workers_for(source_types))
    topic = next((t.business_context.offering for t in plan.tasks if t.business_context.offering), None)
    if (topic and not inputs["offering"]) or request.extras.get("custom_metrics"):
        # Keep what the planner settled on, so the run and the brief use it: the offering when the form named none,
        # and the figures it proposed for a field the fixed metric sets do not cover (saved in extras).
        await analyses_repo.update_request_contract(db, request, **({"offering": topic} if topic and not inputs["offering"] else {}))
    await analyses_repo.save_plan(db, plan, est_minutes=estimate_minutes(plan))
    await analyses_repo.set_request_status(db, request_id, "awaiting_approval")
    row = await analyses_repo.get_request(db, request_id)
    assert row is not None
    return row


@router.post("/draft", response_model=AnalysisDetailOut)
async def draft_plan(
    req: DraftPlanRequest,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> AnalysisDetailOut:
    row = await draft(
        db, form=req.form, workspace_id=session.workspace.workspace_id, user_id=session.user.user_id,
        analysis_id=req.analysis_id,
    )
    return await _detail(db, row)


@router.post("/{analysis_id}/approve")
async def approve_plan(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    """Checkpoint 1: nothing runs until the plan is approved, and it is approved only once."""
    row = await _require(db, session, analysis_id)
    plan_row = await analyses_repo.current_plan(db, analysis_id)
    if row["status"] != "awaiting_approval" or not plan_row or plan_row["status"] != "awaiting_approval":
        raise HTTPException(status_code=409, detail="wrong_state")
    if not await analyses_repo.approve_plan(db, plan_row["plan_id"], user_id=session.user.user_id):
        raise HTTPException(status_code=409, detail="wrong_state")
    await audit_repo.record(
        db, workspace_id=session.workspace.workspace_id, action="plan.approved", target_type="research_plan",
        target_id=plan_row["plan_id"], actor_id=session.user.user_id, metadata={"request_id": analysis_id},
    )
    try:
        await run_manager.start_run(analysis_id, user_id=session.user.user_id, approver_name=session.user.name)
    except ValueError as exc:
        logger.warning("Run for %s did not start: %s", analysis_id, exc)
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "started", "analysis_id": analysis_id}


@router.post("/{analysis_id}/reject")
async def reject_plan(
    analysis_id: str,
    req: RejectPlanRequest,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    row = await _require(db, session, analysis_id)
    plan_row = await analyses_repo.current_plan(db, analysis_id)
    if row["status"] != "awaiting_approval" or not plan_row:
        raise HTTPException(status_code=409, detail="wrong_state")
    await analyses_repo.reject_plan(db, plan_row["plan_id"], user_id=session.user.user_id, reason=req.reason)
    await analyses_repo.set_request_status(db, analysis_id, "draft", rejection_reason=req.reason or "Rejected")
    await audit_repo.record(
        db, workspace_id=session.workspace.workspace_id, action="plan.rejected", target_type="research_plan",
        target_id=plan_row["plan_id"], actor_id=session.user.user_id, metadata={"reason": req.reason},
    )
    return {"status": "rejected", "analysis_id": analysis_id}


@router.post("/{analysis_id}/rerun")
async def rerun_analysis(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    """A new analysis from the same form, with a fresh plan to approve. The old run stays as it was."""
    row = await _require(db, session, analysis_id)
    form = AnalysisForm(**(row["form"] or {"use_case": row["use_case"], "goal": row["goal"]}))
    new_row = await draft(db, form=form, workspace_id=session.workspace.workspace_id, user_id=session.user.user_id)
    return {"analysis_id": new_row["request_id"]}


@router.get("/{analysis_id}/guardrails")
async def analysis_guardrails(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    await _require(db, session, analysis_id)
    return await guardrails_repo.list_events(db, session.workspace.workspace_id, request_id=analysis_id)
