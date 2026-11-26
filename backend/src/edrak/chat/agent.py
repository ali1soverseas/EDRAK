"""Ask Edrak: one conversational agent for every scope (workspace, brief, card, signal).

Order of trust, highest first:
  1. this brief (exact numbers from its metrics table, never from memory), when the chat is about a brief;
  2. the workspace's other briefs, its watchlist and alerts (research Edrak has already done);
  3. the company profile [P] and approved company-research facts [CF] (what the company says about itself);
  4. uploaded documents [D];
  5. the web [W], always labelled unverified, and only when the above do not answer.

So the chat answers before any analysis has run (from the profile, facts, documents and, if needed, the web)
and says when an analysis would verify what it found. Web results never edit a card; adding one to the
brief is a separate, explicit challenge.

The loop uses structured steps rather than provider function-calling, which the model used here does not
do reliably (see core/llm.get_structured). Every answer goes through guardrails.chat.check_chat_answer.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator, Literal, Optional

import aiosqlite
from pydantic import BaseModel, Field

from edrak.briefing.search import hybrid_search
from edrak.chat import tools
from edrak.chat.sources import CITE_RE, SourceBook
from edrak.chat.tools import WorkspaceContext
from edrak.guardrails.chat import ChatAnswerCheck, check_chat_answer
from edrak.guardrails.chat_input import detect_lang, tr
from edrak.guardrails.content import UNTRUSTED_RULE, sanitize
from edrak.metrics.format import format_value

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 4
MAX_WEB_CALLS = 2
HISTORY_VERBATIM = 8         # messages quoted in full (about four turns)
HISTORY_EARLIER = 8          # earlier questions listed in one line each, so long chats keep their thread

BRIEF_ACTIONS = ("get_metric", "get_card", "search_brief")
WORKSPACE_ACTIONS = ("search_workspace", "list_briefs", "get_watchlist", "recent_alerts", "search_company_facts", "search_documents")

Label = Literal["brief", "workspace", "company", "documents", "web_unverified", "conversation", "not_covered"]


class Step(BaseModel):
    action: Literal["get_metric", "get_card", "search_brief", "search_workspace", "list_briefs", "get_watchlist", "recent_alerts",
                    "search_company_facts", "search_documents", "web_search", "request_rerun", "answer"]
    metric: Optional[str] = Field(default=None, description="get_metric: a metric uid or a registry metric id")
    entity: Optional[str] = Field(default=None, description="get_metric: restrict to this company or market. get_watchlist: a name, or empty for all")
    card_key: Optional[str] = Field(default=None, description="get_card: the card key")
    query: Optional[str] = Field(default=None, description="search_* or web_search: a standalone search (resolve 'it', 'they', 'and X?' from the conversation)")
    target: Optional[str] = Field(default=None, description="request_rerun: what should be researched or re-researched")
    answer: Optional[str] = Field(default=None, description="answer: the reply, citing every fact with its tag, e.g. [E12] [B3:E4] [P] [CF2] [D1] [W1]")
    label: Optional[Label] = None
    sources: list[str] = Field(default_factory=list, description="answer: the tags the reply relies on")
    evidence_nos: list[int] = Field(default_factory=list, description="answer: evidence numbers of this brief relied on")
    metric_uids: list[str] = Field(default_factory=list, description="answer: metric uids relied on")
    follow_ups: list[str] = Field(default_factory=list, description="answer: 2-3 short questions the person may ask next, in their language")


_TOOL_HELP = {
    "get_metric": "get_metric: exact figures from this brief's metrics table. The ONLY source for this brief's numbers.",
    "get_card": "get_card: one card of this brief.",
    "search_brief": "search_brief: evidence, claims, gaps and verifier notes in this brief.",
    "search_workspace": "search_workspace: evidence, cards, gaps and findings across the workspace's {other}briefs.",
    "list_briefs": "list_briefs: the workspace's briefs (number, title, date, summary). Use for 'my latest brief', 'what have we analysed'.",
    "get_watchlist": "get_watchlist: tracked competitors, markets and topics, with what Edrak knows about each. entity = a name, or empty for all.",
    "recent_alerts": "recent_alerts: the latest monitoring alerts (competitor changes, signals).",
    "search_company_facts": "search_company_facts: approved facts about the company from its own website, PDFs and social pages.",
    "search_documents": "search_documents: passages from documents the company uploaded.",
    "web_search": "web_search: the open web, at most {web} times per message. Only when Edrak's own sources do not answer.",
    "request_rerun": "request_rerun: propose an analysis (or a re-run) that would answer this properly. It only proposes; it starts nothing.",
    "answer": "answer: finish.",
}


def _system(actions: list[str], *, scope: str, has_briefs: bool) -> str:
    other = "other " if scope in ("brief", "card", "signal") else ""
    tool_lines = "\n".join("- " + _TOOL_HELP[a].format(other=other, web=MAX_WEB_CALLS) for a in actions)
    order = ("this brief, then the workspace's other briefs, then the company profile and facts, then documents, then the web"
             if scope in ("brief", "card", "signal") else
             "the workspace's briefs, watchlist and alerts, then the company profile and facts, then documents, then the web")
    no_runs = "" if has_briefs else (
        "\n- No analysis has run in this workspace yet. Answer from the company profile [P], company facts, documents and, if needed, "
        "the web, and say that running an analysis would research and verify it.")
    return f"""You are Ask Edrak, a conversational assistant inside Edrak, a decision-intelligence product.
