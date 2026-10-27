"""Sign in, account creation and sessions. Email and password only; one user per workspace."""

from __future__ import annotations

import re
from typing import Any, Optional

import aiosqlite

from edrak.core.security import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    new_session_token,
    verify_password,
)
from edrak.db.repositories import auth as auth_repo

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    """Carries an API error code such as email_taken or weak_password."""


async def authenticate_user(db: aiosqlite.Connection, email: str, password: str) -> Optional[dict[str, Any]]:
    user = await auth_repo.get_user_by_email(db, email)
    if not user or not verify_password(user["password_hash"], password):
        return None
    if needs_rehash(user["password_hash"]):
        await auth_repo.update_password_hash(db, user["id"], hash_password(password))
    return user


async def register_user(
    db: aiosqlite.Connection,
    email: str,
    password: str,
    name: Optional[str] = None,
    title: Optional[str] = None,
    workspace_name: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email):
        raise AuthError("invalid_email")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError("weak_password")
    if await auth_repo.get_user_by_email(db, email):
        raise AuthError("email_taken")
    full_name = (name or "").strip() or email.split("@")[0].replace(".", " ").title()
    ws_name = (workspace_name or "").strip() or f"{full_name.split(' ')[0]}'s workspace"
    return await auth_repo.create_account(
        db,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        job_title=(title or "").strip() or None,
        workspace_name=ws_name,
    )


async def start_session(db: aiosqlite.Connection, user_id: str, user_agent: Optional[str] = None) -> str:
    token = new_session_token()
    await auth_repo.create_session(db, token, user_id, user_agent)
    return token


async def end_session(db: aiosqlite.Connection, token: str) -> None:
    await auth_repo.delete_session(db, token)


async def resolve_session(
    db: aiosqlite.Connection, token: str, *, touch: bool = True
) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    return await auth_repo.resolve_session(db, token, touch=touch)
