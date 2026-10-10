"""Repository functions for EDRAK database operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite


def now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# Users & Workspaces
# ----------------------------------------------------------------------


async def get_user_by_id(db: aiosqlite.Connection, user_id: str) -> Optional[dict[str, Any]]:
    cursor = await db.execute("SELECT * FROM users WHERE user_id = ?;", (user_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_user_by_email(db: aiosqlite.Connection, email: str) -> Optional[dict[str, Any]]:
    cursor = await db.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?);", (email.strip(),))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def create_workspace(db: aiosqlite.Connection, workspace_id: str, name: str) -> dict[str, Any]:
    await db.execute(
        "INSERT INTO workspaces (workspace_id, name) VALUES (?, ?);",
        (workspace_id, name),
    )
    await db.commit()
    return {"workspace_id": workspace_id, "name": name}


async def get_workspace(db: aiosqlite.Connection, workspace_id: str) -> Optional[dict[str, Any]]:
    cursor = await db.execute("SELECT * FROM workspaces WHERE workspace_id = ?;", (workspace_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def create_user(
    db: aiosqlite.Connection,
    user_id: str,
    email: str,
    password_hash: str,
    name: str,
    title: Optional[str],
    workspace_id: str,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO users (user_id, email, password_hash, name, title, workspace_id)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (user_id, email.strip().lower(), password_hash, name, title, workspace_id),
    )
    await db.commit()
    return {
        "user_id": user_id,
        "email": email.strip().lower(),
        "name": name,
        "title": title,
        "workspace_id": workspace_id,
    }


# ----------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------


async def create_session(db: aiosqlite.Connection, token: str, user_id: str) -> None:
    await db.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?);",
        (token, user_id, now_iso()),
    )
    await db.commit()


async def get_session_user(
    db: aiosqlite.Connection, token: str
) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    cursor = await db.execute(
        """
        SELECT u.*, w.name as workspace_name
        FROM sessions s
        JOIN users u ON s.user_id = u.user_id
        JOIN workspaces w ON u.workspace_id = w.workspace_id
        WHERE s.token = ?;
        """,
        (token,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    data = dict(row)
    user = {
        "user_id": data["user_id"],
        "email": data["email"],
        "name": data["name"],
        "title": data["title"],
        "workspace_id": data["workspace_id"],
    }
    workspace = {
        "workspace_id": data["workspace_id"],
        "name": data["workspace_name"],
    }
    return user, workspace


async def delete_session(db: aiosqlite.Connection, token: str) -> None:
    await db.execute("DELETE FROM sessions WHERE token = ?;", (token,))
    await db.commit()


# ----------------------------------------------------------------------
# Company Setup
# ----------------------------------------------------------------------


async def get_company_profile(db: aiosqlite.Connection, workspace_id: str) -> Optional[dict[str, Any]]:
    cursor = await db.execute(
        "SELECT fields_json, completed FROM company_profiles WHERE workspace_id = ?;",
        (workspace_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    fields = json.loads(row["fields_json"])
    fields["completed"] = bool(row["completed"])
    return fields


async def save_company_profile(
    db: aiosqlite.Connection,
    workspace_id: str,
    fields_dict: dict[str, Any],
    completed: bool = True,
) -> None:
    fields_json = json.dumps(fields_dict)
    await db.execute(
        """
        INSERT INTO company_profiles (workspace_id, fields_json, completed)
        VALUES (?, ?, ?)
        ON CONFLICT(workspace_id) DO UPDATE SET
            fields_json = excluded.fields_json,
            completed = excluded.completed;
        """,
        (workspace_id, fields_json, 1 if completed else 0),
    )
    await db.commit()


async def add_company_document(
    db: aiosqlite.Connection,
    document_id: str,
    workspace_id: str,
    filename: str,
    size_dict: Optional[dict[str, Any]],
    status: str = "indexing",
) -> dict[str, Any]:
    created_at = now_iso()
    size_json = json.dumps(size_dict) if size_dict else None
    await db.execute(
        """
        INSERT INTO company_documents (document_id, workspace_id, filename, size_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (document_id, workspace_id, filename, size_json, status, created_at),
    )
    await db.commit()
    return {
        "document_id": document_id,
        "filename": filename,
        "size": size_dict,
        "status": status,
    }


async def list_company_documents(db: aiosqlite.Connection, workspace_id: str) -> list[dict[str, Any]]:
    cursor = await db.execute(
        "SELECT document_id, filename, size_json, status FROM company_documents WHERE workspace_id = ? ORDER BY created_at DESC;",
        (workspace_id,),
    )
    rows = await cursor.fetchall()
    results = []
    for r in rows:
        size = json.loads(r["size_json"]) if r["size_json"] else None
        results.append(
            {
                "document_id": r["document_id"],
                "filename": r["filename"],
                "size": size,
                "status": r["status"],
            }
        )
    return results