You help one company understand its competitors, markets, customers and its own decision briefs.

Tools (one per step, at most {MAX_TOOL_STEPS} steps before you must answer):
{tool_lines}

How to work:
- This is a conversation. Read CONVERSATION SO FAR. Resolve follow-ups ("why?", "and Temu?", "what about prices?") from it, and
  write every tool query as a full standalone search.
- Look in this order: {order}. Do not use the web when Edrak's own sources answer the question.
- Answer as soon as you have enough; do not call a tool you do not need. The COMPANY PROFILE below is already loaded.
- For questions asking what changed recently, latest news, or current competitor activity, check recent_alerts and the tracked entities/profile first.
  If there is no dated, relevant alert or other current evidence, search the web even if older briefs mention that competitor.
  Use the profile/watchlist only to identify which competitors to search; it does not establish recent activity.
- For questions combining the company's current position with market trends or competitor strategy, use briefs and the profile as background only.
  Check the tracked entities and alerts, then search the web for current external evidence if no relevant dated update is available.
  Do not answer from a brief summary alone or add competitor claims from memory.
- For recent-activity answers, give at most 3 concise bullets with the change, source, and date when available. Do not pad the answer with older brief facts.
  If the web returns no relevant dated updates, say that no recent update was found; do not imply that no change occurred.
- Greetings, thanks and questions about yourself: reply briefly with label "conversation", no tools.
- If the question is genuinely ambiguous, ask one short clarifying question (label "conversation").{no_runs}

Rules for the answer:
- Cite every fact with the tag shown next to it: [E12], [B3], [B3:E4], [B3:N1], [C1], [A1], [P], [CF2], [D1], [W1]. List the tags in "sources".
- Never state a number you did not read in a tool result or the profile. If a number is not there, say it is not in what Edrak holds.
- The profile [P] and company facts [CF] are what the company says about itself; say so when it matters ("according to your profile").
- Web results are unverified: say so and name the source.
- label: "brief" (rests on this brief), "workspace" (other briefs, watchlist, alerts), "company" (profile or facts), "documents",
  "web_unverified", "conversation" (small talk or a clarifying question), or "not_covered" (nothing answers it; say what is missing
  and, where useful, call request_rerun first to propose an analysis).
- If asked what the company should do, give one concise, evidence-grounded suggestion and up to two alternatives when useful.
  Separate sourced facts from your analysis, state important assumptions or trade-offs, and frame the suggestion as advice for the person
  to evaluate, not as a decision already made or a certainty. Cite the factual premises; never invent competitor claims.
