"""Hybrid search over one brief: keyword (FTS5) and vector (Chroma), merged by reciprocal rank."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

import aiosqlite

from edrak.db.util import fetch_all

RRF_K = 60


def _match(query: str) -> Optional[str]:
    words = re.findall(r"\w{2,}", query or "", flags=re.UNICODE)[:10]
    return " OR ".join(f'"{w}"' for w in words) or None


async def _keyword(db: aiosqlite.Connection, brief_id: str, query: str, k: int, kinds: Optional[list[str]]) -> list[dict[str, Any]]:
    m = _match(query)
    if not m:
        return []
    sql = "SELECT kind, ref_id, card_version, text FROM brief_search WHERE brief_search MATCH ? AND brief_id = ?"
    params: list[Any] = [m, brief_id]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        params += kinds
    return await fetch_all(db, sql + " ORDER BY bm25(brief_search) LIMIT ?", (*params, k))


def _vector(brief_id: str, query: str, k: int, kinds: Optional[list[str]]) -> list[dict[str, Any]]:
    from edrak.briefing.index import _collection

    col = _collection(brief_id)
    if col is None:
        return []
    where: dict[str, Any] = {"superseded": False}
    if kinds:
        where = {"$and": [where, {"kind": {"$in": kinds}}]}
    try:
        got = col.query(query_texts=[query], n_results=k, where=where)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for doc, md in zip((got.get("documents") or [[]])[0], (got.get("metadatas") or [[]])[0]):
        out.append({"kind": md.get("kind"), "ref_id": md.get("ref_id"), "card_version": md.get("card_version", 1), "text": doc})
    return out


async def hybrid_search(db: aiosqlite.Connection, brief_id: str, query: str, *, k: int = 8, kinds: Optional[list[str]] = None) -> list[dict[str, Any]]:
    kw = await _keyword(db, brief_id, query, k * 2, kinds)
    vec = await asyncio.to_thread(_vector, brief_id, query, k * 2, kinds)
    scores: dict[tuple, float] = {}
    rows: dict[tuple, dict[str, Any]] = {}
    for ranked in (kw, vec):
        for rank, r in enumerate(ranked):
            key = (r["kind"], str(r["ref_id"]))
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            rows.setdefault(key, r)
    return [{**rows[key], "score": round(score, 4)} for key, score in sorted(scores.items(), key=lambda kv: -kv[1])[:k]]
