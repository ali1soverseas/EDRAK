"""Move a v1 database (8 tables of JSON blobs) to the v0.2 schema.

Steps:
  1. Copy the v1 file to edrak.v1.bak.db next to it. The copy is never touched again.
  2. Build the v0.2 schema in a temporary file and port every record into it.
  3. Swap the temporary file into place.

What is carried over:
  * users keep their ids; their unsalted SHA-256 passwords are stored as "sha256$<hex>" and replaced
    with argon2 at the next sign-in.
  * one user per workspace: the first user of a v1 workspace owns it (the demo user for ws-default);
    any other user of the same workspace gets a new, empty workspace and goes through onboarding.
  * company profiles, document records, analyses (form, request, plan), runs, worker results
    (normalized into worker_results / evidence / findings / ...) and briefs (document kept as is,
    document_version 1, number kept).
  * sessions are not carried over: everyone signs in again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from edrak.core.config import settings
from edrak.core.security import legacy_sha256

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "draft": "draft",
    "awaiting_approval": "awaiting_approval",
    "running": "failed",          # nothing can be running across a restart
    "completed": "completed",
    "partial": "partial",
    "failed": "failed",
    "cancelled": "cancelled",
}
RUN_STATE_MAP = {"running": "failed", "completed": "completed", "partial": "partial", "failed": "failed", "cancelled": "cancelled"}
SOURCE_MAP = {
    "internal_files": ["internal_kb"],
    "competitor_web": ["competitor_sites", "web_search"],
    "news_open_data": ["news", "open_data"],
    "reviews_social": ["social_forums"],
}


def _backup_path(path: Path) -> Path:
    candidate = path.with_name("edrak.v1.bak.db")
    n = 1
    while candidate.exists():
        candidate = path.with_name(f"edrak.v1.bak.{n}.db")
        n += 1
    return candidate


def migrate_v1_file(path: Path) -> dict[str, int]:
    """Blocking entry point. Returns counts of what was ported."""
    backup = _backup_path(path)
    # Fold any WAL content into the main file before copying it.
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError:
        pass
    finally:
        conn.close()
    shutil.copy2(path, backup)
    logger.warning("Migrating %s to the v0.2 schema. The original is kept at %s", path, backup)

    tmp = path.with_name(path.name + ".v2.tmp")
    for leftover in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        if leftover.exists():
            leftover.unlink()

    from edrak.db.connection import SCHEMA_VERSION, schema_sql

    conn = sqlite3.connect(tmp)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema_sql())
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()

    counts = asyncio.run(_port(backup, tmp))

    for side in ("-wal", "-shm"):
        p = Path(str(path) + side)
        if p.exists():
            p.unlink()
    os.replace(tmp, path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    finally:
        conn.close()
    logger.warning("Migration finished: %s", counts)
    return counts


def _loads(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _find_file(workspace_id: str, filename: str) -> Optional[Path]:
    upload = settings.get_uploads_path() / workspace_id / filename
    if upload.exists():
        return upload
    internal = settings.get_absolute_internal_data_path()
    if internal.exists():
        return next(internal.rglob(filename), None)
    return None


def _relative_to_data(path: Path) -> str:
    try:
        return path.resolve().relative_to(settings.get_data_path()).as_posix()
    except ValueError:
        return str(path)


async def _port(old_path: Path, new_path: Path) -> dict[str, int]:
    from edrak.contracts import BusinessRequest, OrchestrationResult, ResearchPlan
    from edrak.db.repositories import analyses as analyses_repo
    from edrak.db.repositories import auth as auth_repo
    from edrak.db.repositories import briefs as briefs_repo
    from edrak.db.repositories import company as company_repo
    from edrak.db.repositories import documents as documents_repo
    from edrak.db.repositories import results as results_repo
    from edrak.db.repositories import runs as runs_repo

    old = sqlite3.connect(old_path)
    old.row_factory = sqlite3.Row
    counts = {"users": 0, "workspaces": 0, "documents": 0, "analyses": 0, "runs": 0, "results": 0, "briefs": 0, "skipped": 0}

    db = await aiosqlite.connect(str(new_path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    try:
        users = [dict(r) for r in old.execute("SELECT rowid AS rid, * FROM users ORDER BY rowid")]
        workspaces = {r["workspace_id"]: dict(r) for r in old.execute("SELECT * FROM workspaces")}
        # v1 workspace id -> owner user id in v0.2 (only for the workspace's first user).
        owner_of: dict[str, str] = {}
        users.sort(key=lambda u: (u["workspace_id"], 0 if u["user_id"] == "user-default" else 1, u["rid"]))
        for u in users:
            ws_old = u["workspace_id"]
            if ws_old not in owner_of:
                ws_id, ws_name = ws_old, workspaces.get(ws_old, {}).get("name") or f"{u['name']}'s workspace"
                owner_of[ws_old] = u["user_id"]
            else:
                ws_id, ws_name = None, f"{u['name']}'s workspace"
            await auth_repo.create_account(
                db,
                email=u["email"],
                password_hash=legacy_sha256(u["password_hash"]),
                full_name=u["name"] or u["email"].split("@")[0],
                job_title=u.get("title"),
                workspace_name=ws_name,
                user_id=u["user_id"],
                workspace_id=ws_id,
                commit=False,
            )
            counts["users"] += 1
            counts["workspaces"] += 1
        await db.commit()

        # Company profiles: only for workspaces that kept their v1 id.
        for row in old.execute("SELECT * FROM company_profiles"):
            ws = row["workspace_id"]
            if ws not in owner_of:
                continue
            f = _loads(row["fields_json"], {})
            goals = f.get("strategic_goals") or ""
            links = [{"platform": "website", "url": f["website"]}] if f.get("website") else []
            links += [{"platform": s.get("platform"), "url": s.get("url")} for s in f.get("socials") or [] if isinstance(s, dict)]
            await company_repo.save_company(
                db,
                ws,
                {
                    "name": f.get("name") or "",
                    "aliases": f.get("aliases") or [],
                    "industry": f.get("industry"),
                    "description": f.get("description") or "",
                    "offerings": f.get("offerings") or [],
                    "markets": [{"name": m, "relation": "serves"} for m in f.get("markets") or []],
                    "goals": [g.strip() for g in goals.replace(";", "\n").split("\n") if g.strip()],
                    "links": links,
                },
                user_id=owner_of[ws],
                commit=False,
            )
            if row["completed"] and (f.get("name") or "").strip():
                await db.execute(
                    "UPDATE company_profiles SET completed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), onboarding_step = ? WHERE workspace_id = ?",
                    (company_repo.TOTAL_ONBOARDING_STEPS, ws),
                )
        await db.commit()

        # Documents
        for row in old.execute("SELECT * FROM company_documents"):
            ws = row["workspace_id"]
            if ws not in owner_of:
                continue
            size = _loads(row["size_json"], None) or {}
            path = None if row["document_id"] == "doc-demo-handbook" else _find_file(ws, row["filename"])
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
            if content_hash and await documents_repo.find_by_hash(db, ws, content_hash):
                content_hash = None
            synthetic = row["document_id"].startswith("doc-demo-")
            doc = await documents_repo.create_document(
                db,
                document_id=row["document_id"],
                workspace_id=ws,
                name=row["filename"],
                storage_uri=_relative_to_data(path) if path else "handbook",
                mime_type=None,
                size_bytes=path.stat().st_size if path else None,
                content_hash=content_hash,
                uploaded_by=owner_of[ws],
                origin="folder" if synthetic else "upload",
                status="indexed" if row["status"] == "indexed" else "failed",
                is_synthetic=synthetic,
                commit=False,
            )
            fields: dict[str, Any] = {"index_progress": 100 if row["status"] == "indexed" else 0}
            if size.get("unit") == "pages":
                fields["page_count"] = int(size.get("value") or 0)
            elif size.get("unit") == "rows":
                fields["row_count"] = int(size.get("value") or 0)
            if row["status"] == "indexed":
                fields["indexed_at"] = row["created_at"]
            else:
                fields["error_message"] = "Indexing did not finish before the upgrade. Retry to index it again."
            await documents_repo.update_document(db, doc["id"], commit=False, **fields)
            counts["documents"] += 1
        await db.commit()

        # Analyses, plans, runs, results
        run_rows = {r["analysis_id"]: dict(r) for r in old.execute("SELECT * FROM runs")}
        brief_rows = [dict(r) for r in old.execute("SELECT * FROM briefs")]
        for row in old.execute("SELECT * FROM analyses ORDER BY created_at"):
            a = dict(row)
            ws = a["workspace_id"]
            if ws not in owner_of:
                counts["skipped"] += 1
                continue
            owner = owner_of[ws]
            try:
                await _port_analysis(db, a, run_rows.get(a["analysis_id"]), owner, counts,
                                     BusinessRequest, ResearchPlan, OrchestrationResult,
                                     analyses_repo, runs_repo, results_repo)
                await db.commit()
            except Exception as exc:  # noqa: BLE001 - one bad record must not stop the upgrade
                await db.rollback()
                counts["skipped"] += 1
                logger.warning("Could not migrate analysis %s: %s", a["analysis_id"], exc)

        for b in brief_rows:
            run = await runs_repo.get_run(db, b["analysis_id"])
            req = await analyses_repo.get_request(db, b["analysis_id"])
            if not run or not req:
                counts["skipped"] += 1
                continue
            document = _loads(b["brief_json"], {})
            document["brief_id"] = b["brief_id"]
            try:
                await briefs_repo.save_brief(
                    db,
                    workspace_id=req["workspace_id"],
                    request_id=b["analysis_id"],
                    requested_by=owner_of.get(req["workspace_id"]),
                    build=lambda _id, _no, d=document: d,
                    brief_id=b["brief_id"],
                    number=int(b["brief_no"]),
                    document_version=1,
                )
                await db.execute(
                    """
                    INSERT INTO workspace_counters (workspace_id, key, value) VALUES (?, 'brief', ?)
                    ON CONFLICT (workspace_id, key) DO UPDATE SET value = max(workspace_counters.value, excluded.value)
                    """,
                    (req["workspace_id"], int(b["brief_no"])),
                )
                await db.commit()
                counts["briefs"] += 1
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                counts["skipped"] += 1
                logger.warning("Could not migrate brief %s: %s", b["brief_id"], exc)
    finally:
        await db.close()
        old.close()
    return counts


async def _port_analysis(db, a, run, owner, counts, BusinessRequest, ResearchPlan, OrchestrationResult,
                         analyses_repo, runs_repo, results_repo) -> None:
    form = _loads(a["form_json"], {})
    request_data = _loads(a["request_json"], {})
    request_data["request_id"] = a["analysis_id"]
    request = BusinessRequest.model_validate(request_data)
    status = STATUS_MAP.get(a["status"], "draft")
    if status not in ("draft",) and run is None and status not in ("awaiting_approval",):
        # Failed or cancelled before a run row existed: nothing ran, so it is still a draft.
        status = "draft"

    sources = form.get("sources") or {}
    allowed: list[str] = []
    for key, on in sources.items():
        if on:
            allowed += [s for s in SOURCE_MAP.get(key, []) if s not in allowed]
    competitors = [{"name": c, "discovered_by": "user"} for c in form.get("competitors") or [] if str(c).strip()]
    markets = [form["market"].strip()] if (form.get("market") or "").strip() else []

    await analyses_repo.create_request(
        db,
        request=request,
        workspace_id=a["workspace_id"],
        created_by=owner,
        title=a["title"],
        form=form,
        competitors=competitors,
        markets=markets,
        offering=(form.get("offering") or "").strip() or None,
        target_customers=(form.get("target_customers") or "").strip() or None,
        allowed_source_types=allowed or list(analyses_repo.SOURCE_TYPES),
        repeat_weekly=bool(form.get("repeat_weekly")),
        status="draft",
        commit=False,
    )
    counts["analyses"] += 1

    plan_data = _loads(a.get("plan_json"), None)
    if plan_data:
        plan = ResearchPlan.model_validate(plan_data)
        if run is not None:
            plan_status = "approved"
        elif status == "awaiting_approval":
            plan_status = "awaiting_approval"
        elif a.get("rejection_reason"):
            plan_status = "rejected"
        else:
            plan_status = "superseded"
        await analyses_repo.save_plan(db, plan, status="awaiting_approval", commit=False)
        if plan_status == "approved":
            await db.execute(
                "UPDATE research_plans SET status = 'approved', approved_at = ?, approved_by = ? WHERE plan_id = ?",
                (run["approved_at"], owner, plan.plan_id),
            )
        elif plan_status != "awaiting_approval":
            await db.execute(
                "UPDATE research_plans SET status = ?, rejection_reason = ? WHERE plan_id = ?",
                (plan_status, a.get("rejection_reason"), plan.plan_id),
            )

        if run is not None:
            await db.execute(
                """
                INSERT INTO runs (request_id, plan_id, status, current_stage, triggered_by, started_at, completed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (a["analysis_id"], plan.plan_id, RUN_STATE_MAP.get(run["state"], "failed"),
                 run["stage"] if run["stage"] in ("plan", "dispatch", "workers", "verify", "synthesize", "brief") else "done",
                 owner, run["started_at"] or run["approved_at"], run["finished_at"] or run.get("cancelled_at"),
                 run["approved_at"]),
            )
            counts["runs"] += 1
            result = _loads(run.get("result_json"), None)
            if result:
                orchestration = OrchestrationResult.model_validate(result)
                # v1 re-planned by replacing every task, so a result can belong to a task the stored
                # plan never had. Add those tasks, from the plan the result records, as replan tasks.
                known = await analyses_repo.task_ids(db, plan.plan_id)
                replanned = {t.task_id: t for t in (orchestration.plan.tasks if orchestration.plan else [])}
                position = len(known)
                for wr in orchestration.results:
                    if wr.task_id in known:
                        continue
                    task = replanned.get(wr.task_id)
                    if task is None:
                        continue
                    await analyses_repo.insert_task(
                        db, plan.plan_id, task.model_copy(update={"parent_request_id": a["analysis_id"]}),
                        position=position, origin="replan", reason="Re-planned by the Supervisor during the run.",
                    )
                    known.add(wr.task_id)
                    position += 1
                for wr in orchestration.results:
                    if wr.task_id not in known:
                        continue
                    if await results_repo.write_worker_result(db, a["analysis_id"], wr, commit=False):
                        counts["results"] += 1
                    await analyses_repo.update_task_fields(
                        db, wr.task_id,
                        state="failed" if wr.status.value == "failed" else "finished",
                        progress=100, sources=len(wr.evidence), attempt=wr.attempt,
                    )

    await db.execute(
        "UPDATE business_requests SET status = ?, rejection_reason = ?, created_at = ?, updated_at = ? WHERE request_id = ?",
        (status, a.get("rejection_reason"), a["created_at"], a["updated_at"], a["analysis_id"]),
    )