- Be concise and plain. Short paragraphs or "- " bullet lists. Answer in the language of the latest question.
- follow_ups: 2-3 short next questions the sources can answer, in the person's language.
- {UNTRUSTED_RULE}"""


class BriefContext:
    """Everything the brief tools read, loaded once per message."""

    def __init__(self, brief_id: str, document: dict[str, Any]) -> None:
        self.brief_id = brief_id
        self.document = document
        self.metrics: dict[str, dict[str, Any]] = document.get("metrics") or {}
        self.cards: dict[str, dict[str, Any]] = document.get("cards") or {}
        self.evidence_text: dict[int, str] = {}
        self.evidence_meta: dict[int, dict[str, Any]] = {}
        for item in document.get("evidence") or []:
            ev = item.get("evidence") or {}
            no = int(item.get("display_no") or 0)
            self.evidence_text[no] = f"{ev.get('source_title') or ''} {ev.get('extracted_fact') or ''} {ev.get('excerpt') or ''}"
            self.evidence_meta[no] = {"title": ev.get("source_title") or f"Source E{no}", "url": ev.get("source_url")}
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

    def outline(self) -> str:
        """A few lines that orient the model in a brief chat: title, summary and the card list."""
        title = self.document.get("title") or ""
        summary = self.document.get("summary")
        summary = summary if isinstance(summary, str) else ""
        cards = ", ".join(f"{k} ({(c.get('title') or '')[:40]})" for k, c in list(self.cards.items())[:25])
        return (f"THE BRIEF: {sanitize(str(title))[0][:200]}\n{sanitize(summary)[0][:600]}\nCARDS: {cards or '(none)'}").strip()


def preload_card(ctx: BriefContext, card_key: str) -> str:
    """Card-level chat starts from this card's claim, evidence and verifier note."""
    card = ctx.cards.get(card_key)
    if not card:
        return ""
    ev = "\n".join(f"[E{n}] {sanitize(ctx.evidence_text.get(n, ''))[0][:500]}" for n in (card.get("refs") or [])[:5])
    notes = "\n".join(f"- {n}" for n in ctx.verifier_notes(card)) or "- none"
    return (f"THE PERSON IS LOOKING AT THIS CARD:\n{json.dumps(ctx.card_brief(card), ensure_ascii=False)}\n"
            f"ITS EVIDENCE:\n{ev or '(none)'}\nVERIFIER NOTES:\n{notes}\nStart from this card, then widen to the brief, then the web.")


def preload_signal(ctx: BriefContext, signal_id: str) -> str:
    """Signal-level chat starts from the signal, its interpretation and its evidence."""
    sig = next((s for s in ctx.document.get("signals_view") or [] if s.get("signal_id") == signal_id), None)
    if not sig:
        return ""
    ev = "\n".join(f"[E{n}] {sanitize(ctx.evidence_text.get(int(n), ''))[0][:500]}" for n in (sig.get("refs") or [])[:6])
    view = {k: sig.get(k) for k in ("signal_id", "title", "type", "signal", "interpretation", "why_it_matters", "domains", "refs")}
    return (f"THE PERSON IS LOOKING AT THIS SIGNAL:\n{json.dumps(view, ensure_ascii=False)[:1500]}\nITS EVIDENCE:\n{ev or '(none)'}\n"
            "Start from this signal, then widen to the brief, then the web.")


def format_history(history: list[dict[str, str]] | None) -> str:
    """The last few messages in full, and earlier questions in one line each."""
    history = [h for h in history or [] if h.get("content")]
    recent, earlier = history[-HISTORY_VERBATIM:], history[:-HISTORY_VERBATIM]
    lines = []
    questions = [h["content"] for h in earlier if h.get("role") == "user"][-HISTORY_EARLIER:]
    if questions:
        lines.append("Earlier in this conversation the person asked: " + " | ".join(re.sub(r"\s+", " ", q)[:120] for q in questions))
    lines += [f"{h['role']}: {sanitize(h['content'])[0][:700]}" for h in recent]
    return "\n".join(lines)


