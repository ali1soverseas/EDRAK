"""Sign in, create account, sign out, current session."""

from __future__ import annotations

from typing import Optional

import aiosqlite
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status

from edrak.api.auth import AuthError, authenticate_user, end_session, register_user, start_session
from edrak.api.deps import SESSION_COOKIE, get_current_session, get_db_conn
from edrak.api.schemas import CreateAccountRequest, SessionOut, SignInRequest, UserOut, WorkspaceOut
from edrak.core.config import settings
from edrak.db.repositories import auth as auth_repo

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.SESSION_DAYS * 24 * 3600,
        path="/",
    )


async def _session_out(db: aiosqlite.Connection, user: dict, workspace: dict) -> SessionOut:
    onboarding = await auth_repo.onboarding_state(db, workspace["id"])
    return SessionOut(
        user=UserOut(user_id=user["id"], email=user["email"], name=user["full_name"], title=user.get("job_title")),
        workspace=WorkspaceOut(workspace_id=workspace["id"], name=workspace["name"]),
        onboarding_complete=onboarding["complete"],
        onboarding_step=onboarding["step"],
    )


@router.post("/session", response_model=SessionOut)
async def get_session(session: SessionOut = Depends(get_current_session)) -> SessionOut:
    return session


@router.post("/sign-in", response_model=SessionOut)
async def sign_in(
    req: SignInRequest,
    response: Response,
    user_agent: Optional[str] = Header(None),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> SessionOut:
    user = await authenticate_user(db, req.email, req.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    workspace = await auth_repo.get_workspace_for_user(db, user["id"])
    if not workspace:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no_workspace")
    _set_cookie(response, await start_session(db, user["id"], user_agent))
    return await _session_out(db, user, workspace)


@router.post("/create-account", response_model=SessionOut)
async def create_account(
    req: CreateAccountRequest,
    response: Response,
    user_agent: Optional[str] = Header(None),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> SessionOut:
    try:
        user, workspace = await register_user(
            db, email=req.email, password=req.password, name=req.name, title=req.title,
            workspace_name=req.workspace_name,
        )
    except AuthError as exc:
        code = str(exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT if code == "email_taken" else status.HTTP_400_BAD_REQUEST,
            detail=code,
        )
    _set_cookie(response, await start_session(db, user["id"], user_agent))
    return await _session_out(db, user, workspace)


@router.post("/sign-out")
async def sign_out(
    response: Response,
    session_token: Optional[str] = Cookie(None),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    if session_token:
        await end_session(db, session_token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "signed_out"}
