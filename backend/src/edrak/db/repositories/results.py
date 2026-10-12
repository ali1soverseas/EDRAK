"""Contract results in the normalized tables.

write_worker_result follows the documented order (worker_results, evidence, findings,
finding_evidence, conflicts) inside one savepoint, so a result is either stored whole or not at all.
Evidence numbers (E1, E2, ...) are handed out by the evidence_assign_number trigger.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiosqlite

from edrak.contracts import (
    Conflict,
    Evidence,
    EvidenceRef,
    Finding,
    VerificationResult,
    WorkerResult,
)
from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id, to_iso

logger = logging.getLogger(__name__)


async def has_worker_result(db: aiosqlite.Connection, task_id: str, attempt: int) -> bool:
    row = await fetch_one(db, "SELECT 1 AS x FROM worker_results WHERE task_id = ? AND attempt = ?", (task_id, attempt))
    return row is not None


async def write_worker_result(
    db: aiosqlite.Connection, request_id: str, result: WorkerResult, *, commit: bool = True
) -> bool:
    """Store one WorkerResult. Returns False when that attempt is already stored."""
    if await has_worker_result(db, result.task_id, result.attempt):
        return False
    await db.execute("SAVEPOINT write_result")
    try:
        await db.execute(
            """
            INSERT INTO worker_results (task_id, attempt, worker, request_id, status, gaps, confidence,
                                        started_at, completed_at, error, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.task_id, result.attempt, result.worker.value, request_id, result.status.value,
                dumps(list(result.gaps)), result.confidence, to_iso(result.started_at), to_iso(result.completed_at),
                result.error, dumps(result.model_dump(mode="json")["metadata"]),
            ),
        )
        for ev in result.evidence:
            await _insert_evidence(db, request_id, result, ev)
        for f in result.findings:
            await db.execute(
                """
                INSERT INTO findings (task_id, attempt, finding_id, request_id, statement, category, confidence, limitations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (result.task_id, result.attempt, f.finding_id, request_id, f.statement, f.category.value,
                 f.confidence, dumps(list(f.limitations))),
            )
            for ref in f.evidence_refs:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO finding_evidence (task_id, attempt, finding_id, evidence_id, relation)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (result.task_id, result.attempt, f.finding_id, ref.evidence_id, ref.relation.value),
                )
        for c in result.conflicts:
            await db.execute(
                """
                INSERT INTO conflicts (task_id, attempt, finding_id, contradicting_evidence_ids, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                (result.task_id, result.attempt, c.finding_id, dumps(list(c.contradicting_evidence_ids)), c.description),
            )
        await db.execute("RELEASE write_result")
    except Exception:
        await db.execute("ROLLBACK TO write_result")
        await db.execute("RELEASE write_result")
        raise
    if commit:
        await db.commit()
    return True


async def write_verification(
    db: aiosqlite.Connection, verification: VerificationResult, *, commit: bool = True
) -> str:
    """Store one verification pass. Verdicts for findings not stored (no matching row) are skipped."""
    vid = new_id()
    cs = verification.control_summary
    await db.execute(
        """
        INSERT INTO verification_results (id, request_id, decision_status, decision_summary, missing_information,
                                          conflicts, failures, next_research_targets, metadata, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            vid, verification.research_run_id, verification.decision.status.value, verification.decision.summary,
            dumps(cs.missing_information), dumps(cs.conflicts), dumps(cs.failures), dumps(cs.next_research_targets),
            dumps(verification.model_dump(mode="json")["metadata"]), to_iso(verification.completed_at),
        ),
    )
    for position, action in enumerate(verification.decision.targeted_actions):
        await db.execute(
            "INSERT INTO verification_actions (verification_id, position, worker, reason) VALUES (?, ?, ?, ?)",
            (vid, position, action.worker.value, action.reason),
        )
    for verdict in verification.findings:
        # A verdict names a finding by finding_id only; take the latest attempt that holds it.
        owner = await fetch_one(
            db,
            "SELECT task_id, attempt FROM findings WHERE request_id = ? AND finding_id = ? ORDER BY attempt DESC LIMIT 1",
            (verification.research_run_id, verdict.finding_id),
        )
        if not owner:
            continue
        await db.execute(
            """
            INSERT OR REPLACE INTO finding_verdicts (verification_id, task_id, attempt, finding_id, verification_status,
                                                     evidence_quality, evidence_ids, contradictions,
                                                     missing_information, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vid, owner["task_id"], owner["attempt"], verdict.finding_id, verdict.verification_status.value,
                verdict.evidence_quality.value, dumps(verdict.evidence_ids), dumps(verdict.contradictions),
                dumps(verdict.missing_information), verdict.confidence,
            ),
        )
    if commit:
        await db.commit()
    return vid


async def write_gaps(
    db: aiosqlite.Connection, request_id: str, gaps: list[dict[str, Any]], *, commit: bool = True
) -> None:
    """Replace a run's open information gaps. Each gap: worker?, subject, description?, suggested_action."""
    await db.execute("DELETE FROM information_gaps WHERE request_id = ? AND status = 'open'", (request_id,))
    for g in gaps:
        subject = (g.get("subject") or "").strip()
        if not subject:
            continue
        await db.execute(
            """
            INSERT INTO information_gaps (request_id, worker, subject, description, searched_note, suggested_action)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_id, g.get("worker"), subject[:500], g.get("description"), g.get("searched_note"),
             g.get("suggested_action") or "investigate"),
        )
    if commit:
        await db.commit()


async def evidence_numbers(db: aiosqlite.Connection, request_id: str) -> dict[tuple[str, int, str], int]:
    """(task_id, attempt, evidence_id) -> E-number, for the brief and the evidence explorer."""
    rows = await fetch_all(
        db, "SELECT task_id, attempt, evidence_id, display_no FROM evidence WHERE request_id = ?", (request_id,)
    )
    return {(r["task_id"], r["attempt"], r["evidence_id"]): r["display_no"] for r in rows}


async def load_worker_results(db: aiosqlite.Connection, request_id: str, latest_only: bool = True) -> list[WorkerResult]:
    """Rebuild WorkerResult contracts from the tables. latest_only keeps the last attempt of each task."""
    results = await fetch_all(
        db, "SELECT * FROM worker_results WHERE request_id = ? ORDER BY task_id, attempt", (request_id,)
    )
    if latest_only:
        last: dict[str, dict[str, Any]] = {}
        for r in results:
            last[r["task_id"]] = r
        results = list(last.values())
    out: list[WorkerResult] = []
    for r in results:
        key = (r["task_id"], r["attempt"])
        evidence_rows = await fetch_all(
            db, "SELECT * FROM evidence WHERE task_id = ? AND attempt = ? ORDER BY display_no", key
        )
        finding_rows = await fetch_all(db, "SELECT * FROM findings WHERE task_id = ? AND attempt = ?", key)
        refs = await fetch_all(db, "SELECT * FROM finding_evidence WHERE task_id = ? AND attempt = ?", key)
        conflict_rows = await fetch_all(db, "SELECT * FROM conflicts WHERE task_id = ? AND attempt = ?", key)
        by_finding: dict[str, list[EvidenceRef]] = {}
        for ref in refs:
            by_finding.setdefault(ref["finding_id"], []).append(
                EvidenceRef(evidence_id=ref["evidence_id"], relation=ref["relation"])
            )
        out.append(
            WorkerResult(
                task_id=r["task_id"],
                worker=r["worker"],
                status=r["status"],
                attempt=r["attempt"],
                gaps=loads(r["gaps"], []),
                confidence=r["confidence"],
                started_at=r["started_at"],
                completed_at=r["completed_at"],
                error=r["error"],
                metadata=loads(r["metadata"], {}),
                evidence=[
                    Evidence(
                        evidence_id=e["evidence_id"],
                        source_type=e["source_type"],
                        source_title=e["source_title"],
                        source_url=e["source_url"],
                        publisher=e["publisher"],
                        extracted_fact=e["extracted_fact"],
                        excerpt=e["excerpt"],
                        retrieved_at=e["retrieved_at"],
                        is_synthetic=bool(e["is_synthetic"]),
                        metadata=loads(e["metadata"], {}),
                    )
                    for e in evidence_rows
                ],
                findings=[
                    Finding(
                        finding_id=f["finding_id"],
                        statement=f["statement"],
                        category=f["category"],
                        confidence=f["confidence"],
                        limitations=loads(f["limitations"], []),
                        evidence_refs=by_finding.get(f["finding_id"], []),
                    )
                    for f in finding_rows
                ],
                conflicts=[
                    Conflict(
                        finding_id=c["finding_id"],
                        contradicting_evidence_ids=loads(c["contradicting_evidence_ids"], []),
                        description=c["description"],
                    )
                    for c in conflict_rows
                ],
            )
        )
    return out


async def latest_verification(db: aiosqlite.Connection, request_id: str) -> Optional[dict[str, Any]]:
    row = await fetch_one(
        db,
        "SELECT * FROM verification_results WHERE request_id = ? ORDER BY completed_at DESC LIMIT 1",
        (request_id,),
    )
    if not row:
        return None
    for col in ("missing_information", "conflicts", "failures", "next_research_targets"):
        row[col] = loads(row[col], [])
    row["metadata"] = loads(row["metadata"], {})
    row["verdicts"] = await fetch_all(
        db, "SELECT * FROM finding_verdicts WHERE verification_id = ?", (row["id"],)
    )
    return row


async def _insert_evidence(db: Any, request_id: str, result: WorkerResult, ev: Any, *, ignore: bool = False) -> None:
    dumped = ev.model_dump(mode="json")
    meta = dumped.get("metadata") or {}
    await db.execute(
        f"""
        INSERT {"OR IGNORE " if ignore else ""}INTO evidence (task_id, attempt, evidence_id, request_id, source_type, source_title,
                              source_url, publisher, extracted_fact, excerpt, page_text, retrieved_at, is_synthetic,
                              metadata, document_id, chunk_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                (SELECT id FROM documents WHERE id = ?), (SELECT id FROM document_chunks WHERE id = ?))
        """,
        (
            result.task_id, result.attempt, ev.evidence_id, request_id, ev.source_type.value, ev.source_title,
            ev.source_url, ev.publisher, ev.extracted_fact, ev.excerpt, ev.page_text, to_iso(ev.retrieved_at),
            1 if ev.is_synthetic else 0, dumps(meta), meta.get("document_id"), meta.get("chunk_id"),
        ),
    )


async def add_evidence(db: Any, request_id: str, result: WorkerResult, *, commit: bool = True) -> int:
    """Evidence added to a stored result after the worker finished (the metrics agent's pages and tallies)."""
    added = 0
    for ev in result.evidence:
        cur = await db.execute("SELECT 1 FROM evidence WHERE task_id = ? AND attempt = ? AND evidence_id = ?",
                               (result.task_id, result.attempt, ev.evidence_id))
        if await cur.fetchone():
            continue
        await _insert_evidence(db, request_id, result, ev, ignore=True)
        added += 1
    if commit:
        await db.commit()
    return added