def _clean_follow_ups(items: list[str], question: str) -> list[str]:
    out: list[str] = []
    for q in items or []:
        q = CITE_RE.sub("", str(q)).strip().strip("-• ").strip()
        if 6 <= len(q) <= 140 and q.lower() != question.lower().strip() and q not in out:
            out.append(q)
    return out[:3]


def _new_book(ctx: Optional[BriefContext]) -> SourceBook:
    book = SourceBook()
    if ctx is not None:
        for n, text in ctx.evidence_text.items():
            meta = ctx.evidence_meta.get(n, {})
            book.add("brief", meta.get("title") or f"Source E{n}", text, tag=f"E{n}", url=meta.get("url"), brief_id=ctx.brief_id, display_no=n)
    return book


async def _run_tool(step: Step, ctx: Optional[BriefContext], ws: Optional[WorkspaceContext], db: Optional[aiosqlite.Connection],
                    book: SourceBook, web_used: list[int], web_text: list[str]) -> str:
    a = step.action
    if a in BRIEF_ACTIONS and ctx is None:
        return "This chat is not about one brief. Use search_workspace or list_briefs."
    if a in WORKSPACE_ACTIONS and (ws is None or (db is None and a not in ("search_company_facts", "search_documents"))):
        return "That source is not available in this chat."
    if a == "get_metric":
        rows = ctx.get_metric(step.metric, step.entity)
        return json.dumps(rows, ensure_ascii=False) if rows else "NOT IN THE TABLE: no stored metric matches. Do not state a number for it."
    if a == "get_card":
        card = ctx.cards.get(step.card_key or "")
        return json.dumps(ctx.card_brief(card), ensure_ascii=False) if card else "No such card."
    if a == "search_brief":
        hits = await hybrid_search(db, ctx.brief_id, step.query or "", k=6)
        return "\n".join(f"[{'E' + str(h['ref_id']) if h['kind'] == 'evidence' else h['kind'] + ' ' + str(h['ref_id'])}] "
                         f"{sanitize(h['text'])[0][:500]}" for h in hits) or "Nothing in the brief matches."
    if a == "search_workspace":
        return await tools.search_workspace(db, ws, book, step.query or "")
    if a == "list_briefs":
        return await tools.list_briefs(db, ws, book)
    if a == "get_watchlist":
        return await tools.watchlist(db, ws, book, step.entity or None)
    if a == "recent_alerts":
        return await tools.recent_alerts(db, ws, book)
    if a == "search_company_facts":
        return tools.search_company_facts(ws, book, step.query or "")
    if a == "search_documents":
        return await tools.search_documents(ws, book, step.query or "")
    if a == "web_search":
        if web_used[0] >= MAX_WEB_CALLS:
            return "Web search limit reached for this message."
        web_used[0] += 1
        result, texts = await tools.web_search(book, step.query or "")
        web_text.extend(texts)
        return result
    if a == "request_rerun":
        return f"Proposed research: {step.target}. Nothing was started; tell the person they can start it as a new analysis."
    return ""


def _available(ctx: Optional[BriefContext], ws: Optional[WorkspaceContext]) -> list[str]:
    actions: list[str] = []
    if ctx is not None:
        actions += list(BRIEF_ACTIONS)
    if ws is not None:
        actions += list(WORKSPACE_ACTIONS)
    return [*actions, "web_search", "request_rerun", "answer"]


