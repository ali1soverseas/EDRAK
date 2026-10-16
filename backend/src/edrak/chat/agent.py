"""One chat agent for a brief, with a small set of tools and a hard step limit.

Order of trust: the brief first (exact numbers from the metrics table, never from memory), the web
second (always labelled unverified), and "not covered" when neither has it. Web results never edit a
card; adding one to the brief is a separate, explicit challenge.

The loop uses structured steps rather than provider function-calling, which the model used here does not
do reliably (see core/llm.get_structured).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Literal, Optional

import aiosqlite
from pydantic import BaseModel, Field

from edrak.briefing.search import hybrid_search
from edrak.guardrails.chat import ChatCheck, check_answer
from edrak.guardrails.content import UNTRUSTED_RULE, sanitize
from edrak.metrics.format import format_value

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 4
MAX_WEB_CALLS = 2


class Step(BaseModel):
    action: Literal["get_metric", "get_card", "search_brief", "web_search", "request_rerun", "answer"]
    metric: Optional[str] = Field(default=None, description="get_metric: a metric uid or a registry metric id")
    entity: Optional[str] = Field(default=None, description="get_metric: restrict to this company or market")
    card_key: Optional[str] = Field(default=None, description="get_card: the card key")
    query: Optional[str] = Field(default=None, description="search_brief or web_search: the search words")
    target: Optional[str] = Field(default=None, description="request_rerun: what should be re-researched")
    answer: Optional[str] = Field(default=None, description="answer: the reply. Cite brief evidence as [E12].")
    label: Optional[Literal["brief", "web_unverified", "not_covered"]] = None
    evidence_nos: list[int] = Field(default_factory=list, description="answer: evidence numbers relied on")
    metric_uids: list[str] = Field(default_factory=list, description="answer: metric uids relied on")


SYSTEM = f"""You are Ask Edrak, answering questions about ONE decision brief.

Tools (one per step, at most {MAX_TOOL_STEPS} steps before you must answer):
- get_metric: exact figures from the brief's metrics table. This is the ONLY source for numbers about the company, competitors and market.
- get_card: one card of the brief.
- search_brief: find evidence, claims, gaps and verifier notes in the brief.
- web_search: the open web, at most {MAX_WEB_CALLS} times. Use it only after the brief does not cover the question.
- request_rerun: propose re-researching something (this only proposes; it starts nothing).
- answer: finish.

Rules:
- Brief first, web second. Never state a number you did not read from get_metric, a card, or cited evidence. If a number is not in the table, say it is not in the brief.
- label "brief" when the answer rests on the brief (cite evidence as [E12] and list evidence_nos / metric_uids). label "web_unverified" when it rests on web results (say it is unverified and name the source). label "not_covered" when neither has it.
- Be brief and plain. Do not rank options or say which to choose; the brief leaves that decision to the person.
- {UNTRUSTED_RULE}"""


class BriefContext:
    """Everything the tools read, loaded once per message."""

    def __init__(self, brief_id: str, document: dict[str, Any]) -> None:
        self.brief_id = brief_id
        self.document = document
        self.metrics: dict[str, dict[str, Any]] = document.get("metrics") or {}
        self.cards: dict[str, dict[str, Any]] = document.get("cards") or {}
        self.evidence_text: dict[int, str] = {}
        for item in document.get("evidence") or []:
            ev = item.get("evidence") or {}
            self.evidence_text[int(item.get("display_no") or 0)] = f"{ev.get('source_title') or ''} {ev.get('extracted_fact') or ''} {ev.get('excerpt') or ''}"
        self.evidence_nos = set(self.evidence_text)

    def metric_view(self, m: dict[str, Any]) -> dict[str, Any]:
        return {"uid": m["uid"], "metric_id": m.get("metric_id"), "entity": m.get("entity"), "value": format_value(m.get("value"), m.get("unit")),
                "period": m.get("period"), "label": m.get("label"), "status": m.get("status"), "scope": m.get("scope"),
                "evidence": m.get("display_no"), "derived": bool(m.get("derived")), "formula": m.get("formula"), "sample_size": m.get("sample_size")}

    def get_metric(self, metric: Optional[str], entity: Optional[str]) -> list[dict[str, Any]]:
        key = (metric or "").strip()
        rows = [m for m in self.metrics.values()
                if key in (m.get("uid"), m.get("metric_id"), (m.get("metric_id") or "").replace("__vs_company", ""))
                and (not entity or (m.get("entity") or "").lower() == entity.lower())]
        return [self.metric_view(m) for m in rows[:12]]

    def card_brief(self, card: dict[str, Any]) -> dict[str, Any]:
        out = {k: card.get(k) for k in ("card_key", "title", "headline", "status", "caveat", "refs", "metric_uids", "version")}
        out["values"] = [self.metric_view(self.metrics[u]) for u in card.get("metric_uids") or [] if u in self.metrics][:10]
        return out

    def verifier_notes(self, card: dict[str, Any]) -> list[str]:
        ctl = ((self.document.get("verification") or {}).get("control_summary") or {})
        notes = [*(ctl.get("missing_information") or []), *(ctl.get("conflicts") or [])]
        entity = (card.get("entity") or "").lower()
        mid = (card.get("metric_id") or "").lower()
        return [n for n in notes if (entity and entity in n.lower()) or (mid and mid.replace("_", " ") in n.lower())][:5]


def preload_card(ctx: BriefContext, card_key: str) -> str:
    """Card-level chat starts from this card's claim, evidence and verifier note."""
    card = ctx.cards.get(card_key)
    if not card:
        return ""
    ev = "\n".join(f"[E{n}] {sanitize(ctx.evidence_text.get(n, ''))[0][:500]}" for n in (card.get("refs") or [])[:5])
    notes = "\n".join(f"- {n}" for n in ctx.verifier_notes(card)) or "- none"
    return (f"THE PERSON IS LOOKING AT THIS CARD:\n{json.dumps(ctx.card_brief(card), ensure_ascii=False)}\n"
            f"ITS EVIDENCE:\n{ev or '(none)'}\nVERIFIER NOTES:\n{notes}\nStart from this card, then widen to the brief, then the web.")


