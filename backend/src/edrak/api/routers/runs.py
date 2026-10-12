"""The live run: snapshot, event stream, cancel."""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

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