async def run_agent(db: Optional[aiosqlite.Connection], ctx: Optional[BriefContext], question: str, *,
                    history: list[dict[str, str]] | None = None, card_key: Optional[str] = None, signal_id: Optional[str] = None,
                    workspace: Optional[WorkspaceContext] = None) -> AsyncIterator[dict[str, Any]]:
    """Yields {"type": "step", ...} while working and one {"type": "answer", ...} at the end.

    `ctx` is the brief (None in a workspace chat); `workspace` adds the company profile, facts, other briefs,
    watchlist, alerts and documents (None keeps the chat to the brief and the web, as before).
    """
    from edrak.core.llm import get_structured, schema_instruction

    lang = detect_lang(question)
    scope = "card" if card_key else "signal" if signal_id else "brief" if ctx is not None else "workspace"
    has_briefs = ctx is not None or bool(workspace and workspace.brief_count)
    actions = _available(ctx, workspace)
    system = _system(actions, scope=scope, has_briefs=has_briefs)
    model = get_structured(Step, agent="orchestrator", temperature=0.2)

    book = _new_book(ctx)
    pre: list[str] = []
    if workspace is not None:
        pre.append(tools.register_profile(workspace, book))
    if ctx is not None:
        pre.append(ctx.outline())
    if card_key and ctx is not None:
        pre.append(preload_card(ctx, card_key))
    elif signal_id and ctx is not None:
        pre.append(preload_signal(ctx, signal_id))
    preamble = "\n\n".join(p for p in pre if p)

    convo = format_history(history)
    user_text = question + " " + " ".join(h["content"] for h in history or [] if h.get("role") == "user")
    scratch: list[str] = []
    trace: list[dict[str, str]] = []
    web_used, web_text = [0], []
    proposal: Optional[str] = None
    final: Optional[Step] = None

    for i in range(MAX_TOOL_STEPS + 1):
        forced = i == MAX_TOOL_STEPS
        prompt = (f"{preamble}\n\nCONVERSATION SO FAR:\n{convo or '(none)'}\n\nQUESTION: {question}\n\nTOOL RESULTS SO FAR:\n"
                  + ("\n\n".join(scratch) or "(none yet)") + ("\n\nYou must answer now." if forced else ""))
        try:
            step: Optional[Step] = await model.ainvoke([("system", system), ("user", prompt), schema_instruction(Step)])
        except Exception as exc:  # noqa: BLE001
            logger.info("Chat step failed: %s", exc)
            step = None
        if step is None:
            break
        if step.action == "answer" or forced:
            final = step if step.action == "answer" else None
            break
        tool_input = step.query or step.metric or step.card_key or step.entity or step.target or ""
        yield {"type": "step", "tool": step.action, "input": tool_input}
        trace.append({"tool": step.action, "input": tool_input[:200]})
        if step.action not in actions:
            result = f"{step.action} is not available in this chat."
        else:
            try:
                result = await _run_tool(step, ctx, workspace, db, book, web_used, web_text)
            except Exception as exc:  # noqa: BLE001 - one failing source should not end the answer
                logger.info("Chat tool %s failed: %s", step.action, exc)
                result = f"{step.action} failed; continue with what you have."
        if step.action == "request_rerun":
            proposal = step.target
        scratch.append(f"[{step.action} {tool_input}]\n{result}")

    if final is None or not (final.answer or "").strip():
        yield {"type": "answer", "answer": tr(lang, "I could not produce an answer right now. Try rephrasing the question.",
                                              "لم أتمكن من الإجابة الآن. جرّب إعادة صياغة السؤال."),
               "label": "not_covered", "kinds": [], "cited": [], "metric_uids": [], "sources": [], "notes": [], "proposal": proposal,
               "follow_ups": [], "steps": trace, "checks": []}
        return

    checked: ChatAnswerCheck = check_chat_answer(
        final.answer or "", final.label, book=book, cited=final.sources, evidence_nos=final.evidence_nos, metric_uids=final.metric_uids,
        brief_metrics=ctx.metrics if ctx is not None else {}, web_used=bool(web_used[0]), user_text=user_text, lang=lang)
    sources = [book.items[t].public() for t in checked.tags if t in book.items]
    yield {"type": "answer", "answer": checked.answer, "label": checked.label, "kinds": checked.kinds, "cited": checked.evidence_nos,
           "metric_uids": checked.metric_uids, "sources": sources, "notes": checked.notes, "proposal": proposal,
           "follow_ups": _clean_follow_ups(final.follow_ups, question), "steps": trace, "checks": checked.checks}