async def _run_tool(step: Step, ctx: BriefContext, db: aiosqlite.Connection, web_used: list[int], web_text: list[str]) -> str:
    if step.action == "get_metric":
        rows = ctx.get_metric(step.metric, step.entity)
        return json.dumps(rows, ensure_ascii=False) if rows else "NOT IN THE TABLE: no stored metric matches. Do not state a number for it."
    if step.action == "get_card":
        card = ctx.cards.get(step.card_key or "")
        return json.dumps(ctx.card_brief(card), ensure_ascii=False) if card else "No such card."
    if step.action == "search_brief":
        hits = await hybrid_search(db, ctx.brief_id, step.query or "", k=6)
        return "\n".join(f"[{h['kind']} {h['ref_id']}] {sanitize(h['text'])[0][:500]}" for h in hits) or "Nothing in the brief matches."
    if step.action == "web_search":
        if web_used[0] >= MAX_WEB_CALLS:
            return "Web search limit reached for this message."
        web_used[0] += 1
        from edrak.agents.competitor_intelligence.discovery import web_search

        results = await asyncio.to_thread(web_search, step.query or "", 4)
        lines = [f"- {r.get('title')} ({r.get('url')}): {sanitize(r.get('content') or '')[0][:400]}" for r in results]
        web_text.extend(r.get("content") or "" for r in results)
        return "\n".join(lines) or "The web search returned nothing."
    if step.action == "request_rerun":
        return f"Proposed follow-up research: {step.target}. Nothing was started; tell the person they can start it from the brief."
    return ""


async def run_agent(db: aiosqlite.Connection, ctx: BriefContext, question: str, *, history: list[dict[str, str]] | None = None,
                    card_key: Optional[str] = None) -> AsyncIterator[dict[str, Any]]:
    """Yields {"type": "step", ...} while working and one {"type": "answer", ...} at the end."""
    from edrak.core.llm import get_structured, schema_instruction

    model = get_structured(Step, agent="orchestrator", temperature=0.2)
    convo = "\n".join(f"{h['role']}: {h['content'][:500]}" for h in (history or [])[-6:])
    pre = preload_card(ctx, card_key) if card_key else ""
    scratch: list[str] = []
    web_used, web_text = [0], []
    proposal: Optional[str] = None
    final: Optional[Step] = None

    for i in range(MAX_TOOL_STEPS + 1):
        forced = i == MAX_TOOL_STEPS
        prompt = (f"{pre}\n\nCONVERSATION SO FAR:\n{convo or '(none)'}\n\nQUESTION: {question}\n\nTOOL RESULTS SO FAR:\n"
                  + ("\n\n".join(scratch) or "(none yet)") + ("\n\nYou must answer now." if forced else ""))
        try:
            step: Optional[Step] = await model.ainvoke([("system", SYSTEM), ("user", prompt), schema_instruction(Step)])
        except Exception as exc:  # noqa: BLE001
            logger.info("Chat step failed: %s", exc)
            step = None
        if step is None:
            break
        if step.action == "answer" or forced:
            final = step if step.action == "answer" else None
            break
        yield {"type": "step", "tool": step.action, "input": step.query or step.metric or step.card_key or step.target or ""}
        result = await _run_tool(step, ctx, db, web_used, web_text)
        if step.action == "request_rerun":
            proposal = step.target
        scratch.append(f"[{step.action} {step.query or step.metric or step.card_key or step.target or ''}]\n{result}")

    if final is None or not (final.answer or "").strip():
        yield {"type": "answer", "answer": "I could not produce an answer right now. Try rephrasing the question.", "label": "not_covered",
               "cited": [], "metric_uids": [], "notes": [], "proposal": proposal}
        return
    label = "web_unverified" if (final.label == "web_unverified" or (web_used[0] and final.label != "brief")) else (final.label or "not_covered")
    checked: ChatCheck = check_answer(final.answer, label, evidence_nos=final.evidence_nos, metric_uids=final.metric_uids,
                                      brief_evidence_nos=ctx.evidence_nos, brief_metrics=ctx.metrics, evidence_text=ctx.evidence_text,
                                      web_text=" ".join(web_text))
    yield {"type": "answer", "answer": checked.answer, "label": checked.label, "cited": checked.evidence_nos, "metric_uids": checked.metric_uids,
           "notes": checked.notes, "proposal": proposal}
