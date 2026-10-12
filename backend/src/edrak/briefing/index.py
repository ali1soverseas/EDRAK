"""Index a validated brief for chat: a keyword index (SQLite FTS5) and a vector index (Chroma, one collection per brief).

Exact numbers are NOT answered from here; they come from the metrics table by id. This index is for finding
the right evidence, claims, cards, gaps and verifier notes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)
MAX_TEXT = 1200


@dataclass
class IndexDoc:
    kind: str          # evidence | claim | card | gap | verifier_note | signal
    ref_id: str        # E-number, finding id, card key, ...
    text: str
    version: int = 1
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        return f"{self.kind}:{self.ref_id}:{self.version}"


def _flat(value: Any, out: list[str], depth: int = 0) -> None:
    if depth > 4:
        return
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        out.append(str(value))
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in ("uid", "refs", "raw", "direction", "formula"):
                continue
            _flat(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value[:40]:
            _flat(v, out, depth + 1)


def card_text(card: dict[str, Any]) -> str:
    parts = [card.get("title") or "", card.get("headline") or "", card.get("caveat") or "", f"status {card.get('status')}"]
    flat: list[str] = []
    _flat(card.get("slots") or {}, flat)
    return " ".join(p for p in [*parts, *flat] if p)[:MAX_TEXT]


def card_doc(card: dict[str, Any], version: int = 1) -> IndexDoc:
    return IndexDoc("card", card["card_key"], card_text(card), version,
                    {"lens": card.get("section") or "", "entity": card.get("entity") or "", "status": card.get("status") or "",
                     "metric_id": card.get("metric_id") or ""})


def docs_from_document(document: dict[str, Any]) -> list[IndexDoc]:
    docs: list[IndexDoc] = []
    for item in document.get("evidence") or []:
        ev = item.get("evidence") or {}
        text = f"{ev.get('source_title') or ''}. {ev.get('extracted_fact') or ''} {ev.get('excerpt') or ''}".strip()
        if text:
            docs.append(IndexDoc("evidence", str(item.get("display_no")), text[:MAX_TEXT], 1, {
                "lens": item.get("worker") or "", "source": ev.get("source_url") or ev.get("source_title") or "",
                "reliability": int(item.get("reliability") or 0), "status": item.get("note") or ""}))
    for lens in document.get("lenses") or []:
        for f in lens.get("findings") or []:
            stmt = (f.get("finding") or {}).get("statement")
            if stmt:
                refs = ",".join(str(r.get("display_no")) for r in f.get("refs") or [])
                docs.append(IndexDoc("claim", (f.get("finding") or {}).get("finding_id", stmt[:20]), f"{stmt} (evidence {refs})"[:MAX_TEXT], 1,
                                     {"lens": lens.get("worker") or "", "status": f.get("status") or ""}))
    for card in (document.get("cards") or {}).values():
        docs.append(card_doc(card))
    for g in (document.get("gaps_panel") or {}).get("gaps", []):
        docs.append(IndexDoc("gap", g.get("card_key") or g.get("metric_id") or "gap", f"{g.get('title')}: {g.get('explanation')}", 1,
                             {"entity": g.get("entity") or ""}))
    for g in document.get("gaps") or []:
        docs.append(IndexDoc("gap", (g.get("text") or "")[:40], g.get("text") or "", 1, {"lens": g.get("reported_by") or ""}))
    ctl = ((document.get("verification") or {}).get("control_summary") or {})
    for kind_key in ("missing_information", "conflicts", "failures"):
        for i, t in enumerate(ctl.get(kind_key) or []):
            docs.append(IndexDoc("verifier_note", f"{kind_key}-{i}", f"Verifier ({kind_key.replace('_', ' ')}): {t}", 1, {}))
    for c in document.get("conflicts") or []:
        docs.append(IndexDoc("verifier_note", f"conflict-{c.get('metric_id')}-{c.get('entity')}", f"Sources disagree on {c.get('metric_id')} for {c.get('entity')}", 1, {}))
    for s in document.get("signals_view") or []:
        docs.append(IndexDoc("signal", s.get("signal_id") or "", f"{s.get('title')}. {s.get('signal')} {s.get('why_it_matters') or ''}"[:MAX_TEXT], 1,
                             {"lens": ",".join(map(str, s.get("domains") or []))}))
    return [d for d in docs if d.text.strip()]


# ---------------------------------------------------------------------------- stores


def _collection(brief_id: str) -> Optional[Any]:
    try:
        from edrak.rag.indexer import InternalIndexer

        return InternalIndexer(collection_name=f"brief_{brief_id}").collection
    except Exception as exc:  # noqa: BLE001 - search falls back to keywords alone
        logger.info("Vector index unavailable for brief %s: %s", brief_id, exc)
        return None


def _vector_upsert(brief_id: str, docs: list[IndexDoc], supersede: Optional[tuple[str, int]] = None) -> None:
    col = _collection(brief_id)
    if col is None or not docs:
        return
    if supersede:  # an older version of this card stays in the store but is excluded from search
        key, version = supersede
        try:
            old = col.get(where={"$and": [{"kind": "card"}, {"ref_id": key}]})
            for oid, md in zip(old.get("ids") or [], old.get("metadatas") or []):
                if int(md.get("card_version", 1)) < version:
                    col.update(ids=[oid], metadatas=[{**md, "superseded": True}])
        except Exception:  # noqa: BLE001
            pass
    col.upsert(ids=[d.doc_id for d in docs], documents=[d.text for d in docs],
               metadatas=[{**{k: v for k, v in d.meta.items() if v is not None}, "brief_id": brief_id, "kind": d.kind, "ref_id": d.ref_id,
                           "card_version": d.version, "superseded": False} for d in docs])


async def index_brief(db: aiosqlite.Connection, brief_id: str, document: dict[str, Any]) -> int:
    docs = docs_from_document(document)
    await db.execute("DELETE FROM brief_search WHERE brief_id = ?", (brief_id,))
    for d in docs:
        await db.execute("INSERT INTO brief_search (brief_id, kind, ref_id, card_version, text) VALUES (?,?,?,?,?)",
                         (brief_id, d.kind, d.ref_id, d.version, d.text))
    await db.commit()
    await asyncio.to_thread(_vector_upsert, brief_id, docs)
    return len(docs)


async def index_card_version(db: aiosqlite.Connection, brief_id: str, card: dict[str, Any], version: int) -> None:
    """A new card version replaces the old one in search; the old row stays in brief_cards."""
    doc = card_doc(card, version)
    await db.execute("DELETE FROM brief_search WHERE brief_id = ? AND kind = 'card' AND ref_id = ?", (brief_id, card["card_key"]))
    await db.execute("INSERT INTO brief_search (brief_id, kind, ref_id, card_version, text) VALUES (?,?,?,?,?)",
                     (brief_id, doc.kind, doc.ref_id, version, doc.text))
    await db.commit()
    await asyncio.to_thread(_vector_upsert, brief_id, [doc], (card["card_key"], version))
