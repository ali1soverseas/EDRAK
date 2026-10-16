"""The live run: snapshot, event stream, cancel."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.run_manager import run_manager
from edrak.api.schemas import RunViewOut, SessionOut
from edrak.api.streaming import format_sse_event
from edrak.db.repositories import analyses as analyses_repo
from edrak.db.repositories import audit as audit_repo

router = APIRouter(prefix="/analyses", tags=["runs"])

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


async def _require(db: aiosqlite.Connection, session: SessionOut, analysis_id: str) -> None:
    if not await analyses_repo.get_request(db, analysis_id, session.workspace.workspace_id):
        raise HTTPException(status_code=404, detail="not_found")


@router.get("/{analysis_id}/run", response_model=RunViewOut)
async def get_run_view(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> RunViewOut:
    await _require(db, session, analysis_id)
    view = await run_manager.get_run_view(analysis_id, session.workspace.workspace_id)
    if not view:
        raise HTTPException(status_code=404, detail="not_found")
    return view


@router.get("/{analysis_id}/run/stream")
async def stream_run(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
):
    await _require(db, session, analysis_id)
    stream = run_manager.get_stream(analysis_id)
    if stream is None or stream.closed:
        view = await run_manager.get_run_view(analysis_id, session.workspace.workspace_id)
        if not view:
            raise HTTPException(status_code=404, detail="not_found")

        async def snapshot():
            yield format_sse_event("complete", {"state": view.state, "brief_id": view.brief_id})

        return StreamingResponse(snapshot(), media_type="text/event-stream", headers=SSE_HEADERS)
    return StreamingResponse(stream.stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/{analysis_id}/cancel")
async def cancel_run(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    await _require(db, session, analysis_id)
    if not await run_manager.cancel_run(analysis_id):
        raise HTTPException(status_code=409, detail="wrong_state")
    await audit_repo.record(
        db, workspace_id=session.workspace.workspace_id, action="run.cancelled", target_type="run",
        target_id=analysis_id, actor_id=session.user.user_id,
    )
    return {"status": "cancelled", "analysis_id": analysis_id}


class ReviewIn(BaseModel):
    """Checkpoint 2: continue to synthesis, send workers out again, or stop."""

    decision: str
    tasks: list[dict[str, Any]] = Field(default_factory=list)   # [{task_id?, worker, goal, focus}] for replan
    note: Optional[str] = None


@router.post("/{analysis_id}/review")
async def review_run(
    analysis_id: str,
    body: ReviewIn,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    await _require(db, session, analysis_id)
    try:
        await run_manager.submit_review(
            analysis_id, decision=body.decision, tasks=body.tasks, note=body.note,
            user_id=session.user.user_id, user_name=session.user.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409 if str(exc) == "no_pending_review" else 422, detail=str(exc))
    await audit_repo.record(
        db, workspace_id=session.workspace.workspace_id, action="run.reviewed", target_type="run",
        target_id=analysis_id, actor_id=session.user.user_id,
        metadata={"decision": body.decision, "tasks": len(body.tasks), "note": body.note},
    )
    return {"status": "resumed", "decision": body.decision}


@router.post("/{analysis_id}/pause")
async def pause_run(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    await _require(db, session, analysis_id)
    if not await run_manager.pause_run(analysis_id):
        raise HTTPException(status_code=409, detail="wrong_state")
    return {"status": "pausing"}


@router.post("/{analysis_id}/resume")
async def resume_run(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    await _require(db, session, analysis_id)
    try:
        await run_manager.resume_run(analysis_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "resumed"}


@router.post("/{analysis_id}/notify-instead")
async def notify_instead(
    analysis_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    """The person leaves the run screen; the bell tells them when the run needs them or is done."""
    await _require(db, session, analysis_id)
    await run_manager.notify_instead(analysis_id)
    return {"status": "ok"}
