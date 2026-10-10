"""Authentication and session management helpers."""

from __future__ import annotations

import secrets
from typing import Any, Optional

import aiosqlite

from edrak.db.connection import hash_password
from edrak.db.repository import (
    create_session,
    create_user,
    create_workspace,
    delete_session,
    get_session_user,
    get_user_by_email,
)


def generate_token() -> str:
    """Generate a secure session token."""
    return secrets.token_urlsafe(32)


async def authenticate_user(
    db: aiosqlite.Connection, email: str, password: str
) -> Optional[dict[str, Any]]:
    user = await get_user_by_email(db, email)
    if not user:
        return None
    if user["password_hash"] != hash_password(password):
        return None
    return user


async def register_user(
    db: aiosqlite.Connection,
    email: str,
    password: str,
    name: Optional[str] = None,
    title: Optional[str] = None,
    workspace_name: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing = await get_user_by_email(db, email)
    if existing:
        raise ValueError("email_taken")

    ws_id = f"ws-{secrets.token_hex(4)}"
    ws_name = workspace_name or f"{name or email.split('@')[0]}'s Workspace"
    workspace = await create_workspace(db, ws_id, ws_name)

    user_id = f"user-{secrets.token_hex(4)}"
    user_name = name or email.split("@")[0].capitalize()
    pwd_hash = hash_password(password)

    user = await create_user(
        db,
        user_id=user_id,
        email=email,
        password_hash=pwd_hash,
        name=user_name,
        title=title,
        workspace_id=ws_id,
    )
    return user, workspace


async def start_session(db: aiosqlite.Connection, user_id: str) -> str:
    token = generate_token()
    await create_session(db, token, user_id)
    return token


async def end_session(db: aiosqlite.Connection, token: str) -> None:
    await delete_session(db, token)


async def resolve_session(
    db: aiosqlite.Connection, token: str
) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    return await get_session_user(db, token)