async def update_company_document_status(
    db: aiosqlite.Connection,
    document_id: str,
    status: str,
    size_dict: Optional[dict[str, Any]] = None,
) -> None:
    if size_dict is not None:
        size_json = json.dumps(size_dict)
        await db.execute(
            "UPDATE company_documents SET status = ?, size_json = ? WHERE document_id = ?;",
            (status, size_json, document_id),
        )
    else:
        await db.execute(
            "UPDATE company_documents SET status = ? WHERE document_id = ?;",
            (status, document_id),
        )
    await db.commit()


async def delete_company_document(
    db: aiosqlite.Connection, document_id: str, workspace_id: str
) -> bool:
    cursor = await db.execute(
        "DELETE FROM company_documents WHERE document_id = ? AND workspace_id = ?;",
        (document_id, workspace_id),
    )
    await db.commit()
    return cursor.rowcount > 0


# ----------------------------------------------------------------------
# Analyses
# ----------------------------------------------------------------------


async def list_analyses(db: aiosqlite.Connection, workspace_id: str) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT a.analysis_id, a.title, a.status, a.form_json, a.updated_at,
               a.brief_no, b.brief_id, r.state as run_state, r.result_json
        FROM analyses a
        LEFT JOIN runs r ON a.analysis_id = r.analysis_id
        LEFT JOIN briefs b ON a.analysis_id = b.analysis_id
        WHERE a.workspace_id = ?
        ORDER BY a.updated_at DESC;
        """,
        (workspace_id,),
    )
    rows = await cursor.fetchall()
    summaries = []
    for r in rows:
        form = json.loads(r["form_json"])
        use_case = form.get("use_case", "competitive_intelligence")
        repeat_weekly = bool(form.get("repeat_weekly", False))

        # Check tasks counts and verification if run/result exists
        tasks_total = 0
        tasks_done = 0
        tasks_failed = 0
        verified = False

        if r["result_json"]:
            try:
                res = json.loads(r["result_json"])
                worker_results = res.get("results", {})
                tasks_total = len(worker_results)
                for wr in worker_results.values():
                    if wr.get("status") == "success":
                        tasks_done += 1
                    else:
                        tasks_failed += 1
                ver = res.get("verification")
                if ver and ver.get("status") == "passed":
                    verified = True
            except Exception:
                pass

        summaries.append(
            {
                "analysis_id": r["analysis_id"],
                "title": r["title"],
                "use_case": use_case,
                "status": r["status"],
                "updated_at": r["updated_at"],
                "repeat_weekly": repeat_weekly,
                "tasks_total": tasks_total,
                "tasks_done": tasks_done,
                "tasks_failed": tasks_failed,
                "brief_id": r["brief_id"],
                "brief_no": r["brief_no"],
                "verified": verified,
            }
        )
    return summaries


async def get_analysis(
    db: aiosqlite.Connection, analysis_id: str, workspace_id: Optional[str] = None
) -> Optional[dict[str, Any]]:
    query = "SELECT * FROM analyses WHERE analysis_id = ?"
    params: list[Any] = [analysis_id]
    if workspace_id:
        query += " AND workspace_id = ?"
        params.append(workspace_id)
    query += ";"

    cursor = await db.execute(query, tuple(params))
    row = await cursor.fetchone()
    if not row:
        return None
    return dict(row)


async def create_or_update_analysis(
    db: aiosqlite.Connection,
    analysis_id: str,
    workspace_id: str,
    title: str,
    status: str,
    form_dict: dict[str, Any],
    request_dict: dict[str, Any],
    plan_dict: Optional[dict[str, Any]] = None,
    rejection_reason: Optional[str] = None,
    brief_no: Optional[int] = None,
) -> dict[str, Any]:
    now = now_iso()
    form_json = json.dumps(form_dict)
    request_json = json.dumps(request_dict)
    plan_json = json.dumps(plan_dict) if plan_dict else None

    cursor = await db.execute("SELECT created_at FROM analyses WHERE analysis_id = ?;", (analysis_id,))
    existing = await cursor.fetchone()
    created_at = existing["created_at"] if existing else now

    await db.execute(
        """
        INSERT INTO analyses (
            analysis_id, workspace_id, title, status, form_json, request_json,
            plan_json, rejection_reason, brief_no, updated_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(analysis_id) DO UPDATE SET
            title = excluded.title,
            status = excluded.status,
            form_json = excluded.form_json,
            request_json = excluded.request_json,
            plan_json = excluded.plan_json,
            rejection_reason = excluded.rejection_reason,
            brief_no = excluded.brief_no,
            updated_at = excluded.updated_at;
        """,
        (
            analysis_id,
            workspace_id,
            title,
            status,
            form_json,
            request_json,
            plan_json,
            rejection_reason,
            brief_no,
            now,
            created_at,
        ),
    )
    await db.commit()
    return {
        "analysis_id": analysis_id,
        "workspace_id": workspace_id,
        "title": title,
        "status": status,
        "form": form_dict,
        "request": request_dict,
        "plan": plan_dict,
        "rejection_reason": rejection_reason,
        "brief_no": brief_no,
        "updated_at": now,
        "created_at": created_at,
    }


async def update_analysis_status(
    db: aiosqlite.Connection,
    analysis_id: str,
    status: str,
    rejection_reason: Optional[str] = None,
    brief_no: Optional[int] = None,
) -> None:
    now = now_iso()
    if rejection_reason is not None:
        await db.execute(
            """
            UPDATE analyses
            SET status = ?, rejection_reason = ?, updated_at = ?
            WHERE analysis_id = ?;
            """,
            (status, rejection_reason, now, analysis_id),
        )
    elif brief_no is not None:
        await db.execute(
            """
            UPDATE analyses
            SET status = ?, brief_no = ?, updated_at = ?
            WHERE analysis_id = ?;
            """,
            (status, brief_no, now, analysis_id),
        )
    else:
        await db.execute(
            """
            UPDATE analyses
            SET status = ?, updated_at = ?
            WHERE analysis_id = ?;
            """,
            (status, now, analysis_id),
        )
    await db.commit()


# ----------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------


async def get_run(db: aiosqlite.Connection, analysis_id: str) -> Optional[dict[str, Any]]:
    cursor = await db.execute("SELECT * FROM runs WHERE analysis_id = ?;", (analysis_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def create_or_replace_run(
    db: aiosqlite.Connection,
    analysis_id: str,
    approved_at: str,
    approved_by: str,
    started_at: Optional[str] = None,
    state: str = "running",
    stage: str = "plan",
) -> None:
    await db.execute(
        """
        INSERT INTO runs (analysis_id, approved_at, approved_by, started_at, state, stage)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(analysis_id) DO UPDATE SET
            approved_at = excluded.approved_at,
            approved_by = excluded.approved_by,
            started_at = excluded.started_at,
            state = excluded.state,
            stage = excluded.stage,
            finished_at = NULL,
            result_json = NULL,
            cancelled_at = NULL;
        """,
        (analysis_id, approved_at, approved_by, started_at, state, stage),
    )
    await db.commit()


async def update_run(
    db: aiosqlite.Connection,
    analysis_id: str,
    state: Optional[str] = None,
    stage: Optional[str] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    result_dict: Optional[dict[str, Any]] = None,
    cancelled_at: Optional[str] = None,
) -> None:
    fields = []
    params: list[Any] = []
    if state is not None:
        fields.append("state = ?")
        params.append(state)
    if stage is not None:
        fields.append("stage = ?")
        params.append(stage)
    if started_at is not None:
        fields.append("started_at = ?")
        params.append(started_at)
    if finished_at is not None:
        fields.append("finished_at = ?")
        params.append(finished_at)
    if result_dict is not None:
        fields.append("result_json = ?")
        params.append(json.dumps(result_dict))
    if cancelled_at is not None:
        fields.append("cancelled_at = ?")
        params.append(cancelled_at)

    if not fields:
        return

    params.append(analysis_id)
    query = f"UPDATE runs SET {', '.join(fields)} WHERE analysis_id = ?;"
    await db.execute(query, tuple(params))
    await db.commit()


# ----------------------------------------------------------------------
# Briefs
# ----------------------------------------------------------------------


async def get_next_brief_no(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT MAX(brief_no) as max_no FROM briefs;")
    row = await cursor.fetchone()
    if row and row["max_no"]:
        return int(row["max_no"]) + 1
    return 1


async def save_brief(
    db: aiosqlite.Connection,
    brief_id: str,
    analysis_id: str,
    brief_no: int,
    brief_dict: dict[str, Any],
) -> None:
    now = now_iso()
    brief_json = json.dumps(brief_dict)
    await db.execute(
        """
        INSERT INTO briefs (brief_id, analysis_id, brief_no, brief_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(brief_id) DO UPDATE SET
            brief_json = excluded.brief_json,
            brief_no = excluded.brief_no;
        """,
        (brief_id, analysis_id, brief_no, brief_json, now),
    )
    await db.commit()


async def get_brief(db: aiosqlite.Connection, brief_id: str) -> Optional[dict[str, Any]]:
    cursor = await db.execute("SELECT brief_json FROM briefs WHERE brief_id = ?;", (brief_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    return json.loads(row["brief_json"])


async def get_brief_by_analysis(db: aiosqlite.Connection, analysis_id: str) -> Optional[dict[str, Any]]:
    cursor = await db.execute(
        "SELECT brief_json FROM briefs WHERE analysis_id = ? ORDER BY brief_no DESC LIMIT 1;",
        (analysis_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return json.loads(row["brief_json"])
