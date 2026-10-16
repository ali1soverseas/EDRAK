"""In-app notifications (the bell). Email delivery is not configured; preferences are respected."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from edrak.db.util import fetch_all, fetch_one, fetch_value, new_id, now_iso

# notification kind -> notification_preferences event that controls it
KIND_TO_EVENT = {
    "plan_awaiting_approval": "plan_awaiting_approval",
    "run_awaiting_review": "run_awaiting_review",
    "run_finished": "run_finished",
    "brief_ready": "run_finished",
    "alert": "high_importance_alert",
    "digest_ready": "weekly_digest",
}


async def notify(
    db: aiosqlite.Connection,
    *,
    workspace_id: str,
    kind: str,
    title: str,
    body: Optional[str] = None,
    request_id: Optional[str] = None,
    brief_id: Optional[str] = None,
    alert_id: Optional[str] = None,
    document_id: Optional[str] = None,
    commit: bool = True,
) -> Optional[str]:
    """Notify the workspace's owner, unless they turned in-app notifications for this off."""
    owner = await fetch_value(db, "SELECT owner_id FROM workspaces WHERE id = ?", (workspace_id,))
    if not owner:
        return None
    event = KIND_TO_EVENT.get(kind)
    if event:
        pref = await fetch_one(
            db,
            "SELECT in_app FROM notification_preferences WHERE user_id = ? AND workspace_id = ? AND event = ?",
            (owner, workspace_id, event),
        )
        if pref is not None and not pref["in_app"]:
            return None
    notification_id = new_id()
    await db.execute(
        """
        INSERT INTO notifications (id, workspace_id, user_id, kind, title, body, request_id, brief_id, alert_id, document_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (notification_id, workspace_id, owner, kind, title, body, request_id, brief_id, alert_id, document_id),
    )
    if commit:
        await db.commit()
    return notification_id


async def list_notifications(
    db: aiosqlite.Connection, user_id: str, workspace_id: str, *, unread_only: bool = False, limit: int = 50
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM notifications WHERE user_id = ? AND workspace_id = ?"
    if unread_only:
        sql += " AND read_at IS NULL"
    return await fetch_all(db, sql + " ORDER BY created_at DESC LIMIT ?", (user_id, workspace_id, limit))


async def unread_count(db: aiosqlite.Connection, user_id: str, workspace_id: str) -> int:
    return int(await fetch_value(
        db, "SELECT count(*) FROM notifications WHERE user_id = ? AND workspace_id = ? AND read_at IS NULL",
        (user_id, workspace_id),
    ) or 0)


async def mark_read(db: aiosqlite.Connection, user_id: str, workspace_id: str, notification_id: Optional[str] = None) -> None:
    if notification_id:
        await db.execute(
            "UPDATE notifications SET read_at = coalesce(read_at, ?) WHERE id = ? AND user_id = ? AND workspace_id = ?",
            (now_iso(), notification_id, user_id, workspace_id),
        )
    else:
        await db.execute(
            "UPDATE notifications SET read_at = ? WHERE user_id = ? AND workspace_id = ? AND read_at IS NULL",
            (now_iso(), user_id, workspace_id),
        )
    await db.commit()
