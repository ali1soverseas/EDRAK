"""Briefs: frozen documents, plus the columns lists query."""

from __future__ import annotations

from typing import Any, Callable, Optional

import aiosqlite

from edrak.db.util import dumps, fetch_all, fetch_one, fetch_value, loads, new_id

DOCUMENT_VERSION = 2


async def reserve_brief_number(db: aiosqlite.Connection, workspace_id: str) -> int:
    """Take the next brief number of a workspace. The caller writes the brief in the same transaction."""
    await db.execute(
        """
        INSERT INTO workspace_counters (workspace_id, key, value) VALUES (?, 'brief', 1)
        ON CONFLICT (workspace_id, key) DO UPDATE SET value = workspace_counters.value + 1
        """,
        (workspace_id,),
    )
    return int(await fetch_value(
        db, "SELECT value FROM workspace_counters WHERE workspace_id = ? AND key = 'brief'", (workspace_id,)
    ))


async def save_brief(
    db: aiosqlite.Connection,
    *,
    workspace_id: str,
    request_id: str,
    requested_by: Optional[str],
    build: Callable[[str, int], dict[str, Any]],
    brief_id: Optional[str] = None,
    number: Optional[int] = None,
    document_version: int = DOCUMENT_VERSION,
) -> dict[str, Any]:
    """Number and store a brief. `build(brief_id, number)` returns the document."""
    brief_id = brief_id or new_id()
    if number is None:
        number = await reserve_brief_number(db, workspace_id)
    document = build(brief_id, number)
    evidence_status = document.get("evidence_status") or ("partial" if document.get("partial") else "verified")
    if evidence_status not in ("verified", "partial", "insufficient", "failed"):
        evidence_status = "partial"
    summary = document.get("summary_text") or _summary_text(document.get("summary"))
    await db.execute(
        """
        INSERT INTO briefs (id, workspace_id, request_id, number, title, use_case, language, summary, evidence_status,
                            requested_by, source_count, worker_count, document, document_version, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, coalesce(?, strftime('%Y-%m-%dT%H:%M:%fZ','now')))
        """,
        (
            brief_id, workspace_id, request_id, number, document.get("title") or "Brief",
            document.get("use_case") or "competitive_intelligence", document.get("language") or "en", summary,
            evidence_status, requested_by, len(document.get("evidence") or []),
            len(document.get("lenses") or []), dumps(document), document_version, document.get("created_at"),
        ),
    )
    await db.commit()
    return {"id": brief_id, "number": number, "document": document}


def _summary_text(summary: Any) -> Optional[str]:
    """Flatten the brief's rich summary segments to plain text for previews."""
    if not isinstance(summary, dict):
        return None
    found = summary.get("found")
    if isinstance(found, list):
        return "".join(seg.get("text", "") for seg in found if isinstance(seg, dict)).strip() or None
    if isinstance(found, str):
        return found
    return None


async def get_brief(db: aiosqlite.Connection, brief_id: str, workspace_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    sql = "SELECT * FROM briefs WHERE id = ?"
    params: list[Any] = [brief_id]
    if workspace_id is not None:
        sql += " AND workspace_id = ?"
        params.append(workspace_id)
    row = await fetch_one(db, sql, params)
    if row:
        row["document"] = loads(row["document"], {})
    return row


async def get_brief_for_request(db: aiosqlite.Connection, request_id: str) -> Optional[dict[str, Any]]:
    row = await fetch_one(db, "SELECT * FROM briefs WHERE request_id = ?", (request_id,))
    if row:
        row["document"] = loads(row["document"], {})
    return row


async def list_briefs(db: aiosqlite.Connection, workspace_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        db,
        """
        SELECT b.id, b.request_id, b.number, b.title, b.use_case, b.language, b.summary, b.evidence_status,
               b.lifecycle, b.source_count, b.worker_count, b.generated_at, u.full_name AS requested_by_name,
               r.monitor_id, r.launched_by,
               (SELECT count(*) FROM information_gaps g WHERE g.request_id = b.request_id AND g.status = 'open') AS open_gaps
        FROM briefs b
        JOIN business_requests r ON r.request_id = b.request_id
        LEFT JOIN users u ON u.id = b.requested_by
        WHERE b.workspace_id = ?
        ORDER BY b.generated_at DESC
        """,
        (workspace_id,),
    )


async def set_lifecycle(db: aiosqlite.Connection, brief_id: str, lifecycle: str) -> None:
    await db.execute("UPDATE briefs SET lifecycle = ? WHERE id = ?", (lifecycle, brief_id))
    await db.commit()
