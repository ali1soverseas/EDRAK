"""FastAPI dependencies: database connection and the signed-in session."""

from __future__ import annotations

from typing import AsyncGenerator, Optional

import aiosqlite
from fastapi import Cookie, Depends, Header, HTTPException, status

from edrak.api.auth import resolve_session
from edrak.api.schemas import SessionOut, UserOut, WorkspaceOut
from edrak.db.connection import get_db
from edrak.db.repositories.auth import onboarding_state

SESSION_COOKIE = "session_token"


async def get_db_conn() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with get_db() as db:
        yield db


def _token(session_token: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if session_token:
        return session_token
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    return None


async def get_current_session(
    session_token: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> SessionOut:
    """The signed-in user and their workspace. 401 when there is no live session."""
    token = _token(session_token, authorization)
    resolved = await resolve_session(db, token) if token else None
    if not resolved:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not_signed_in")
    user, workspace = resolved
    onboarding = await onboarding_state(db, workspace["workspace_id"])
    return SessionOut(
        user=UserOut(**user),
        workspace=WorkspaceOut(**workspace),
        onboarding_complete=onboarding["complete"],
        onboarding_step=onboarding["step"],
    )
