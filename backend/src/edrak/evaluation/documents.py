"""Retrievability of an indexed document: can retrieval find it from its own key sentences?

For up to three of the document's chunks, the most informative sentence is used as a query against
the workspace's knowledge base. The score is the share of those queries whose top five results
include this document. A low score means the document is indexed but hard to find (for example a
scan with little text, or a table whose rows read alike), which the file viewer shows.
No model is used, so this runs for every upload.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from edrak.db.connection import get_db
from edrak.db.repositories import documents as documents_repo

SAMPLES = 3
TOP_K = 5


def probe_sentence(text: str) -> str:
    """The longest plain sentence of a chunk (headings and very long lines left out)."""
    body = re.sub(r"^#+ .*$", "", text, flags=re.M)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body) if 6 <= len(s.split()) <= 40]
    return max(sentences, key=len) if sentences else " ".join(body.split()[:25])


def pick(chunks: list[dict[str, Any]], n: int = SAMPLES) -> list[dict[str, Any]]:
    usable = [c for c in chunks if not c["excluded"] and len(c["content"].split()) >= 12]
    if len(usable) <= n:
        return usable
    step = len(usable) / n
    return [usable[int(i * step)] for i in range(n)]


async def score_retrievability(workspace_id: str, document_id: str) -> float | None:
    async with get_db() as db:
        chunks = await documents_repo.list_chunks(db, workspace_id, document_id)
    probes = [probe_sentence(c["content"]) for c in pick(chunks)]
    probes = [p for p in probes if p]
    if not probes:
        return None

    def run() -> int:
        from edrak.rag.retriever import InternalRetriever

        retriever = InternalRetriever()
        hits = 0
        for query in probes:
            found = retriever.retrieve(query, top_k=TOP_K, min_score=0.0, workspace_id=workspace_id)
            if any((e.metadata or {}).get("document_id") == document_id for e in found):
                hits += 1
        return hits

    hits = await asyncio.to_thread(run)
    score = round(hits / len(probes), 3)
    async with get_db() as db:
        await documents_repo.update_document(db, document_id, retrievability=score)
    return score
