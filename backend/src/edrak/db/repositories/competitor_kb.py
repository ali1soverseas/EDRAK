"""Competitor knowledge base: shared profiles, per-workspace build jobs and the chunk mirror."""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

import aiosqlite

from edrak.db.util import fetch_all, fetch_one

ACTIVE = ("queued", "building")


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) and value else default
    except ValueError:
        return default


# ------------------------------------------------------------------ jobs (per workspace)


async def upsert_job(db: aiosqlite.Connection, workspace_id: str, entity_id: str, *, status: str = "queued",
                     stage: str = "queued", progress: int = 0, kb_key: Optional[str] = None,
                     error_message: Optional[str] = None) -> None:
    await db.execute(
        """
        INSERT INTO competitor_kb_jobs (workspace_id, entity_id, kb_key, status, stage, progress, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (workspace_id, entity_id) DO UPDATE SET
          kb_key = COALESCE(excluded.kb_key, competitor_kb_jobs.kb_key), status = excluded.status,
          stage = excluded.stage, progress = excluded.progress, error_message = excluded.error_message,
          updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        (workspace_id, entity_id, kb_key, status, stage, progress, error_message),
    )


async def update_job(db: aiosqlite.Connection, workspace_id: str, entity_id: str, **fields: Any) -> None:
    allowed = {"kb_key", "status", "stage", "progress", "error_message"}
    sets = [f"{k} = ?" for k in fields if k in allowed]
    if not sets:
        return
    params = [v for k, v in fields.items() if k in allowed]
    await db.execute(
        f"UPDATE competitor_kb_jobs SET {', '.join(sets)}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE workspace_id = ? AND entity_id = ?",
        (*params, workspace_id, entity_id),
    )
    await db.commit()


async def get_job(db: aiosqlite.Connection, workspace_id: str, entity_id: str) -> Optional[dict[str, Any]]:
    return await fetch_one(
        db, "SELECT * FROM competitor_kb_jobs WHERE workspace_id = ? AND entity_id = ?", (workspace_id, entity_id)
    )


async def list_status(db: aiosqlite.Connection, workspace_id: str,
                      entity_ids: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
    """Every tracked competitor of the workspace with its knowledge-base state. No job yet = 'missing'."""
    rows = await fetch_all(
        db,
        """
        SELECT e.id AS entity_id, e.name, e.website_domain,
               COALESCE(j.status, 'missing') AS status, j.stage, COALESCE(j.progress, 0) AS progress,
               j.error_message, j.updated_at, k.kb_key, k.page_count, k.chunk_count, k.built_at
        FROM entities e
        LEFT JOIN competitor_kb_jobs j ON j.entity_id = e.id AND j.workspace_id = e.workspace_id
        LEFT JOIN competitor_kb k ON k.kb_key = j.kb_key
        WHERE e.workspace_id = ? AND e.kind = 'competitor' AND e.is_tracked = 1
        ORDER BY e.created_at
        """,
        (workspace_id,),
    )
    wanted = set(entity_ids) if entity_ids else None
    out = []
    for r in rows:
        if wanted is not None and r["entity_id"] not in wanted:
            continue
        r["page_count"] = r["page_count"] or 0
        r["chunk_count"] = r["chunk_count"] or 0
        out.append(r)
    return out


async def interrupted_jobs(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Jobs that were queued or building when the server stopped. They are queued again on start."""
    return await fetch_all(
        db, "SELECT workspace_id, entity_id FROM competitor_kb_jobs WHERE status IN ('queued','building')"
    )


# ------------------------------------------------------------------ shared profiles


async def get_kb(db: aiosqlite.Connection, kb_key: str) -> Optional[dict[str, Any]]:
    row = await fetch_one(db, "SELECT * FROM competitor_kb WHERE kb_key = ?", (kb_key,))
    if row:
        row["markets"] = _loads(row["markets"], [])
        row["profile"] = _loads(row["profile"], {})
    return row


async def save_kb(db: aiosqlite.Connection, kb_key: str, *, name: str, website_domain: Optional[str],
                  markets: list[str], status: str, profile: Optional[dict[str, Any]] = None,
                  page_count: Optional[int] = None, chunk_count: Optional[int] = None,
                  error_message: Optional[str] = None, built: bool = False) -> None:
    await db.execute(
        """
        INSERT INTO competitor_kb (kb_key, name, website_domain, markets, status, profile, page_count, chunk_count,
                                   error_message, built_at)
        VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, 0), COALESCE(?, 0), ?, CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') END)
        ON CONFLICT (kb_key) DO UPDATE SET
          name = excluded.name,
          website_domain = COALESCE(excluded.website_domain, competitor_kb.website_domain),
          markets = excluded.markets, status = excluded.status,
          profile = CASE WHEN ? IS NULL THEN competitor_kb.profile ELSE excluded.profile END,
          page_count = COALESCE(?, competitor_kb.page_count),
          chunk_count = COALESCE(?, competitor_kb.chunk_count),
          error_message = excluded.error_message,
          built_at = CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE competitor_kb.built_at END,
          updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        (
            kb_key, name, website_domain, json.dumps(markets, ensure_ascii=False), status,
            json.dumps(profile or {}, ensure_ascii=False), page_count, chunk_count, error_message, built,
            None if profile is None else 1, page_count, chunk_count, built,
        ),
    )
    await db.commit()


async def replace_chunks(db: aiosqlite.Connection, kb_key: str, source_url: str, chunks: list[dict[str, Any]]) -> None:
    """Replace one page's chunks in the mirror (the page was read again)."""
    await db.execute("DELETE FROM competitor_kb_chunks WHERE kb_key = ? AND source_url = ?", (kb_key, source_url))
    await db.executemany(
        """
        INSERT OR REPLACE INTO competitor_kb_chunks
          (id, kb_key, source_url, title, source_type, page_type, market, language, chunk_index, content,
           content_hash, fetched_at)
        VALUES (:id, :kb_key, :source_url, :title, :source_type, :page_type, :market, :language, :chunk_index,
                :content, :content_hash, :fetched_at)
        """,
        chunks,
    )


async def count_chunks(db: aiosqlite.Connection, kb_key: str) -> tuple[int, int]:
    row = await fetch_one(
        db, "SELECT count(*) AS chunks, count(DISTINCT source_url) AS pages FROM competitor_kb_chunks WHERE kb_key = ?",
        (kb_key,),
    )
    return (int(row["pages"]), int(row["chunks"])) if row else (0, 0)


async def knowledge_for_entity(db: aiosqlite.Connection, workspace_id: str, entity_id: str) -> Optional[dict[str, Any]]:
    """The profile and source list behind one tracked competitor, for the review screen."""
    job = await get_job(db, workspace_id, entity_id)
    if not job or not job.get("kb_key"):
        return None
    kb = await get_kb(db, job["kb_key"])
    if not kb:
        return None
    sources = await fetch_all(
        db,
        """
        SELECT source_url, max(title) AS title, max(source_type) AS source_type, max(page_type) AS page_type,
               max(fetched_at) AS fetched_at, count(*) AS chunks
        FROM competitor_kb_chunks WHERE kb_key = ? GROUP BY source_url ORDER BY source_type, page_type
        """,
        (job["kb_key"],),
    )
    return {**kb, "sources": sources}


async def list_pages(db: aiosqlite.Connection, kb_key: str) -> list[dict[str, Any]]:
    """Reconstruct saved pages from their chunks, in page and reading order."""
    chunks = await fetch_all(
        db,
        """
        SELECT source_url, title, source_type, page_type, market, language, fetched_at, chunk_index, content
        FROM competitor_kb_chunks
        WHERE kb_key = ?
        ORDER BY source_url, chunk_index
        """,
        (kb_key,),
    )
    pages: list[dict[str, Any]] = []
    for chunk in chunks:
        if not pages or pages[-1]["url"] != chunk["source_url"]:
            pages.append({
                "url": chunk["source_url"],
                "title": chunk["title"] or "",
                "source_type": chunk["source_type"],
                "page_type": chunk["page_type"] or "other",
                "market": chunk["market"] or "any",
                "language": chunk["language"] or "en",
                "fetched_at": chunk["fetched_at"],
                "text": chunk["content"],
            })
        else:
            pages[-1]["fetched_at"] = max(pages[-1]["fetched_at"], chunk["fetched_at"])
            pages[-1]["text"] += f"\n\n{chunk['content']}"
    return pages


async def list_passages(db: aiosqlite.Connection, kb_key: str, source_url: str, limit: int = 60) -> list[dict[str, Any]]:
    """The stored chunks of one page, in reading order."""
    return await fetch_all(
        db,
        """
        SELECT id, chunk_index, content, page_type, source_type, market, language, fetched_at
        FROM competitor_kb_chunks WHERE kb_key = ? AND source_url = ? ORDER BY chunk_index LIMIT ?
        """,
        (kb_key, source_url, limit),
    )


async def kb_key_for_entity(db: aiosqlite.Connection, workspace_id: str, entity_id: str) -> Optional[str]:
    job = await get_job(db, workspace_id, entity_id)
    return job.get("kb_key") if job else None


async def prune_pages(db: aiosqlite.Connection, kb_key: str, keep_urls: Iterable[str], market: Optional[str]) -> list[str]:
    """Remove pages a rebuild no longer keeps. Pages tied to another market stay. Returns the removed URLs."""
    keep = set(keep_urls)
    rows = await fetch_all(
        db, "SELECT DISTINCT source_url, market FROM competitor_kb_chunks WHERE kb_key = ?", (kb_key,)
    )
    gone = [r["source_url"] for r in rows
            if r["source_url"] not in keep and (r["market"] in (None, "", "any") or r["market"] == market)]
    for url in gone:
        await db.execute("DELETE FROM competitor_kb_chunks WHERE kb_key = ? AND source_url = ?", (kb_key, url))
    return gone
