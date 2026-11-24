"""Which approved company facts the knowledge graph already holds, and the hash of what was published."""

from __future__ import annotations

from typing import Mapping, Sequence

import aiosqlite

from edrak.db.util import fetch_all, now_iso


async def list_synced(db: aiosqlite.Connection, workspace_id: str) -> dict[str, str]:
    """fact id -> content hash of every fact published for this workspace."""
    rows = await fetch_all(
        db, "SELECT fact_id, content_hash FROM company_fact_graph_sync WHERE workspace_id = ?", (workspace_id,)
    )
    return {r["fact_id"]: r["content_hash"] for r in rows}


async def mark_synced(
    db: aiosqlite.Connection, workspace_id: str, hashes: Mapping[str, str], *, commit: bool = True
) -> None:
    """Record (or update) the published hash of each fact."""
    if not hashes:
        return
    stamp = now_iso()
    await db.executemany(
        """
        INSERT INTO company_fact_graph_sync (fact_id, workspace_id, content_hash, synced_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(fact_id) DO UPDATE SET content_hash = excluded.content_hash, synced_at = excluded.synced_at
        """,
        [(fact_id, workspace_id, content_hash, stamp) for fact_id, content_hash in hashes.items()],
    )
    if commit:
        await db.commit()


async def clear_synced(
    db: aiosqlite.Connection, workspace_id: str, fact_ids: Sequence[str], *, commit: bool = True
) -> None:
    """Forget facts that were retracted from the graph."""
    if not fact_ids:
        return
    await db.executemany(
        "DELETE FROM company_fact_graph_sync WHERE workspace_id = ? AND fact_id = ?",
        [(workspace_id, fact_id) for fact_id in fact_ids],
    )
    if commit:
        await db.commit()
