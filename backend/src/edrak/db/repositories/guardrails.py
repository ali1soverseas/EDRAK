"""guardrail_events: every guardrail decision on a question, a document, web content or a brief."""

from __future__ import annotations

from typing import Any, Iterable, Optional

import aiosqlite

from edrak.db.util import dumps, fetch_all, loads


async def record(
    db: aiosqlite.Connection,
    *,
    workspace_id: str,
    stage: str,
    check_name: str,
    severity: str,
    action: str,
    detail: Optional[dict[str, Any]] = None,
    request_id: Optional[str] = None,
    document_id: Optional[str] = None,
    commit: bool = True,
) -> None:
    await db.execute(
        """
        INSERT INTO guardrail_events (workspace_id, request_id, document_id, stage, check_name, severity, action, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (workspace_id, request_id, document_id, stage, check_name, severity, action, dumps(detail or {})),
    )
    if commit:
        await db.commit()


async def record_checks(
    db: aiosqlite.Connection, *, workspace_id: str, stage: str, checks: Iterable[Any],
    request_id: Optional[str] = None, document_id: Optional[str] = None, commit: bool = True,
) -> None:
    """Record QueryVerdict checks (objects with name/severity/action/detail)."""
    for c in checks:
        await record(
            db, workspace_id=workspace_id, stage=stage, check_name=c.name, severity=c.severity, action=c.action,
            detail=c.detail, request_id=request_id, document_id=document_id, commit=False,
        )
    if commit:
        await db.commit()


async def list_events(
    db: aiosqlite.Connection, workspace_id: str, *, request_id: Optional[str] = None,
    document_id: Optional[str] = None, limit: int = 200,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM guardrail_events WHERE workspace_id = ?"
    params: list[Any] = [workspace_id]
    if request_id:
        sql += " AND request_id = ?"
        params.append(request_id)
    if document_id:
        sql += " AND document_id = ?"
        params.append(document_id)
    rows = await fetch_all(db, sql + " ORDER BY created_at DESC LIMIT ?", [*params, limit])
    for r in rows:
        r["detail"] = loads(r["detail"], {})
    return rows
