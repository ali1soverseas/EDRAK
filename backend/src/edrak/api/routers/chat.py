"""Ask Edrak: a conversational assistant over the workspace, a brief, a card or a signal.

Answers come from what Edrak holds, in order of trust: the brief (in a brief chat), the workspace's other
briefs, watchlist and alerts, the company profile and approved company facts, uploaded documents, and
only then the web (always marked unverified). So it can answer before any analysis has run.

Every message goes through guardrails.chat_input (small talk, rate limit, injection, harm, off-topic,
personal data) and every answer through guardrails.chat.check_chat_answer (citations, numbers, labels,
verdict wording, personal data, prompt leaks). Each exchange is stored with its sources, tool trace,
guardrail notes and latency, and can be rated.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, AsyncIterator, Literal, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import SessionOut
from edrak.core.config import settings
from edrak.db.repositories import guardrails as guardrails_repo
from edrak.db.util import dumps, fetch_all, fetch_one, loads, new_id
from edrak.guardrails.chat_input import check_chat_message, small_talk_reply
from edrak.guardrails.query import Check

router = APIRouter(prefix="/chat", tags=["ask-edrak"])
logger = logging.getLogger(__name__)

HISTORY_LIMIT = 24          # messages read back for the agent (it quotes the last 8 and lists the earlier questions)
DELTA_WORDS = 3             # words per streamed chunk of the checked answer


class ThreadIn(BaseModel):
    scope: Literal["workspace", "brief", "card", "signal", "entity", "analysis"] = "workspace"
    brief_id: Optional[str] = None
    entity_id: Optional[str] = None
    request_id: Optional[str] = None
    card_key: Optional[str] = None       # with a brief: the chat is about one card
    signal_id: Optional[str] = None      # with a brief: the chat is about one signal


class MessageIn(BaseModel):
    content: str = Field(default="", max_length=8000)
    regenerate: bool = False             # answer the last question again (replaces the last answer)


class ThreadPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class FeedbackIn(BaseModel):
    rating: Literal["up", "down"]
    reason: Optional[str] = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------------------------- threads


@router.post("/threads")
async def create_thread(body: ThreadIn, session: SessionOut = Depends(get_current_session),
                        db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    ws = session.workspace.workspace_id
    if body.scope in ("brief", "card", "signal") and not body.brief_id:
        raise HTTPException(status_code=422, detail="brief_id_required")
    if body.brief_id:
        from edrak.db.repositories import briefs as briefs_repo

        if not await briefs_repo.get_brief(db, body.brief_id, ws):
            raise HTTPException(status_code=404, detail="not_found")
    tid = new_id()
    scope = "brief" if body.brief_id else body.scope
    await db.execute(
        """INSERT INTO chat_threads (id, workspace_id, user_id, kind, scope, brief_id, entity_id, request_id, card_key, signal_id)
           VALUES (?, ?, ?, 'ask_edrak', ?, ?, ?, ?, ?, ?)""",
        (tid, ws, session.user.user_id, scope, body.brief_id, body.entity_id, body.request_id,
         body.card_key if body.brief_id else None, body.signal_id if body.brief_id and not body.card_key else None),
    )
    await db.commit()
    return {"thread_id": tid}


@router.get("/threads")
async def list_threads(scope: Optional[Literal["workspace", "brief"]] = None, brief_id: Optional[str] = None,
                       limit: int = Query(default=30, ge=1, le=100),
                       session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)):
    """The person's conversations, newest first, with the last question and a message count. Empty threads are left out."""
    where, params = ["t.workspace_id = ?", "t.user_id = ?", "t.kind = 'ask_edrak'"], [session.workspace.workspace_id, session.user.user_id]
    if brief_id:
        where.append("t.brief_id = ?")
        params.append(brief_id)
    elif scope == "workspace":
        where.append("t.brief_id IS NULL")
    elif scope == "brief":
        where.append("t.brief_id IS NOT NULL")
    return await fetch_all(
        db,
        f"""SELECT t.id, t.scope, t.brief_id, t.card_key, t.signal_id, t.title, t.created_at, t.updated_at,
                   (SELECT count(*) FROM chat_messages m WHERE m.thread_id = t.id) AS message_count,
                   (SELECT m.content FROM chat_messages m WHERE m.thread_id = t.id AND m.role = 'user'
                    ORDER BY m.created_at DESC LIMIT 1) AS last_question
            FROM chat_threads t WHERE {' AND '.join(where)}
              AND EXISTS (SELECT 1 FROM chat_messages m WHERE m.thread_id = t.id)
            ORDER BY t.updated_at DESC LIMIT ?""",
        (*params, limit),
    )


