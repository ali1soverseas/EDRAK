"""Repair vector-index drift from the durable workspace document chunks."""

import logging
import sqlite3
from typing import Any

from edrak.core.config import settings

logger = logging.getLogger(__name__)


def restore_missing_workspace_chunks(workspace_id: str, collection: Any) -> int:
    """Restore clean indexed chunks missing from Chroma without touching other workspaces."""
    db_path = settings.get_db_path()
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT c.id AS chunk_id, c.content, c.locator, d.id AS document_id,
                   d.name, d.origin, d.is_synthetic
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id AND d.workspace_id = c.workspace_id
            WHERE c.workspace_id = ? AND c.excluded = 0 AND d.status = 'indexed'
            ORDER BY d.id, c.chunk_index
            """,
            (workspace_id,),
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        return 0

    indexed = collection.get(where={"workspace_id": workspace_id}, include=["metadatas"])
    existing_ids = set(indexed.get("ids") or [])
    missing = [row for row in rows if row["chunk_id"] not in existing_ids]
    if not missing:
        return 0

    metadatas = []
    for row in missing:
        metadata = {
            "workspace_id": workspace_id,
            "document_id": row["document_id"],
            "filename": row["name"],
            "origin": row["origin"] or "internal",
            "is_synthetic": "true" if row["is_synthetic"] else "false",
            "source_uri": f"internal://{row['document_id']}/{row['name']}",
            "title": row["name"],
            "doc_type": "internal_doc",
        }
        if row["locator"]:
            metadata["heading"] = row["locator"]
        metadatas.append(metadata)

    collection.upsert(
        ids=[row["chunk_id"] for row in missing],
        documents=[row["content"] for row in missing],
        metadatas=metadatas,
    )
    logger.warning(
        "Restored %d missing vector chunks for workspace %s from the document database",
        len(missing),
        workspace_id,
    )
    return len(missing)
