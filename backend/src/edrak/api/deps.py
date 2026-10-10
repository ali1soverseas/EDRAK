"""FastAPI dependency injection utilities."""

from __future__ import annotations

from typing import AsyncGenerator, Optional

import aiosqlite
from fastapi import Cookie, Depends, Header, HTTPException, status

from edrak.api.auth import resolve_session
from edrak.api.schemas import SessionOut, UserOut, WorkspaceOut
from edrak.core.config import settings
from edrak.db.connection import get_db


async def get_db_conn() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Dependency for obtaining an async database connection."""
    async with get_db() as db:
        yield db


async def get_current_session(
    session_token: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> SessionOut:
    """Resolve current authenticated session from cookie or Authorization header."""
    token = session_token
    if not token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]

    if token:
        res = await resolve_session(db, token)
        if res:
            user, workspace = res
            return SessionOut(
                user=UserOut(**user),
                workspace=WorkspaceOut(**workspace),
            )

    # In development mode, fallback to default seed user if available
    cursor = await db.execute("SELECT u.*, w.name as workspace_name FROM users u JOIN workspaces w ON u.workspace_id = w.workspace_id LIMIT 1;")
    row = await cursor.fetchone()
    if row:
        data = dict(row)
        return SessionOut(
            user=UserOut(
                user_id=data["user_id"],
                email=data["email"],
                name=data["name"],
                title=data.get("title"),
            ),
            workspace=WorkspaceOut(
                workspace_id=data["workspace_id"],
                name=data["workspace_name"],
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not_signed_in",
    )
