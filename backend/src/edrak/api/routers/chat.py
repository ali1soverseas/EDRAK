"""Ask Edrak: questions about the workspace's briefs, findings, sources and documents.

The answer is written only from what Edrak already holds (briefs, findings, evidence, indexed
documents), with E-number citations. The question goes through the same guardrail as an analysis.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import SessionOut
from edrak.core.config import settings
from edrak.db.repositories import guardrails as guardrails_repo
from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id
from edrak.guardrails.content import UNTRUSTED_RULE, sanitize
from edrak.guardrails.query import check_query

router = APIRouter(prefix="/chat", tags=["ask-edrak"])

SYSTEM = (
    "You are Ask Edrak. Answer the person's question using only the CONTEXT: findings and sources from their "
    "workspace's analyses and their documents. Cite sources as [E12] using the E-numbers given, or [D] for a "
    "document passage. If the context does not answer the question, say what is missing and suggest running an "
    "analysis. Never recommend a decision; describe what the evidence shows. Answer in the language of the question. "
    + UNTRUSTED_RULE
)


class ThreadIn(BaseModel):
    scope: str = "workspace"          # workspace | brief | entity | analysis
    brief_id: Optional[str] = None
    entity_id: Optional[str] = None
    request_id: Optional[str] = None


class MessageIn(BaseModel):
    content: str


@router.post("/threads")
async def create_thread(body: ThreadIn, session: SessionOut = Depends(get_current_session),
                        db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    tid = new_id()
    await db.execute(
        """INSERT INTO chat_threads (id, workspace_id, user_id, kind, scope, brief_id, entity_id, request_id)
           VALUES (?, ?, ?, 'ask_edrak', ?, ?, ?, ?)""",
        (tid, session.workspace.workspace_id, session.user.user_id, body.scope, body.brief_id, body.entity_id, body.request_id),
    )
    await db.commit()
    return {"thread_id": tid}


@router.get("/threads")
async def list_threads(session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)):
    return await fetch_all(db, "SELECT * FROM chat_threads WHERE workspace_id = ? AND user_id = ? ORDER BY updated_at DESC LIMIT 30",
                           (session.workspace.workspace_id, session.user.user_id))


async def _thread(db, session, thread_id):
    t = await fetch_one(db, "SELECT * FROM chat_threads WHERE id = ? AND workspace_id = ? AND user_id = ?",
                        (thread_id, session.workspace.workspace_id, session.user.user_id))
    if not t:
        raise HTTPException(status_code=404, detail="not_found")
    return t


@router.get("/threads/{thread_id}/messages")
async def messages(thread_id: str, session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)):
    await _thread(db, session, thread_id)
    rows = await fetch_all(db, "SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY created_at", (thread_id,))
    for r in rows:
        r["cards"] = loads(r["cards"], [])
    return rows


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w؀-ۿ]{4,}", text.lower())][:8]


async def _context(db, ws: str, thread: dict[str, Any], question: str) -> tuple[str, list[str]]:
    """Evidence and findings that share words with the question, scoped to the thread."""
    scope_sql, params = "b.workspace_id = ?", [ws]
    if thread.get("brief_id"):
        scope_sql += " AND b.id = ?"
        params.append(thread["brief_id"])
    terms = _terms(question) or [question.lower()[:20]]
    like = " OR ".join(["lower(e.extracted_fact) LIKE ?" for _ in terms])
    rows = await fetch_all(
        db,
        f"""SELECT e.uid, e.display_no, e.extracted_fact, e.source_title, b.number AS brief_no FROM evidence e
            JOIN briefs b ON b.request_id = e.request_id WHERE {scope_sql} AND ({like})
            ORDER BY b.generated_at DESC LIMIT 12""",
        [*params, *[f"%{t}%" for t in terms]],
    )
    findings = await fetch_all(
        db,
        f"""SELECT f.statement, b.number AS brief_no FROM findings f JOIN briefs b ON b.request_id = f.request_id
            WHERE {scope_sql} AND ({' OR '.join(['lower(f.statement) LIKE ?' for _ in terms])}) LIMIT 10""",
        [*params, *[f"%{t}%" for t in terms]],
    )

    def docs() -> list[str]:
        try:
            from edrak.rag.retriever import InternalRetriever

            return [e.excerpt or e.extracted_fact for e in InternalRetriever().retrieve(question, top_k=3, workspace_id=ws)]
        except Exception:  # noqa: BLE001 - documents are optional context
            return []

    passages = await asyncio.to_thread(docs)
    lines = [f"[E{r['display_no']}] (brief {r['brief_no']:04d}) {sanitize(r['extracted_fact'])[0][:500]}" for r in rows]
    lines += [f"- Finding (brief {f['brief_no']:04d}): {f['statement'][:300]}" for f in findings]
    lines += [f"[D] {sanitize(p)[0][:600]}" for p in passages if p]
    return "\n".join(lines) or "(nothing in the workspace matches this question yet)", [r["uid"] for r in rows]


@router.post("/threads/{thread_id}/messages")
async def ask(thread_id: str, body: MessageIn, session: SessionOut = Depends(get_current_session),
              db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    thread = await _thread(db, session, thread_id)
    ws = session.workspace.workspace_id
    verdict = check_query(body.content, use_model=settings.GUARDRAIL_SCOPE_CHECK)
    await guardrails_repo.record_checks(db, workspace_id=ws, stage="chat", checks=verdict.checks)
    if verdict.blocked:
        raise HTTPException(status_code=422, detail={"code": verdict.code, "message": verdict.reason})
    question = verdict.text
    history = await fetch_all(db, "SELECT role, content FROM chat_messages WHERE thread_id = ? ORDER BY created_at DESC LIMIT 6", (thread_id,))
    context, evidence_uids = await _context(db, ws, thread, question)

    def answer() -> str:
        from edrak.core.llm import get_chat_model

        convo = "\n".join(f"{h['role']}: {h['content'][:600]}" for h in reversed(history))
        prompt = f"{SYSTEM}\n\nCONTEXT:\n{context}\n\nCONVERSATION SO FAR:\n{convo}\n\nQUESTION: {question}"
        reply = get_chat_model(agent="orchestrator", temperature=0.2).invoke(prompt)
        return str(getattr(reply, "content", reply)).strip()

    try:
        text = await asyncio.to_thread(answer)
    except Exception:  # noqa: BLE001
        text = "Ask Edrak cannot reach the language model right now. Try again in a moment."
    uid_in = new_id()
    await db.execute("INSERT INTO chat_messages (id, thread_id, role, content) VALUES (?, ?, 'user', ?)", (uid_in, thread_id, question))
    mid = new_id()
    cited = {int(n) for n in re.findall(r"\[E(\d+)\]", text)}
    await db.execute("INSERT INTO chat_messages (id, thread_id, role, content, cards) VALUES (?, ?, 'assistant', ?, ?)",
                     (mid, thread_id, text, dumps([])))
    rows = await fetch_all(db, f"SELECT uid, display_no FROM evidence WHERE uid IN ({','.join('?' * len(evidence_uids)) or 'NULL'})", evidence_uids)
    for r in rows:
        if r["display_no"] in cited:
            await db.execute("INSERT OR IGNORE INTO chat_message_evidence (message_id, evidence_uid) VALUES (?, ?)", (mid, r["uid"]))
    await db.execute("UPDATE chat_threads SET title = coalesce(title, ?) WHERE id = ?", (question[:80], thread_id))
    await db.commit()
    return {"message_id": mid, "content": text, "cited": sorted(cited)}
