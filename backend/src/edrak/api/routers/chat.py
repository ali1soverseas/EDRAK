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
from fastapi.responses import StreamingResponse
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
    card_key: Optional[str] = None       # with scope=brief: the chat is about one card


class MessageIn(BaseModel):
    content: str


@router.post("/threads")
async def create_thread(body: ThreadIn, session: SessionOut = Depends(get_current_session),
                        db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    tid = new_id()
    await db.execute(
        """INSERT INTO chat_threads (id, workspace_id, user_id, kind, scope, brief_id, entity_id, request_id, card_key)
           VALUES (?, ?, ?, 'ask_edrak', ?, ?, ?, ?, ?)""",
        (tid, session.workspace.workspace_id, session.user.user_id, "brief" if body.scope == "card" else body.scope, body.brief_id,
         body.entity_id, body.request_id, body.card_key if body.brief_id else None),
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
    if thread.get("brief_id"):
        question = await _guarded_question(db, session, body.content)
        answer: dict[str, Any] = {}
        request_id = ""
        async for event, request_id in _agent_result(db, thread, session, question):
            if event["type"] == "answer":
                answer = event
        mid = await _save_exchange(db, thread_id, request_id, question, answer)
        return {"message_id": mid, "content": answer["answer"], "cited": answer["cited"], "label": answer["label"],
                "metric_uids": answer["metric_uids"], "notes": answer["notes"], "proposal": answer.get("proposal")}
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


# ---------------------------------------------------------------- chat about one brief (brief-level and card-level)


async def _brief_context(db: aiosqlite.Connection, session: SessionOut, brief_id: str):
    from edrak.api.routers.briefs import merge_live
    from edrak.chat.agent import BriefContext
    from edrak.db.repositories import briefs as briefs_repo

    row = await briefs_repo.get_brief(db, brief_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    doc = dict(row["document"])
    await merge_live(db, brief_id, doc)
    return BriefContext(brief_id, doc), row


async def _guarded_question(db: aiosqlite.Connection, session: SessionOut, content: str) -> str:
    """A question inside a brief or card chat is about that brief by construction, so the off-topic model check is
    skipped there ("what is the main takeaway?" has no business words of its own). Injection, harm and PII checks stay."""
    verdict = check_query(content, use_model=False)
    await guardrails_repo.record_checks(db, workspace_id=session.workspace.workspace_id, stage="chat", checks=verdict.checks)
    if verdict.blocked:
        raise HTTPException(status_code=422, detail={"code": verdict.code, "message": verdict.reason})
    return verdict.text


async def _save_exchange(db: aiosqlite.Connection, thread_id: str, request_id: str, question: str, result: dict[str, Any]) -> str:
    await db.execute("INSERT INTO chat_messages (id, thread_id, role, content) VALUES (?, ?, 'user', ?)", (new_id(), thread_id, question))
    mid = new_id()
    meta = [{"kind": "meta", "label": result["label"], "metric_uids": result["metric_uids"], "notes": result["notes"], "proposal": result.get("proposal")}]
    await db.execute("INSERT INTO chat_messages (id, thread_id, role, content, cards) VALUES (?, ?, 'assistant', ?, ?)",
                     (mid, thread_id, result["answer"], dumps(meta)))
    for no in result["cited"]:
        row = await fetch_one(db, "SELECT uid FROM evidence WHERE request_id = ? AND display_no = ?", (request_id, no))
        if row:
            await db.execute("INSERT OR IGNORE INTO chat_message_evidence (message_id, evidence_uid) VALUES (?, ?)", (mid, row["uid"]))
    await db.execute("UPDATE chat_threads SET title = coalesce(title, ?), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?", (question[:80], thread_id))
    await db.commit()
    return mid


async def _agent_result(db: aiosqlite.Connection, thread: dict[str, Any], session: SessionOut, question: str):
    from edrak.chat.agent import run_agent

    ctx, row = await _brief_context(db, session, thread["brief_id"])
    history = await fetch_all(db, "SELECT role, content FROM chat_messages WHERE thread_id = ? ORDER BY created_at DESC LIMIT 6", (thread["id"],))
    async for event in run_agent(db, ctx, question, history=list(reversed(history)), card_key=thread.get("card_key")):
        yield event, row["request_id"]


@router.post("/threads/{thread_id}/stream")
async def stream_message(thread_id: str, body: MessageIn, session: SessionOut = Depends(get_current_session),
                         db: aiosqlite.Connection = Depends(get_db_conn)) -> StreamingResponse:
    """Server-sent events: `step` while the agent uses a tool, then one `answer`."""
    from edrak.api.streaming import format_sse_event

    thread = await _thread(db, session, thread_id)
    if not thread.get("brief_id"):
        raise HTTPException(status_code=400, detail="streaming_needs_a_brief_thread")
    question = await _guarded_question(db, session, body.content)

    async def events():
        try:
            async for event, request_id in _agent_result(db, thread, session, question):
                if event["type"] == "answer":
                    event["message_id"] = await _save_exchange(db, thread_id, request_id, question, event)
                yield format_sse_event(event["type"], event)
        except Exception:  # noqa: BLE001
            yield format_sse_event("error", {"message": "Ask Edrak could not answer right now."})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