async def _thread(db, session, thread_id):
    t = await fetch_one(db, "SELECT * FROM chat_threads WHERE id = ? AND workspace_id = ? AND user_id = ?",
                        (thread_id, session.workspace.workspace_id, session.user.user_id))
    if not t:
        raise HTTPException(status_code=404, detail="not_found")
    return t


@router.patch("/threads/{thread_id}")
async def rename_thread(thread_id: str, body: ThreadPatch, session: SessionOut = Depends(get_current_session),
                        db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    await _thread(db, session, thread_id)
    await db.execute("UPDATE chat_threads SET title = ? WHERE id = ?", (body.title.strip(), thread_id))
    await db.commit()
    return {"thread_id": thread_id, "title": body.title.strip()}


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, session: SessionOut = Depends(get_current_session),
                        db: aiosqlite.Connection = Depends(get_db_conn)) -> None:
    await _thread(db, session, thread_id)
    await db.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
    await db.commit()


@router.get("/threads/{thread_id}/messages")
async def messages(thread_id: str, session: SessionOut = Depends(get_current_session), db: aiosqlite.Connection = Depends(get_db_conn)):
    """The conversation, with each answer's label, sources, follow-ups, notes and the person's rating."""
    thread = await _thread(db, session, thread_id)
    rows = await fetch_all(
        db, """SELECT m.id, m.role, m.content, m.cards, m.created_at, f.rating
               FROM chat_messages m LEFT JOIN chat_feedback f ON f.message_id = m.id
               WHERE m.thread_id = ? ORDER BY m.created_at, m.rowid""", (thread_id,))
    out = []
    for r in rows:
        cards = loads(r["cards"], [])
        meta = next((c for c in cards if isinstance(c, dict) and c.get("kind") == "meta"), {})
        out.append({"id": r["id"], "role": r["role"], "content": r["content"], "created_at": r["created_at"], "rating": r.get("rating"),
                    "label": meta.get("label"), "kinds": meta.get("kinds") or [], "sources": meta.get("sources") or [],
                    "follow_ups": meta.get("follow_ups") or [], "notes": meta.get("notes") or [], "proposal": meta.get("proposal")})
    return {"thread": {k: thread.get(k) for k in ("id", "scope", "brief_id", "card_key", "signal_id", "title")}, "messages": out}


