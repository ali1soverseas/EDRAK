"""Action plans: draft for a chosen option, edit as new versions, approve to record the decision."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from edrak.action_plan.contracts import ActionPlan
from edrak.action_plan.generate import draft_plan
from edrak.action_plan.validate import check, find_cycle, schedule
from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.routers import briefs as briefs_router
from edrak.api.schemas import SessionOut
from edrak.db.connection import get_db
from edrak.db.repositories import action_plans as plans_repo
from edrak.db.repositories import audit as audit_repo
from edrak.db.repositories import briefs as briefs_repo
from edrak.guardrails.plan import check_plan

logger = logging.getLogger(__name__)
router = APIRouter(tags=["action-plans"])
_tasks: set[asyncio.Task] = set()


class CreateIn(BaseModel):
    option_key: str
    note: Optional[str] = Field(default=None, max_length=2000)


class EditIn(BaseModel):
    document: dict[str, Any]
    note: Optional[str] = Field(default=None, max_length=500)


class ApproveIn(BaseModel):
    title: Optional[str] = None
    rationale: Optional[str] = None


async def _brief_doc(db: aiosqlite.Connection, session: SessionOut, brief_id: str) -> dict[str, Any]:
    row = await briefs_repo.get_brief(db, brief_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    doc = dict(row["document"])
    if doc.get("cards"):
        doc["cards"] = await briefs_router.merge_card_versions(db, brief_id, dict(doc["cards"]))
    return doc


def _present(row: dict[str, Any], versions: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    out = {k: row[k] for k in ("id", "brief_id", "option_key", "version", "status", "note", "error", "created_at", "approved_at", "approved_by")}
    out["document"] = row["document"]
    if row["document"] and row["status"] in ("draft", "approved"):
        try:
            errors, warnings = check(ActionPlan.model_validate(row["document"]))
            out["issues"] = {"errors": errors, "warnings": warnings}
        except ValidationError:
            out["issues"] = {"errors": ["The plan could not be read."], "warnings": []}
    if versions is not None:
        out["versions"] = [{"id": v["id"], "version": v["version"], "status": v["status"], "created_at": v["created_at"], "note": v["note"]} for v in versions]
    return out


async def _generate(plan_id: str, brief_doc: dict[str, Any], option: dict[str, Any], note: Optional[str]) -> None:
    try:
        plan = await asyncio.to_thread(draft_plan, brief_doc, option, note)
        errors, warnings = check(plan)
        doc = plan.model_dump(mode="json")
        doc["notes"] = [*doc.get("notes", []), *warnings]
        async with get_db() as db:
            await plans_repo.finish(db, plan_id, doc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Action plan generation failed")
        async with get_db() as db:
            await plans_repo.fail(db, plan_id, f"{type(exc).__name__}: {exc}")


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.get("/briefs/{brief_id}/action-plans")
async def list_plans(brief_id: str, session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)) -> list[dict[str, Any]]:
    await _brief_doc(db, session, brief_id)
    rows = await plans_repo.for_brief(db, brief_id)
    keys = sorted({r["option_key"] for r in rows})
    out = []
    for k in keys:
        versions = [r for r in rows if r["option_key"] == k]
        live = next((r for r in reversed(versions) if r["status"] != "superseded"), versions[-1])
        out.append(_present(live, versions))
    return out


@router.post("/briefs/{brief_id}/action-plan")
async def create_plan(brief_id: str, body: CreateIn, session: SessionOut = Depends(get_current_session),
                      db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    doc = await _brief_doc(db, session, brief_id)
    option = next((o for o in doc.get("options") or [] if o.get("key") == body.option_key), None)
    if option is None:
        raise HTTPException(status_code=404, detail="option_not_found")
    if body.note:
        verdict = check_plan_text(body.note)
        if verdict:
            raise HTTPException(status_code=422, detail=verdict)
    current = await plans_repo.current(db, brief_id, body.option_key)
    if current and current["status"] == "approved":
        raise HTTPException(status_code=409, detail="plan_approved_undo_decision_first")
    if current and current["status"] == "generating":
        return {"plan_id": current["id"], "status": "generating"}
    plan_id = await plans_repo.start(db, brief_id=brief_id, workspace_id=session.workspace.workspace_id, option_key=body.option_key,
                                     user_id=session.user.user_id, note=body.note, supersedes=current)
    await audit_repo.record(db, workspace_id=session.workspace.workspace_id, action="action_plan.created", target_type="action_plan",
                            target_id=plan_id, actor_id=session.user.user_id, metadata={"option": body.option_key})
    _spawn(_generate(plan_id, doc, option, body.note))
    return {"plan_id": plan_id, "status": "generating"}


def check_plan_text(text: str) -> Optional[str]:
    from edrak.guardrails.plan import check_free_text

    return check_free_text(text)


@router.get("/action-plans/{plan_id}")
async def get_plan(plan_id: str, session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    row = await plans_repo.get(db, plan_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    versions = [r for r in await plans_repo.for_brief(db, row["brief_id"]) if r["option_key"] == row["option_key"]]
    return _present(row, versions)


@router.put("/action-plans/{plan_id}")
async def edit_plan(plan_id: str, body: EditIn, session: SessionOut = Depends(get_current_session),
                    db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    row = await plans_repo.get(db, plan_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    if row["status"] != "draft":
        raise HTTPException(status_code=409, detail="only_a_draft_can_be_edited")
    try:
        plan = ActionPlan.model_validate(body.document)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"errors": [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]]})
    problems = check_plan(plan)
    errors, warnings = check(plan)
    if errors or problems:
        raise HTTPException(status_code=422, detail={"errors": [*errors, *problems], "warnings": warnings})
    plan = schedule(plan)
    prev_doc = row["document"]
    # Baselines come from the stored metrics and are not editable.
    locked = {k["id"]: (k.get("baseline_uid"), k.get("baseline")) for k in prev_doc.get("kpis", [])}
    for k in plan.kpis:
        if k.id in locked:
            k.baseline_uid, k.baseline = locked[k.id]
        before = next((x for x in prev_doc.get("kpis", []) if x.get("id") == k.id), None)
        if before and before.get("target") != k.target:
            k.target_status = "edited"
    new_id = await plans_repo.save_version(db, row, plan.model_dump(mode="json"), session.user.user_id, body.note)
    await audit_repo.record(db, workspace_id=session.workspace.workspace_id, action="action_plan.edited", target_type="action_plan",
                            target_id=new_id, actor_id=session.user.user_id)
    saved = await plans_repo.get(db, new_id, session.workspace.workspace_id)
    return _present(saved)  # type: ignore[arg-type]


@router.post("/action-plans/{plan_id}/regenerate")
async def regenerate(plan_id: str, body: CreateIn, session: SessionOut = Depends(get_current_session),
                     db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    row = await plans_repo.get(db, plan_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    return await create_plan(row["brief_id"], CreateIn(option_key=row["option_key"], note=body.note), session, db)


@router.post("/action-plans/{plan_id}/approve")
async def approve_plan(plan_id: str, body: ApproveIn, session: SessionOut = Depends(get_current_session),
                       db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    row = await plans_repo.get(db, plan_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    if row["status"] != "draft":
        raise HTTPException(status_code=409, detail="only_a_draft_can_be_approved")
    plan = ActionPlan.model_validate(row["document"])
    errors, _ = check(plan)
    problems = check_plan(plan)
    if errors or problems:
        raise HTTPException(status_code=422, detail={"errors": [*errors, *problems]})
    steps = [a.title for w in plan.workstreams for a in sorted(w.actions, key=lambda a: a.start_week)][:6]
    steps = [a.title for a in sorted((a for w in plan.workstreams for a in w.actions), key=lambda a: (a.start_week, a.id))][:6]
    review_on: Optional[date] = None
    try:
        review_on = date.fromisoformat(plan.review_date) if plan.review_date else None
    except ValueError:
        review_on = None
    decision = briefs_router.DecisionIn(
        option_label=row["option_key"], title=(body.title or plan.chosen_option.title).strip(),
        rationale=body.rationale or plan.chosen_option.why or plan.objective, review_on=review_on, next_steps=steps)
    result = await briefs_router.create_decision(db, session, row["brief_id"], decision, action_plan_id=plan_id)
    await plans_repo.approve(db, plan_id, session.user.user_id)
    await audit_repo.record(db, workspace_id=session.workspace.workspace_id, action="action_plan.approved", target_type="action_plan",
                            target_id=plan_id, actor_id=session.user.user_id, metadata={"decision_id": result["decision_id"]})
    return {"plan_id": plan_id, "status": "approved", **result}
