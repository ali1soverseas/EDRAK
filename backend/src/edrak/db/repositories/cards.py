"""Card versions on a brief. The brief document is frozen; a challenge adds a version here."""

from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    out["slots"] = loads(out.get("slots"), {})
    out["labels"] = loads(out.get("labels"), {})
    return out


async def insert_initial(db: aiosqlite.Connection, brief_id: str, cards: list[dict[str, Any]], *, commit: bool = True) -> None:
    for c in cards:
        await db.execute(
            "INSERT OR IGNORE INTO brief_cards (id, brief_id, card_key, version, section, component, headline, why_visual,"
            " slots, labels, status, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pipeline')",
            (new_id(), brief_id, c["card_key"], 1, c.get("section"), c["component"], c.get("headline"), c.get("why_visual"),
             dumps(c.get("slots") or {}), dumps({"title": c.get("title"), "caveat": c.get("caveat"), "refs": c.get("refs"),
                                                 "metric_uids": c.get("metric_uids"), "metric_id": c.get("metric_id"),
                                                 "entity": c.get("entity")}), c.get("status")),
        )
    if commit:
        await db.commit()


async def latest(db: aiosqlite.Connection, brief_id: str) -> dict[str, dict[str, Any]]:
    rows = await fetch_all(
        db, "SELECT * FROM brief_cards bc WHERE brief_id = ? AND version = ("
            "SELECT MAX(version) FROM brief_cards WHERE brief_id = bc.brief_id AND card_key = bc.card_key)", (brief_id,))
    return {r["card_key"]: _row(r) for r in rows}


async def versions(db: aiosqlite.Connection, brief_id: str, card_key: str) -> list[dict[str, Any]]:
    rows = await fetch_all(db, "SELECT * FROM brief_cards WHERE brief_id = ? AND card_key = ? ORDER BY version", (brief_id, card_key))
    return [_row(r) for r in rows]


async def add_version(db: aiosqlite.Connection, brief_id: str, card_key: str, card: dict[str, Any], *,
                      note: Optional[str], created_by: Optional[str]) -> dict[str, Any]:
    prev = await fetch_one(db, "SELECT id, version FROM brief_cards WHERE brief_id = ? AND card_key = ? ORDER BY version DESC LIMIT 1",
                           (brief_id, card_key))
    version = (prev["version"] if prev else 0) + 1
    cid = new_id()
    await db.execute(
        "INSERT INTO brief_cards (id, brief_id, card_key, version, section, component, headline, why_visual, slots, labels,"
        " status, origin, supersedes_id, note, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'challenge', ?,?,?)",
        (cid, brief_id, card_key, version, card.get("section"), card["component"], card.get("headline"), card.get("why_visual"),
         dumps(card.get("slots") or {}), dumps({"title": card.get("title"), "caveat": card.get("caveat"), "refs": card.get("refs"),
                                                "metric_uids": card.get("metric_uids"), "metric_id": card.get("metric_id"),
                                                "entity": card.get("entity")}),
         card.get("status"), prev["id"] if prev else None, note, created_by),
    )
    await db.commit()
    return {"id": cid, "version": version}
