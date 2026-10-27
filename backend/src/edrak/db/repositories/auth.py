"""Users, their one workspace, sessions and default settings."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

import aiosqlite

from edrak.core.config import settings
from edrak.core.security import hash_token
from edrak.db.util import fetch_one, new_id, now_iso, slugify

NOTIFICATION_EVENTS = (
    "plan_awaiting_approval",
    "run_awaiting_review",
    "run_finished",
    "high_importance_alert",
    "weekly_digest",
)


async def get_user_by_email(db: aiosqlite.Connection, email: str) -> Optional[dict[str, Any]]:
    return await fetch_one(db, "SELECT * FROM users WHERE lower(email) = lower(?)", (email.strip(),))


async def get_user(db: aiosqlite.Connection, user_id: str) -> Optional[dict[str, Any]]:
    return await fetch_one(db, "SELECT * FROM users WHERE id = ?", (user_id,))


async def get_workspace_for_user(db: aiosqlite.Connection, user_id: str) -> Optional[dict[str, Any]]:
    return await fetch_one(db, "SELECT * FROM workspaces WHERE owner_id = ?", (user_id,))


async def get_workspace(db: aiosqlite.Connection, workspace_id: str) -> Optional[dict[str, Any]]:
    return await fetch_one(db, "SELECT * FROM workspaces WHERE id = ?", (workspace_id,))


async def create_account(
    db: aiosqlite.Connection,
    *,
    email: str,
    password_hash: str,
    full_name: str,
    job_title: Optional[str],
    workspace_name: str,
    user_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    commit: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A user and their workspace, with every per-workspace row the app expects to exist."""
    user_id = user_id or new_id()
    workspace_id = workspace_id or new_id()
    await db.execute(
        "INSERT INTO users (id, email, full_name, job_title, password_hash) VALUES (?, ?, ?, ?, ?)",
        (user_id, email.strip().lower(), full_name, job_title, password_hash),
    )
    await db.execute(
        "INSERT INTO workspaces (id, owner_id, name, slug) VALUES (?, ?, ?, ?)",
        (workspace_id, user_id, workspace_name, slugify(workspace_name)),
    )
    await ensure_workspace_defaults(db, user_id, workspace_id)
    if commit:
        await db.commit()
    user = await get_user(db, user_id)
    workspace = await get_workspace(db, workspace_id)
    assert user is not None and workspace is not None
    return user, workspace


async def ensure_workspace_defaults(db: aiosqlite.Connection, user_id: str, workspace_id: str) -> None:
    await db.execute("INSERT OR IGNORE INTO workspace_settings (workspace_id) VALUES (?)", (workspace_id,))
    await db.execute("INSERT OR IGNORE INTO user_preferences (user_id) VALUES (?)", (user_id,))
    await db.execute("INSERT OR IGNORE INTO company_profiles (workspace_id) VALUES (?)", (workspace_id,))
    for event in NOTIFICATION_EVENTS:
        await db.execute(
            "INSERT OR IGNORE INTO notification_preferences (user_id, workspace_id, event) VALUES (?, ?, ?)",
            (user_id, workspace_id, event),
        )
    for source_type in ("web_search", "competitor_sites", "news", "open_data", "social_forums"):
        await db.execute(
            "INSERT OR IGNORE INTO external_source_policies (workspace_id, source_type) VALUES (?, ?)",
            (workspace_id, source_type),
        )


async def update_password_hash(db: aiosqlite.Connection, user_id: str, password_hash: str) -> None:
    await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    await db.commit()


async def update_user(db: aiosqlite.Connection, user_id: str, *, full_name: Optional[str] = None,
                      job_title: Optional[str] = None) -> None:
    if full_name is not None:
        await db.execute("UPDATE users SET full_name = ? WHERE id = ?", (full_name.strip(), user_id))
    if job_title is not None:
        await db.execute("UPDATE users SET job_title = ? WHERE id = ?", (job_title.strip() or None, user_id))
    await db.commit()


async def rename_workspace(db: aiosqlite.Connection, workspace_id: str, name: str) -> None:
    await db.execute("UPDATE workspaces SET name = ? WHERE id = ?", (name.strip(), workspace_id))
    await db.commit()


# ---------------------------------------------------------------- sessions


async def create_session(db: aiosqlite.Connection, token: str, user_id: str, user_agent: Optional[str] = None) -> None:
    expires = now_iso(timedelta(days=settings.SESSION_DAYS))
    await db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at, user_agent) VALUES (?, ?, ?, ?)",
        (hash_token(token), user_id, expires, (user_agent or "")[:300] or None),
    )
    await db.execute("UPDATE users SET last_sign_in_at = ? WHERE id = ?", (now_iso(), user_id))
    await db.commit()


async def resolve_session(
    db: aiosqlite.Connection, token: str, *, touch: bool = True
) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    """The user and workspace behind a live session, or None when it is unknown or expired."""
    row = await fetch_one(
        db,
        """
        SELECT u.id AS user_id, u.email, u.full_name, u.job_title,
               w.id AS workspace_id, w.name AS workspace_name, s.expires_at, s.last_seen_at
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        JOIN workspaces w ON w.owner_id = u.id
        WHERE s.token_hash = ?
        """,
        (hash_token(token),),
    )
    if not row:
        return None
    if row["expires_at"] <= now_iso():
        await db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))
        await db.commit()
        return None
    # Refresh last_seen_at at most once a minute; every request would otherwise write.
    if touch and row["last_seen_at"] <= now_iso(timedelta(minutes=-1)):
        await db.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now_iso(), hash_token(token)))
        await db.commit()
    user = {"user_id": row["user_id"], "email": row["email"], "name": row["full_name"], "title": row["job_title"]}
    workspace = {"workspace_id": row["workspace_id"], "name": row["workspace_name"]}
    return user, workspace


async def delete_session(db: aiosqlite.Connection, token: str) -> None:
    await db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))
    await db.commit()


async def purge_expired_sessions(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
    await db.commit()
    return cursor.rowcount


async def onboarding_state(db: aiosqlite.Connection, workspace_id: str) -> dict[str, Any]:
    row = await fetch_one(
        db, "SELECT onboarding_step, completed_at FROM company_profiles WHERE workspace_id = ?", (workspace_id,)
    )
    if not row:
        return {"complete": False, "step": 1}
    return {"complete": row["completed_at"] is not None, "step": int(row["onboarding_step"] or 1)}
