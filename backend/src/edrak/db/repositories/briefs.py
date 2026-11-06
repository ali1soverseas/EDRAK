"""Briefs: frozen documents, plus the columns lists query."""

from __future__ import annotations

from typing import Any, Callable, Optional

import aiosqlite

from edrak.db.util import dumps, fetch_all, fetch_one, fetch_value, loads, new_id, now_iso

DOCUMENT_VERSION = 2  # briefs without typed metrics
TYPED_DOCUMENT_VERSION = 3


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
    document_version: Optional[int] = None,
) -> dict[str, Any]:
    """Number and store a brief. `build(brief_id, number)` returns the document."""
    brief_id = brief_id or new_id()
    if number is None:
        number = await reserve_brief_number(db, workspace_id)
    document = build(brief_id, number)
    document_version = document_version or (TYPED_DOCUMENT_VERSION if document.get("cards") is not None else DOCUMENT_VERSION)
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
    return await _read_brief(db, "id", brief_id, workspace_id)


async def get_brief_version(
    db: aiosqlite.Connection,
    brief_id: str,
    version: int,
    workspace_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    return await _read_brief(db, "id", brief_id, workspace_id, version)


async def _read_brief(
    db: aiosqlite.Connection,
    key: str,
    value: str,
    workspace_id: Optional[str] = None,
    version: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    if key not in {"id", "request_id"}:
        raise ValueError(f"unsupported brief lookup key: {key}")
    sql = "SELECT * FROM briefs WHERE id = ?"
    if key == "request_id":
        sql = "SELECT * FROM briefs WHERE request_id = ?"
    params: list[Any] = [value]
    if workspace_id is not None:
        sql += " AND workspace_id = ?"
        params.append(workspace_id)
    row = await fetch_one(db, sql, params)
    if not row:
        return None

    original_document = loads(row["document"], {})
    revision = None
    if version is not None and version > 1:
        revision = await fetch_one(
            db,
            "SELECT * FROM brief_revisions WHERE brief_id = ? AND version = ?",
            (row["id"], version),
        )
        if revision is None:
            return None
    elif version is None:
        revision = await fetch_one(
            db,
            "SELECT * FROM brief_revisions WHERE brief_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (row["id"],),
        )

    if revision:
        row["document"] = loads(revision["document"], {})
        row["summary"] = revision["summary"]
        row["evidence_status"] = revision["evidence_status"]
        row["document_version"] = revision["document_version"]
        row["revision"] = {
            "version": revision["version"],
            "reason": revision["reason"],
            "created_at": revision["created_at"],
        }
    else:
        row["document"] = original_document
        row["revision"] = {
            "version": 1,
            "reason": "Original brief",
            "created_at": row["generated_at"],
        }
    latest = await fetch_value(
        db,
        "SELECT coalesce(max(version), 1) FROM brief_revisions WHERE brief_id = ?",
        (row["id"],),
    )
    row["latest_revision_version"] = int(latest)
    return row


async def get_brief_for_request(
    db: aiosqlite.Connection, request_id: str
) -> Optional[dict[str, Any]]:
    return await _read_brief(db, "request_id", request_id)


async def save_revision(
    db: aiosqlite.Connection,
    brief_id: str,
    document: dict[str, Any],
    *,
    reason: str,
    commit: bool = True,
) -> dict[str, Any]:
    """Append a new immutable snapshot while retaining the original brief."""
    brief = await fetch_one(
        db, "SELECT id, document_version FROM briefs WHERE id = ?", (brief_id,)
    )
    if brief is None:
        raise ValueError("brief_not_found")
    next_version = await fetch_value(
        db,
        "SELECT coalesce(max(version), 1) + 1 FROM brief_revisions WHERE brief_id = ?",
        (brief_id,),
    )
    version = int(next_version)
    revision_document = dict(document)
    evidence_status = revision_document.get("evidence_status") or (
        "partial" if revision_document.get("partial") else "verified"
    )
    if evidence_status not in ("verified", "partial", "insufficient", "failed"):
        evidence_status = "partial"
    fallback_version = (
        TYPED_DOCUMENT_VERSION
        if revision_document.get("cards") is not None
        else DOCUMENT_VERSION
    )
    document_version = int(
        revision_document.get("document_version")
        or brief["document_version"]
        or fallback_version
    )
    created_at = revision_document.get("created_at") or now_iso()
    revision_document["created_at"] = created_at
    revision_document["revision"] = {
        "version": version,
        "reason": reason,
        "created_at": created_at,
    }
    await db.execute(
        """
        INSERT INTO brief_revisions
          (brief_id, version, reason, summary, evidence_status, document, document_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, coalesce(?, strftime('%Y-%m-%dT%H:%M:%fZ','now')))
        """,
        (
            brief_id,
            version,
            reason,
            revision_document.get("summary_text")
            or _summary_text(revision_document.get("summary")),
            evidence_status,
            dumps(revision_document),
            document_version,
            created_at,
        ),
    )
    if commit:
        await db.commit()
    return {
        "brief_id": brief_id,
        "version": version,
        "reason": reason,
        "document": revision_document,
    }


async def list_revisions(
    db: aiosqlite.Connection, brief_id: str
) -> list[dict[str, Any]]:
    brief = await fetch_one(
        db,
        "SELECT id, number, generated_at, summary, evidence_status, document_version "
        "FROM briefs WHERE id = ?",
        (brief_id,),
    )
    if brief is None:
        return []
    revisions = await fetch_all(
        db,
        "SELECT version, reason, created_at, summary, evidence_status, document_version "
        "FROM brief_revisions WHERE brief_id = ? ORDER BY version",
        (brief_id,),
    )
    return [{
        "version": 1,
        "reason": "Original brief",
        "created_at": brief["generated_at"],
        "summary": brief["summary"],
        "evidence_status": brief["evidence_status"],
        "document_version": brief["document_version"],
    }, *revisions]


async def list_briefs(db: aiosqlite.Connection, workspace_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        db,
        """
        SELECT b.id, b.request_id, b.number, b.title, b.use_case, b.language,
               coalesce(rv.summary, b.summary) AS summary,
               coalesce(rv.evidence_status, b.evidence_status) AS evidence_status,
               b.lifecycle, b.source_count, b.worker_count, coalesce(rv.created_at, b.generated_at) AS generated_at,
               coalesce(rv.version, 1) AS revision_version,
               u.full_name AS requested_by_name,
               r.monitor_id, r.launched_by,
               (SELECT count(*) FROM information_gaps g WHERE g.request_id = b.request_id AND g.status = 'open') AS open_gaps
        FROM briefs b
        JOIN business_requests r ON r.request_id = b.request_id
        LEFT JOIN users u ON u.id = b.requested_by
        LEFT JOIN brief_revisions rv ON rv.brief_id = b.id
          AND rv.version = (SELECT max(latest.version) FROM brief_revisions latest WHERE latest.brief_id = b.id)
        WHERE b.workspace_id = ?
        ORDER BY coalesce(rv.created_at, b.generated_at) DESC
        """,
        (workspace_id,),
    )


async def set_lifecycle(db: aiosqlite.Connection, brief_id: str, lifecycle: str) -> None:
    await db.execute("UPDATE briefs SET lifecycle = ? WHERE id = ?", (lifecycle, brief_id))
    await db.commit()
