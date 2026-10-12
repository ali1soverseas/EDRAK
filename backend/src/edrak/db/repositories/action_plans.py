"""Action plan versions."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id, now_iso


def _row(r: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not r:
        return None
    out = dict(r)
    out["document"] = loads(out.get("document"), {})
    return out


async def start(db: aiosqlite.Connection, *, brief_id: str, workspace_id: str, option_key: str, user_id: Optional[str],
                note: Optional[str] = None, supersedes: Optional[dict[str, Any]] = None) -> str:
    """A new version in 'generating'. The previous draft of this option is superseded."""
    plan_id = new_id()
    top = await fetch_one(db, "SELECT MAX(version) AS v FROM action_plans WHERE brief_id = ? AND option_key = ?", (brief_id, option_key))
    version = int((top or {}).get("v") or 0) + 1  # editing an older approved version still gets the next number
    if supersedes and supersedes["status"] == "draft":
        await db.execute("UPDATE action_plans SET status = 'superseded' WHERE id = ?", (supersedes["id"],))
    await db.execute(
        "INSERT INTO action_plans (id, brief_id, workspace_id, option_key, version, status, note, created_by, supersedes_id)"
        " VALUES (?,?,?,?,?, 'generating', ?,?,?)",
        (plan_id, brief_id, workspace_id, option_key, version, note, user_id, supersedes["id"] if supersedes else None))
    await db.commit()
    return plan_id


async def finish(db: aiosqlite.Connection, plan_id: str, document: dict[str, Any]) -> None:
    await db.execute("UPDATE action_plans SET status = 'draft', document = ? WHERE id = ?", (dumps(document), plan_id))
    await db.commit()


async def fail(db: aiosqlite.Connection, plan_id: str, error: str) -> None:
    await db.execute("UPDATE action_plans SET status = 'failed', error = ? WHERE id = ?", (error[:500], plan_id))
    await db.commit()


async def save_version(db: aiosqlite.Connection, prev: dict[str, Any], document: dict[str, Any], user_id: Optional[str], note: Optional[str] = None) -> str:
    """An edit: a new draft version holding the person's document; the previous draft is superseded."""
    plan_id = await start(db, brief_id=prev["brief_id"], workspace_id=prev["workspace_id"], option_key=prev["option_key"],
                          user_id=user_id, note=note, supersedes=prev)
    await finish(db, plan_id, document)
    return plan_id


async def get(db: aiosqlite.Connection, plan_id: str, workspace_id: str) -> Optional[dict[str, Any]]:
    return _row(await fetch_one(db, "SELECT * FROM action_plans WHERE id = ? AND workspace_id = ?", (plan_id, workspace_id)))


async def current(db: aiosqlite.Connection, brief_id: str, option_key: str) -> Optional[dict[str, Any]]:
    """The newest version that is not superseded."""
    return _row(await fetch_one(
        db, "SELECT * FROM action_plans WHERE brief_id = ? AND option_key = ? AND status <> 'superseded' ORDER BY version DESC LIMIT 1",
        (brief_id, option_key)))


async def for_brief(db: aiosqlite.Connection, brief_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(db, "SELECT * FROM action_plans WHERE brief_id = ? ORDER BY option_key, version", (brief_id,))
    return [_row(r) for r in rows]  # type: ignore[misc]


async def approve(db: aiosqlite.Connection, plan_id: str, user_id: str, *, commit: bool = True) -> None:
    await db.execute("UPDATE action_plans SET status = 'approved', approved_by = ?, approved_at = ? WHERE id = ?", (user_id, now_iso(), plan_id))
    if commit:
        await db.commit()


async def supersede_approved(db: aiosqlite.Connection, brief_id: str) -> None:
    await db.execute("UPDATE action_plans SET status = 'superseded' WHERE brief_id = ? AND status = 'approved'", (brief_id,))
