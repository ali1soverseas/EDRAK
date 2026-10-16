"""Append-only audit trail: who approved, rejected, reviewed, decided or flagged what."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from edrak.db.util import dumps


async def record(
    db: aiosqlite.Connection,
    *,
    workspace_id: str,
    action: str,
    target_type: str,
    target_id: Optional[str],
    actor_id: Optional[str] = None,
    actor_kind: str = "user",
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> None:
    await db.execute(
        """
        INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, target_type, target_id, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (workspace_id, actor_kind, actor_id, action, target_type, target_id, dumps(metadata or {})),
    )
    if commit:
        await db.commit()
