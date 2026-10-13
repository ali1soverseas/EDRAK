"""Analyses: business requests, their plans (versioned) and research tasks."""

from __future__ import annotations

from edrak.metrics.custom import metric_set

from typing import Any, Iterable, Optional

import aiosqlite

from edrak.contracts import BusinessContext, BusinessRequest, CompanyProfile, ResearchPlan, ResearchTask
from edrak.db.util import dumps, fetch_all, fetch_one, fetch_value, loads, now_iso, to_iso

# Statuses in which a request is "done" and has nothing more to run.
FINISHED = ("completed", "partial", "failed", "cancelled")
# Workers an analysis may use, by allowed source type.
SOURCE_TYPES = ("internal_kb", "competitor_sites", "web_search", "news", "open_data", "social_forums")

_JSON_COLUMNS = (
    "company_profile", "targets", "competitors", "markets", "focus_areas", "constraints", "extras", "form",
    "allowed_source_types",
)


def _parse(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    for col in _JSON_COLUMNS:
        if col in row:
            row[col] = loads(row[col], {} if col in ("company_profile", "extras", "form") else [])
    return row


# ------------------------------------------------------------------ requests


async def create_request(
    db: aiosqlite.Connection,
    *,
    request: BusinessRequest,
    workspace_id: str,
    created_by: Optional[str],
    title: str,
    form: dict[str, Any],
    competitors: list[dict[str, Any]],
    markets: list[str],
    offering: Optional[str],
    target_customers: Optional[str],
    allowed_source_types: list[str],
    repeat_weekly: bool = False,
    notify_on_finish: bool = True,
    status: str = "draft",
    launched_by: str = "user",
    monitor_id: Optional[str] = None,
    parent_request_id: Optional[str] = None,
    follow_up_reason: Optional[str] = None,
    commit: bool = True,
) -> None:
    ctx = request.business_context
    await db.execute(
        """
        INSERT INTO business_requests (
          request_id, workspace_id, created_by, title, goal, company_profile, use_case, targets, competitors,
          markets, offering, target_customers, focus_areas, trigger, time_window_days, constraints, extras, form,
          launched_by, monitor_id, parent_request_id, follow_up_reason, allowed_source_types, repeat_weekly,
          notify_on_finish, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.request_id, workspace_id, created_by, title, request.goal,
            dumps(request.company_profile.model_dump(mode="json")), ctx.use_case.value, dumps(list(ctx.targets)),
            dumps(competitors), dumps(markets), offering, target_customers, dumps(list(ctx.focus_areas)),
            ctx.trigger.value, ctx.time_window_days, dumps(list(ctx.constraints)), dumps(request.extras), dumps(form),
            launched_by, monitor_id, parent_request_id, follow_up_reason, dumps(allowed_source_types),
            1 if repeat_weekly else 0, 1 if notify_on_finish else 0, status, to_iso(request.created_at),
        ),
    )
    if commit:
        await db.commit()


async def update_request_contract(db: aiosqlite.Connection, request: BusinessRequest, **app_fields: Any) -> None:
    """Rewrite the contract columns from `request`, plus any app columns given."""
    ctx = request.business_context
    sets = {
        "goal": request.goal,
        "company_profile": dumps(request.company_profile.model_dump(mode="json")),
        "use_case": ctx.use_case.value,
        "targets": dumps(list(ctx.targets)),
        "focus_areas": dumps(list(ctx.focus_areas)),
        "trigger": ctx.trigger.value,
        "time_window_days": ctx.time_window_days,
        "constraints": dumps(list(ctx.constraints)),
        "extras": dumps(request.extras),
    }
    for key, value in app_fields.items():
        sets[key] = dumps(value) if key in _JSON_COLUMNS else value
    cols = ", ".join(f"{k} = ?" for k in sets)
    await db.execute(f"UPDATE business_requests SET {cols} WHERE request_id = ?", [*sets.values(), request.request_id])
    await db.commit()


async def set_request_status(
    db: aiosqlite.Connection,
    request_id: str,
    status: str,
    *,
    failure_reason: Optional[str] = None,
    rejection_reason: Optional[str] = None,
    commit: bool = True,
) -> None:
    completed_at = now_iso() if status in FINISHED else None
    await db.execute(
        """
        UPDATE business_requests
        SET status = ?, completed_at = ?,
            failure_reason = coalesce(?, failure_reason),
            rejection_reason = coalesce(?, rejection_reason)
        WHERE request_id = ?
        """,
        (status, completed_at, failure_reason, rejection_reason, request_id),
    )
    if commit:
        await db.commit()


async def get_request(
    db: aiosqlite.Connection, request_id: str, workspace_id: Optional[str] = None
) -> Optional[dict[str, Any]]:
    sql = "SELECT * FROM business_requests WHERE request_id = ?"
    params: list[Any] = [request_id]
    if workspace_id is not None:
        sql += " AND workspace_id = ?"
        params.append(workspace_id)
    return _parse(await fetch_one(db, sql, params))


def request_contract(row: dict[str, Any]) -> BusinessRequest:
    """Rebuild the BusinessRequest contract from a business_requests row."""
    profile = dict(row["company_profile"] or {})
    if not profile.get("name"):
        profile["name"] = "Unnamed company"
    known_profile = set(CompanyProfile.model_fields)
    context_fields = set(BusinessContext.model_fields)
    context: dict[str, Any] = {
        "use_case": row["use_case"],
        "targets": row["targets"],
        "focus_areas": row["focus_areas"],
        "trigger": row["trigger"],
        "time_window_days": row["time_window_days"],
        "constraints": row["constraints"],
    }
    # Standard names, sent when the contract carries them.
    if "competitors" in context_fields:
        context["competitors"] = row.get("competitors") or []
    if "markets" in context_fields:
        context["markets"] = row.get("markets") or []
    if "offering" in context_fields:
        context["offering"] = row.get("offering")
    if "target_customers" in context_fields:
        context["target_customers"] = row.get("target_customers")
    return BusinessRequest(
        request_id=row["request_id"],
        goal=row["goal"] or row["title"] or "Untitled analysis",
        company_profile=CompanyProfile(**{k: v for k, v in profile.items() if k in known_profile}),
        business_context=BusinessContext(**context),
        extras=row["extras"] or {},
        created_at=row["created_at"],
    )


async def list_requests(db: aiosqlite.Connection, workspace_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(
        db,
        """
        SELECT r.request_id, r.title, r.use_case, r.status, r.updated_at, r.created_at, r.repeat_weekly,
               r.launched_by, r.monitor_id,
               b.id AS brief_id, b.number AS brief_no, b.evidence_status,
               (SELECT count(*) FROM research_tasks t JOIN research_plans p ON p.plan_id = t.plan_id
                 WHERE p.request_id = r.request_id AND p.status IN ('awaiting_approval','approved') AND t.enabled = 1) AS tasks_total,
               (SELECT count(*) FROM research_tasks t JOIN research_plans p ON p.plan_id = t.plan_id
                 WHERE p.request_id = r.request_id AND p.status = 'approved' AND t.state = 'finished') AS tasks_done,
               (SELECT count(*) FROM research_tasks t JOIN research_plans p ON p.plan_id = t.plan_id
                 WHERE p.request_id = r.request_id AND p.status = 'approved' AND t.state = 'failed') AS tasks_failed,
               (SELECT count(*) FROM information_gaps g WHERE g.request_id = r.request_id AND g.status = 'open') AS open_gaps
        FROM business_requests r
        LEFT JOIN briefs b ON b.request_id = r.request_id
        WHERE r.workspace_id = ?
        ORDER BY r.updated_at DESC
        """,
        (workspace_id,),
    )
    for r in rows:
        r["verified"] = r.get("evidence_status") == "verified"
        r["repeat_weekly"] = bool(r["repeat_weekly"])
    return rows


# ------------------------------------------------------------------ plans


async def save_plan(
    db: aiosqlite.Connection,
    plan: ResearchPlan,
    *,
    status: str = "awaiting_approval",
    est_minutes: Optional[int] = None,
    scope: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> int:
    """Store a new plan version for its request. Any open plan of the request is superseded."""
    await db.execute(
        "UPDATE research_plans SET status = 'superseded' WHERE request_id = ? AND status = 'awaiting_approval'",
        (plan.request_id,),
    )
    version = int(await fetch_value(
        db, "SELECT coalesce(max(version), 0) + 1 FROM research_plans WHERE request_id = ?", (plan.request_id,)
    ))
    workers = {t.worker for t in plan.tasks}
    await db.execute(
        """
        INSERT INTO research_plans (plan_id, request_id, rationale, created_at, version, status,
                                    est_tasks, est_workers, est_minutes, scope)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (plan.plan_id, plan.request_id, plan.rationale, to_iso(plan.created_at), version, status,
         len(plan.tasks), len(workers), est_minutes, dumps(scope or {})),
    )
    for position, task in enumerate(plan.tasks):
        await insert_task(db, plan.plan_id, task, position=position)
    if commit:
        await db.commit()
    return version


async def insert_task(
    db: aiosqlite.Connection,
    plan_id: str,
    task: ResearchTask,
    *,
    position: int,
    origin: str = "plan",
    reason: Optional[str] = None,
) -> None:
    await db.execute(
        """
        INSERT INTO research_tasks (task_id, parent_request_id, plan_id, worker, goal, focus, attempt, position, origin, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task.task_id, task.parent_request_id, plan_id, task.worker.value, task.goal, task.focus,
         task.attempt, position, origin, reason),
    )


async def current_plan(db: aiosqlite.Connection, request_id: str) -> Optional[dict[str, Any]]:
    """The open plan of a request (awaiting approval or approved), else its latest one."""
    row = await fetch_one(
        db,
        """
        SELECT p.*, u.full_name AS approved_by_name FROM research_plans p
        LEFT JOIN users u ON u.id = p.approved_by
        WHERE p.request_id = ?
        ORDER BY CASE WHEN p.status IN ('awaiting_approval','approved') THEN 0 ELSE 1 END, p.version DESC
        LIMIT 1
        """,
        (request_id,),
    )
    if row:
        row["scope"] = loads(row["scope"], {})
    return row


async def plan_tasks(db: aiosqlite.Connection, plan_id: str, enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM research_tasks WHERE plan_id = ?"
    if enabled_only:
        sql += " AND enabled = 1"
    return await fetch_all(db, sql + " ORDER BY position, created_at", (plan_id,))


async def plan_contract(
    db: aiosqlite.Connection, request: BusinessRequest, plan_row: dict[str, Any], enabled_only: bool = True
) -> ResearchPlan:
    from edrak.metrics.planning import requirements_for

    tasks = await plan_tasks(db, plan_row["plan_id"], enabled_only=enabled_only)
    ctx = request.business_context
    competitors = [c.name for c in ctx.competitors] or list(ctx.targets)
    return ResearchPlan(
        plan_id=plan_row["plan_id"],
        request_id=request.request_id,
        rationale=plan_row.get("rationale"),
        created_at=plan_row["created_at"],
        tasks=[
            ResearchTask(
                task_id=t["task_id"],
                parent_request_id=request.request_id,
                worker=t["worker"],
                goal=t["goal"],
                focus=t["focus"],
                attempt=t["attempt"],
                company_profile=request.company_profile,
                business_context=request.business_context,
                requirements=requirements_for(
                    t["worker"].value if hasattr(t["worker"], "value") else str(t["worker"]),
                    ctx.use_case.value if hasattr(ctx.use_case, "value") else str(ctx.use_case),
                    request.company_profile.name,
                    competitors,
                    list(ctx.markets),
                    ctx.offering or "", *metric_set(request),
                ),
            )
            for t in tasks
        ],
    )


async def approve_plan(
    db: aiosqlite.Connection, plan_id: str, *, user_id: Optional[str], auto: bool = False, commit: bool = True
) -> bool:
    """Checkpoint 1. Moves the plan from awaiting_approval to approved exactly once."""
    cursor = await db.execute(
        """
        UPDATE research_plans SET status = 'approved', approved_at = ?, approved_by = ?, auto_approved = ?
        WHERE plan_id = ? AND status = 'awaiting_approval'
        """,
        (now_iso(), user_id, 1 if auto else 0, plan_id),
    )
    if commit:
        await db.commit()
    return cursor.rowcount == 1


async def reject_plan(db: aiosqlite.Connection, plan_id: str, *, user_id: Optional[str], reason: str) -> bool:
    cursor = await db.execute(
        """
        UPDATE research_plans SET status = 'rejected', rejected_at = ?, rejected_by = ?, rejection_reason = ?
        WHERE plan_id = ? AND status = 'awaiting_approval'
        """,
        (now_iso(), user_id, reason or None, plan_id),
    )
    await db.commit()
    return cursor.rowcount == 1


async def update_task_fields(db: aiosqlite.Connection, task_id: str, **fields: Any) -> None:
    allowed = {"goal", "focus", "enabled", "attempt", "state", "progress", "sources", "status_line", "error", "reason", "origin"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"cannot update research_tasks.{key}")
        sets.append(f"{key} = ?")
        params.append(value)
    if sets:
        params.append(task_id)
        await db.execute(f"UPDATE research_tasks SET {', '.join(sets)} WHERE task_id = ?", params)


async def task_ids(db: aiosqlite.Connection, plan_id: str) -> set[str]:
    rows = await fetch_all(db, "SELECT task_id FROM research_tasks WHERE plan_id = ?", (plan_id,))
    return {r["task_id"] for r in rows}


# ------------------------------------------------------------------ intake questions


async def save_questions(db: aiosqlite.Connection, request_id: str, questions: Iterable[dict[str, Any]]) -> None:
    await db.execute("DELETE FROM analysis_questions WHERE request_id = ?", (request_id,))
    for ordinal, q in enumerate(questions, start=1):
        await db.execute(
            "INSERT INTO analysis_questions (request_id, ordinal, question, options) VALUES (?, ?, ?, ?)",
            (request_id, ordinal, q["question"], dumps(q.get("options") or [])),
        )
    await db.commit()


async def list_questions(db: aiosqlite.Connection, request_id: str) -> list[dict[str, Any]]:
    rows = await fetch_all(
        db, "SELECT * FROM analysis_questions WHERE request_id = ? ORDER BY ordinal", (request_id,)
    )
    for r in rows:
        r["options"] = loads(r["options"], [])
        r["skipped"] = bool(r["skipped"])
    return rows


async def answer_question(
    db: aiosqlite.Connection, request_id: str, ordinal: int, answer: Optional[str], skipped: bool = False
) -> None:
    await db.execute(
        "UPDATE analysis_questions SET answer = ?, skipped = ?, answered_at = ? WHERE request_id = ? AND ordinal = ?",
        (answer, 1 if skipped else 0, now_iso(), request_id, ordinal),
    )
    await db.commit()


async def link_documents(db: aiosqlite.Connection, request_id: str, document_ids: Iterable[str]) -> None:
    for doc_id in document_ids:
        await db.execute(
            "INSERT OR IGNORE INTO analysis_documents (request_id, document_id) VALUES (?, ?)", (request_id, doc_id)
        )
    await db.commit()
