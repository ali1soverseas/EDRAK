"""Share approved company facts with the knowledge graph.

SQLite stays the source of truth. A run only ever produces *suggested* facts, so nothing reaches the graph
until a person approves it. This module makes the graph agree with the database:

* an approved fact the graph does not hold yet, or holds in an older form (it was edited), is published;
* a fact the graph holds that is no longer approved (rejected, or deleted) is removed.

It works by comparing state, not by reacting to events. ``company_fact_graph_sync`` remembers a hash of what
was published for each fact, so the same function serves a review click, the manual sync endpoint and any
later retry, and repeating it changes nothing. It never raises: a graph that is off or down is reported in
the result and the facts stay pending, because a person's review must never fail on account of the graph.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Optional

import aiosqlite

from edrak.company_research.models import CompanyFact, FactStatus
from edrak.core.config import settings
from edrak.db.connection import get_db
from edrak.db.repositories import audit as audit_repo
from edrak.db.repositories import company as company_repo
from edrak.db.repositories import company_fact_graph_sync as sync_repo
from edrak.db.repositories import company_facts as facts_repo
from edrak.graph.entities import slugify
from edrak.graph.ingestion.company_facts import CompanyFactRecord, publish_facts, retract_facts

logger = logging.getLogger(__name__)

AUDIT_IDS_MAX = 100  # fact ids kept in one audit row; the count is always exact


@dataclass
class SyncResult:
    published: int = 0
    retracted: int = 0
    failed: int = 0  # facts the graph refused or errored on; they stay pending and the next sync retries them
    pending: int = 0  # facts that need syncing but were not attempted because the graph is off or unreachable
    skipped_reason: Optional[str] = None
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def to_record(fact: CompanyFact) -> CompanyFactRecord:
    return CompanyFactRecord(
        fact_id=fact.id,
        category=fact.category,
        key=fact.key,
        value=fact.value,
        value_type=fact.value_type.value,
        quote=fact.quote,
        source_url=fact.source_url,
        pdf_page=fact.pdf_page,
        source_tier=fact.source_tier.value,
        confidence=fact.confidence.value,
        edited_by_user=fact.edited_by_user,
        conflict_group=fact.conflict_group,
        observed_at=fact.observed_at,
    )


def content_hash(record: CompanyFactRecord, company_id: str, graph_workspace_id: str) -> str:
    """Changes whenever anything the graph holds for this fact would change, including the company it hangs off."""
    payload = {"company": company_id, "graph_workspace": graph_workspace_id, **record.model_dump()}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _connection() -> Any:
    from edrak.graph.connection import get_connection  # lazy: the neo4j driver is only needed when the sync is on

    return get_connection()


_schema_ready: set[int] = set()
_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _lock(workspace_id: str) -> asyncio.Lock:
    """One sync at a time per workspace, so quick successive reviews don't write the same facts twice."""
    return _locks.setdefault((id(asyncio.get_running_loop()), workspace_id), asyncio.Lock())


async def sync_workspace(db: aiosqlite.Connection, workspace_id: str, *, conn: Any = None) -> SyncResult:
    """Bring the graph in line with this workspace's approved facts. Never raises."""
    if not settings.COMPANY_RESEARCH_GRAPH_SYNC:
        return SyncResult(skipped_reason="graph sync is off (COMPANY_RESEARCH_GRAPH_SYNC)")
    try:
        async with _lock(workspace_id):
            return await _sync(db, workspace_id, conn)
    except Exception as exc:  # noqa: BLE001 - the graph must never break the caller
        logger.exception("Company fact graph sync failed for workspace %s", workspace_id)
        return SyncResult(error=f"{type(exc).__name__}: {exc}"[:300])


async def sync_in_background(workspace_id: str) -> None:
    """Entry point for a background task: it opens its own connection, because the request's is closed by then."""
    try:
        async with get_db() as db:
            await sync_workspace(db, workspace_id)
    except Exception:  # noqa: BLE001
        logger.exception("Background company fact graph sync failed for workspace %s", workspace_id)


async def _sync(db: aiosqlite.Connection, workspace_id: str, conn: Any) -> SyncResult:
    from edrak.graph.connection import effective_graph_workspace_id

    company = await company_repo.get_company(db, workspace_id)
    name = (company.get("name") or "").strip()
    company_id = slugify(name)
    if not company_id:
        return SyncResult(skipped_reason="the company has no name yet, so the facts have nothing to attach to")

    graph_workspace_id = effective_graph_workspace_id(workspace_id) or workspace_id
    approved = await facts_repo.list_facts(db, workspace_id, status=FactStatus.APPROVED)
    synced = await sync_repo.list_synced(db, workspace_id)

    records = {fact.id: to_record(fact) for fact in approved}
    hashes = {fact_id: content_hash(rec, company_id, graph_workspace_id) for fact_id, rec in records.items()}
    to_publish = [fact_id for fact_id, h in hashes.items() if synced.get(fact_id) != h]
    to_retract = [fact_id for fact_id in synced if fact_id not in records]

    result = SyncResult()
    if not to_publish and not to_retract:
        return result  # nothing changed: the graph is not even contacted

    conn = conn if conn is not None else _connection()
    if not await asyncio.to_thread(conn.available):
        result.pending = len(to_publish) + len(to_retract)
        result.skipped_reason = "the knowledge graph is off or unreachable; the facts stay pending"
        return result
    if id(conn) not in _schema_ready:
        await asyncio.to_thread(conn.apply_schema)
        _schema_ready.add(id(conn))

    if to_retract:
        try:
            await asyncio.to_thread(retract_facts, conn, graph_workspace_id, to_retract)
            await sync_repo.clear_synced(db, workspace_id, to_retract)
            result.retracted = len(to_retract)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Retracting %d company facts from the graph failed: %s", len(to_retract), exc)
            result.failed += len(to_retract)
            result.error = f"{type(exc).__name__}: {exc}"[:300]

    if to_publish:
        try:
            await asyncio.to_thread(
                publish_facts, conn, graph_workspace_id, name, [records[fact_id] for fact_id in to_publish]
            )
            await sync_repo.mark_synced(db, workspace_id, {fact_id: hashes[fact_id] for fact_id in to_publish})
            result.published = len(to_publish)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Publishing %d company facts to the graph failed: %s", len(to_publish), exc)
            result.failed += len(to_publish)
            result.error = f"{type(exc).__name__}: {exc}"[:300]

    await _audit(db, workspace_id, "company_fact.graph_published", to_publish[: AUDIT_IDS_MAX], result.published)
    await _audit(db, workspace_id, "company_fact.graph_retracted", to_retract[: AUDIT_IDS_MAX], result.retracted)
    return result


async def _audit(db: aiosqlite.Connection, workspace_id: str, action: str, fact_ids: list[str], count: int) -> None:
    if not count:
        return
    await audit_repo.record(
        db,
        workspace_id=workspace_id,
        action=action,
        target_type="company_fact",
        target_id=None,
        actor_kind="system",
        metadata={"count": count, "fact_ids": fact_ids},
    )