@router.post("/messages/{message_id}/feedback")
async def feedback(message_id: str, body: FeedbackIn, session: SessionOut = Depends(get_current_session),
                   db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    """Thumbs up or down on an answer, with an optional reason. Rating again replaces the earlier rating."""
    row = await fetch_one(
        db, """SELECT m.id FROM chat_messages m JOIN chat_threads t ON t.id = m.thread_id
               WHERE m.id = ? AND m.role = 'assistant' AND t.workspace_id = ? AND t.user_id = ?""",
        (message_id, session.workspace.workspace_id, session.user.user_id))
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    await db.execute(
        """INSERT INTO chat_feedback (message_id, user_id, rating, reason) VALUES (?, ?, ?, ?)
           ON CONFLICT(message_id) DO UPDATE SET rating = excluded.rating, reason = excluded.reason,
                                                 created_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
        (message_id, session.user.user_id, body.rating, (body.reason or "").strip() or None))
    await db.commit()
    return {"message_id": message_id, "rating": body.rating}


# ---------------------------------------------------------------------------------------------- answering


def _scope_kind(thread: dict[str, Any]) -> str:
    if not thread.get("brief_id"):
        return "workspace"
    return "card" if thread.get("card_key") else "signal" if thread.get("signal_id") else "brief"


async def _brief_context(db: aiosqlite.Connection, session: SessionOut, brief_id: str):
    from edrak.api.routers.briefs import merge_live
    from edrak.chat.agent import BriefContext
    from edrak.db.repositories import briefs as briefs_repo

    row = await briefs_repo.get_brief(db, brief_id, session.workspace.workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    doc = dict(row["document"])
    doc.setdefault("title", row.get("title"))
    doc.setdefault("summary", row.get("summary"))
    await merge_live(db, brief_id, doc)
    return BriefContext(brief_id, doc), row


def _checks_from(items: list[dict[str, Any]]) -> list[Check]:
    return [Check(c["name"], "medium" if c.get("action") in ("blocked", "redacted") else "info", c.get("action", "allowed"),
                  {k: v for k, v in c.items() if k not in ("name", "action")}) for c in items]


async def _save_exchange(db: aiosqlite.Connection, thread: dict[str, Any], request_id: Optional[str], question: str,
                         result: dict[str, Any]) -> str:
    thread_id = thread["id"]
    await db.execute("INSERT INTO chat_messages (id, thread_id, role, content) VALUES (?, ?, 'user', ?)", (new_id(), thread_id, question))
    mid = new_id()
    meta = {"kind": "meta", **{k: result.get(k) for k in ("label", "kinds", "metric_uids", "sources", "follow_ups", "notes", "proposal",
                                                         "steps", "latency_ms")},
            "guardrail": [c.get("name") for c in result.get("checks") or []]}
    await db.execute("INSERT INTO chat_messages (id, thread_id, role, content, cards) VALUES (?, ?, 'assistant', ?, ?)",
                     (mid, thread_id, result["answer"], dumps([meta])))
    # Link cited evidence, so "where was this source used" can find chat answers too.
    if request_id:
        for no in result.get("cited") or []:
            row = await fetch_one(db, "SELECT uid FROM evidence WHERE request_id = ? AND display_no = ?", (request_id, no))
            if row:
                await db.execute("INSERT OR IGNORE INTO chat_message_evidence (message_id, evidence_uid) VALUES (?, ?)", (mid, row["uid"]))
    for s in result.get("sources") or []:
        if s.get("kind") == "workspace" and s.get("brief_id") and s.get("display_no") is not None:
            row = await fetch_one(db, """SELECT e.uid FROM evidence e JOIN briefs b ON b.request_id = e.request_id
                                         WHERE b.id = ? AND e.display_no = ?""", (s["brief_id"], s["display_no"]))
            if row:
                await db.execute("INSERT OR IGNORE INTO chat_message_evidence (message_id, evidence_uid) VALUES (?, ?)", (mid, row["uid"]))
    title = re.sub(r"\s+", " ", question).strip()[:80]
    await db.execute("UPDATE chat_threads SET title = coalesce(title, ?), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
                     (title, thread_id))
    await db.commit()
    return mid


def _chunks(text: str) -> list[str]:
    words = re.findall(r"\S+\s*", text)
    lead = text[: len(text) - len(text.lstrip())]
    pieces = ["".join(words[i:i + DELTA_WORDS]) for i in range(0, len(words), DELTA_WORDS)]
    if pieces and lead:
        pieces[0] = lead + pieces[0]
    return pieces or [text]


def _empty_result(answer: str, label: str, follow_ups: Optional[list[str]] = None) -> dict[str, Any]:
    return {"answer": answer, "label": label, "kinds": [], "cited": [], "metric_uids": [], "sources": [], "notes": [],
            "proposal": None, "follow_ups": follow_ups or [], "steps": [], "checks": []}


async def _respond(db: aiosqlite.Connection, session: SessionOut, thread: dict[str, Any], body: MessageIn,
                   *, stream: bool) -> AsyncIterator[dict[str, Any]]:
    """The whole turn: guard the message, answer it, check the answer, store it. Yields step, delta, answer or error events."""
    from edrak.chat.agent import run_agent
    from edrak.chat.tools import load_workspace

    started = time.monotonic()
    ws_id, user_id = session.workspace.workspace_id, session.user.user_id
    content = (body.content or "").strip()

    if body.regenerate:
        last = await fetch_one(db, "SELECT content, created_at FROM chat_messages WHERE thread_id = ? AND role = 'user' "
                                   "ORDER BY created_at DESC, rowid DESC LIMIT 1", (thread["id"],))
        if not last:
            yield {"type": "error", "code": "nothing_to_regenerate", "message": "There is no question to answer again."}
            return
        content = last["content"]
        await db.execute("DELETE FROM chat_messages WHERE thread_id = ? AND created_at >= ?", (thread["id"], last["created_at"]))
        await db.commit()
    if not content:
        yield {"type": "error", "code": "empty", "message": "Type a question first."}
        return

    scope = _scope_kind(thread)
    verdict = check_chat_message(content, scope=scope, use_model=scope == "workspace" and bool(settings.GUARDRAIL_SCOPE_CHECK),
                                 rate_key=f"{ws_id}:{user_id}")
    await guardrails_repo.record_checks(db, workspace_id=ws_id, stage="chat", checks=verdict.checks)
    question = verdict.text
    request_id: Optional[str] = None

    if verdict.action == "declined" and verdict.kind == "rate_limited":
        yield {"type": "error", "code": "rate_limited", "message": verdict.reply}
        return

    if verdict.action == "small_talk":
        ws = await load_workspace(db, ws_id)
        answer, follow = small_talk_reply(verdict.kind or "greeting", verdict.lang, scope=scope, company=ws.company_name,
                                          has_briefs=bool(ws.brief_count) or scope != "workspace")
        result = _empty_result(answer, "conversation", follow)
    elif verdict.action == "declined":
        result = _empty_result(verdict.reply or "", "declined")
    else:
        ctx = None
        if thread.get("brief_id"):
            ctx, row = await _brief_context(db, session, thread["brief_id"])
            request_id = row["request_id"]
        workspace = await load_workspace(db, ws_id, exclude_brief_id=thread.get("brief_id"))
        history = list(reversed(await fetch_all(
            db, "SELECT role, content FROM chat_messages WHERE thread_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (thread["id"], HISTORY_LIMIT))))
        result = {}
        async for event in run_agent(db, ctx, question, history=history, card_key=thread.get("card_key"),
                                     signal_id=thread.get("signal_id"), workspace=workspace):
            if event["type"] == "answer":
                result = {k: v for k, v in event.items() if k != "type"}
            else:
                yield event
        if not result:
            result = _empty_result("Ask Edrak could not answer right now. Try again in a moment.", "not_covered")
        if result.get("checks"):
            await guardrails_repo.record_checks(db, workspace_id=ws_id, stage="chat", checks=_checks_from(result["checks"]))

    if stream:
        for piece in _chunks(result["answer"]):
            yield {"type": "delta", "text": piece}
            await asyncio.sleep(0.012)
    result["latency_ms"] = int((time.monotonic() - started) * 1000)
    result["message_id"] = await _save_exchange(db, thread, request_id, question, result)
    result.pop("checks", None)
    yield {"type": "answer", **result}


@router.post("/threads/{thread_id}/messages")
async def ask(thread_id: str, body: MessageIn, session: SessionOut = Depends(get_current_session),
              db: aiosqlite.Connection = Depends(get_db_conn)) -> dict[str, Any]:
    """One answer, without streaming. Same pipeline as /stream."""
    thread = await _thread(db, session, thread_id)
    final: dict[str, Any] = {}
    async for event in _respond(db, session, thread, body, stream=False):
        if event["type"] == "error":
            status = 429 if event.get("code") == "rate_limited" else 422
            raise HTTPException(status_code=status, detail={"code": event.get("code"), "message": event.get("message")})
        if event["type"] == "answer":
            final = event
    final["content"] = final.get("answer", "")
    return final


@router.post("/threads/{thread_id}/stream")
async def stream_message(thread_id: str, body: MessageIn, session: SessionOut = Depends(get_current_session),
                         db: aiosqlite.Connection = Depends(get_db_conn)) -> StreamingResponse:
    """Server-sent events for any scope: `step` while a tool runs, `delta` chunks of the checked answer, then one `answer`.

    The answer is checked in full before any of it is sent, so a removed number never flashes on screen. If the
    person stops the answer (closes the connection), nothing is stored.
    """
    from edrak.api.streaming import format_sse_event

    thread = await _thread(db, session, thread_id)

    async def events():
        try:
            async for event in _respond(db, session, thread, body, stream=True):
                yield format_sse_event(event["type"], event)
        except HTTPException as exc:
            yield format_sse_event("error", {"code": "not_found" if exc.status_code == 404 else "error",
                                             "message": "Ask Edrak could not open this chat."})
        except Exception:  # noqa: BLE001
            logger.exception("Ask Edrak stream failed")
            yield format_sse_event("error", {"code": "error", "message": "Ask Edrak could not answer right now. Try again in a moment."})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
