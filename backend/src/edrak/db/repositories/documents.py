"""Knowledge base documents and their chunks."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id, now_iso


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    row["guardrail_notes"] = loads(row.get("guardrail_notes"), [])
    return row


async def create_document(
    db: aiosqlite.Connection,
    *,
    workspace_id: str,
    name: str,
    storage_uri: str,
    mime_type: Optional[str],
    size_bytes: Optional[int],
    content_hash: Optional[str],
    uploaded_by: Optional[str],
    kind: str = "document",
    origin: str = "upload",
    status: str = "uploaded",
    is_synthetic: bool = False,
    document_id: Optional[str] = None,
    commit: bool = True,
) -> dict[str, Any]:
    document_id = document_id or new_id()
    await db.execute(
        """
        INSERT INTO documents (id, workspace_id, name, kind, mime_type, size_bytes, storage_uri, content_hash,
                               origin, status, index_progress, is_synthetic, uploaded_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (document_id, workspace_id, name, kind, mime_type, size_bytes, storage_uri, content_hash,
         origin, status, 1 if is_synthetic else 0, uploaded_by),
    )
    if commit:
        await db.commit()
    doc = await get_document(db, workspace_id, document_id)
    assert doc is not None
    return doc


async def get_document(db: aiosqlite.Connection, workspace_id: str, document_id: str) -> Optional[dict[str, Any]]:
    row = await fetch_one(db, "SELECT * FROM documents WHERE id = ? AND workspace_id = ?", (document_id, workspace_id))
    return _shape(row) if row else None


async def find_by_hash(db: aiosqlite.Connection, workspace_id: str, content_hash: str) -> Optional[dict[str, Any]]:
    row = await fetch_one(
        db, "SELECT * FROM documents WHERE workspace_id = ? AND content_hash = ?", (workspace_id, content_hash)
    )
    return _shape(row) if row else None


async def list_documents(db: aiosqlite.Connection, workspace_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(
        db,
        """
        SELECT d.*, u.full_name AS uploaded_by_name,
               (SELECT count(*) FROM analysis_documents ad WHERE ad.document_id = d.id) AS used_in_runs,
               (SELECT count(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunk_count
        FROM documents d LEFT JOIN users u ON u.id = d.uploaded_by
        WHERE d.workspace_id = ?
        ORDER BY d.created_at DESC
        """,
        (workspace_id,),
    )
    return [_shape(r) for r in rows]


async def update_document(db: aiosqlite.Connection, document_id: str, commit: bool = True, **fields: Any) -> None:
    allowed = {
        "status", "index_progress", "error_message", "page_count", "row_count", "kind", "indexed_at",
        "guardrail_status", "guardrail_notes", "retrievability", "mime_type",
    }
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"cannot update documents.{key}")
        if key == "guardrail_notes":
            value = dumps(value)
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(document_id)
    await db.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id = ?", params)
    if commit:
        await db.commit()


async def delete_document(db: aiosqlite.Connection, workspace_id: str, document_id: str) -> bool:
    cursor = await db.execute("DELETE FROM documents WHERE id = ? AND workspace_id = ?", (document_id, workspace_id))
    await db.commit()
    return cursor.rowcount > 0


async def replace_chunks(
    db: aiosqlite.Connection,
    *,
    workspace_id: str,
    document_id: str,
    chunks: list[dict[str, Any]],
    embedding_model: Optional[str],
) -> None:
    """Mirror the chunks written to Chroma. Each chunk: id, content, locator, flags, excluded."""
    await db.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
    for index, chunk in enumerate(chunks):
        await db.execute(
            """
            INSERT OR REPLACE INTO document_chunks
              (id, workspace_id, document_id, chunk_index, content, locator, token_count, embedding_model, flags, excluded)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk["id"], workspace_id, document_id, index, chunk["content"], chunk.get("locator"),
                len(chunk["content"].split()), embedding_model, dumps(chunk.get("flags") or []),
                1 if chunk.get("excluded") else 0,
            ),
        )
    await db.commit()


async def list_chunks(db: aiosqlite.Connection, workspace_id: str, document_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(
        db,
        """
        SELECT id, chunk_index, content, locator, token_count, flags, excluded
        FROM document_chunks WHERE workspace_id = ? AND document_id = ? ORDER BY chunk_index
        """,
        (workspace_id, document_id),
    )
    for r in rows:
        r["flags"] = loads(r["flags"], [])
        r["excluded"] = bool(r["excluded"])
    return rows


async def excluded_chunk_ids(db: aiosqlite.Connection, workspace_id: str) -> set[str]:
    rows = await fetch_all(
        db, "SELECT id FROM document_chunks WHERE workspace_id = ? AND excluded = 1", (workspace_id,)
    )
    return {r["id"] for r in rows}


async def mark_indexing_failed_on_restart(db: aiosqlite.Connection) -> int:
    """Files that were mid-indexing when the server stopped will not finish by themselves."""
    cursor = await db.execute(
        "UPDATE documents SET status = 'failed', error_message = 'Indexing was interrupted. Retry to index it again.' "
        "WHERE status IN ('uploaded','indexing')"
    )
    await db.commit()
    return cursor.rowcount


def size_label(doc: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The size the Sources table shows: pages, rows, or kilobytes."""
    if doc.get("page_count"):
        return {"unit": "pages", "value": doc["page_count"]}
    if doc.get("row_count"):
        return {"unit": "rows", "value": doc["row_count"]}
    if doc.get("size_bytes") is not None:
        return {"unit": "kb", "value": round(doc["size_bytes"] / 1024, 1)}
    return None


__all__ = [
    "create_document", "get_document", "find_by_hash", "list_documents", "update_document", "delete_document",
    "replace_chunks", "list_chunks", "excluded_chunk_ids", "mark_indexing_failed_on_restart", "size_label", "now_iso",
]
