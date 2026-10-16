"""Watchlist entities (competitors, markets, topics) and their links."""

from __future__ import annotations

from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import aiosqlite

from edrak.db.util import fetch_all, fetch_one, new_id

ENTITY_KINDS = ("competitor", "market", "topic")
LINK_PLATFORMS = ("website", "facebook", "instagram", "x", "linkedin", "youtube", "tiktok")


def domain_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else (host or None)


async def get_entity(db: aiosqlite.Connection, workspace_id: str, entity_id: str) -> Optional[dict[str, Any]]:
    return await fetch_one(db, "SELECT * FROM entities WHERE id = ? AND workspace_id = ?", (entity_id, workspace_id))


async def find_entity(db: aiosqlite.Connection, workspace_id: str, kind: str, name: str) -> Optional[dict[str, Any]]:
    return await fetch_one(
        db,
        "SELECT * FROM entities WHERE workspace_id = ? AND kind = ? AND lower(name) = lower(?)",
        (workspace_id, kind, name.strip()),
    )


async def upsert_entity(
    db: aiosqlite.Connection,
    workspace_id: str,
    *,
    kind: str,
    name: str,
    created_by: Optional[str] = None,
    website: Optional[str] = None,
    sector: Optional[str] = None,
    country: Optional[str] = None,
    discovered_by: str = "user",
    discovery_reason: Optional[str] = None,
    discovery_confidence: Optional[float] = None,
    track: bool = True,
) -> dict[str, Any]:
    """Find an entity by kind and name, creating it if needed. Never commits."""
    name = name.strip()
    existing = await find_entity(db, workspace_id, kind, name)
    domain = domain_of(website)
    if existing:
        updates: list[str] = []
        params: list[Any] = []
        if domain and not existing.get("website_domain"):
            updates.append("website_domain = ?")
            params.append(domain)
        if track and not existing["is_tracked"]:
            updates.append("is_tracked = 1")
        if sector and not existing.get("sector"):
            updates.append("sector = ?")
            params.append(sector)
        if updates:
            params.append(existing["id"])
            await db.execute(f"UPDATE entities SET {', '.join(updates)} WHERE id = ?", params)
        entity_id = existing["id"]
    else:
        entity_id = new_id()
        await db.execute(
            """
            INSERT INTO entities (id, workspace_id, kind, name, sector, country, website_domain, is_tracked,
                                  discovered_by, discovery_reason, discovery_confidence, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_id, workspace_id, kind, name, sector, country, domain, 1 if track else 0,
             discovered_by, discovery_reason, discovery_confidence, created_by),
        )
    if website:
        await add_entity_link(db, entity_id, "website", website)
    entity = await fetch_one(db, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    assert entity is not None
    return entity


async def add_entity_link(
    db: aiosqlite.Connection,
    entity_id: str,
    platform: str,
    url: str,
    *,
    verified: bool = False,
    found_on_url: Optional[str] = None,
) -> None:
    url = normalise_url(url)
    if not url or platform not in LINK_PLATFORMS:
        return
    await db.execute(
        """
        INSERT INTO entity_links (id, entity_id, platform, url, verified, found_on_url)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (entity_id, platform, lower(url)) DO UPDATE SET
          verified = max(entity_links.verified, excluded.verified),
          found_on_url = coalesce(excluded.found_on_url, entity_links.found_on_url)
        """,
        (new_id(), entity_id, platform, url, 1 if verified else 0, found_on_url),
    )


async def entity_links(db: aiosqlite.Connection, entity_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    ids = list(entity_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = await fetch_all(
        db,
        f"SELECT entity_id, platform, url, verified, found_on_url FROM entity_links WHERE entity_id IN ({marks}) ORDER BY platform",
        ids,
    )
    out: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
    for r in rows:
        out[r["entity_id"]].append(
            {"platform": r["platform"], "url": r["url"], "verified": bool(r["verified"]), "found_on_url": r["found_on_url"]}
        )
    return out


async def list_entities(
    db: aiosqlite.Connection, workspace_id: str, kind: Optional[str] = None, tracked_only: bool = True
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM entities WHERE workspace_id = ?"
    params: list[Any] = [workspace_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if tracked_only:
        sql += " AND is_tracked = 1"
    sql += " ORDER BY kind, lower(name)"
    rows = await fetch_all(db, sql, params)
    links = await entity_links(db, [r["id"] for r in rows])
    for r in rows:
        r["links"] = links.get(r["id"], [])
    return rows


async def set_tracked(db: aiosqlite.Connection, workspace_id: str, entity_id: str, tracked: bool) -> None:
    await db.execute(
        "UPDATE entities SET is_tracked = ? WHERE id = ? AND workspace_id = ?",
        (1 if tracked else 0, entity_id, workspace_id),
    )


def normalise_url(url: Optional[str]) -> Optional[str]:
    url = (url or "").strip()
    if not url or " " in url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    return url
