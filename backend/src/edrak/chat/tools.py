"""What Ask Edrak can read beyond one brief, so it can answer before any analysis has run.

Everything here is read-only and scoped to one workspace. Each tool registers what it returns in the
chat's SourceBook and returns the text the model reads, with the tags to cite.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import aiosqlite

from edrak.chat.sources import SourceBook
from edrak.db.util import fetch_all, fetch_one
from edrak.guardrails.content import sanitize
from edrak.guardrails.patterns import redact_pii

logger = logging.getLogger(__name__)


def _clean(text: Any, limit: int) -> str:
    return sanitize(str(text or ""))[0][:limit]


def terms(text: str, limit: int = 10) -> list[str]:
    """Search words: 3+ letters (Arabic included), lower case, with a plain plural "s" also tried without it."""
    out: list[str] = []
    for w in re.findall(r"[\w؀-ۿ]{3,}", (text or "").lower()):
        for v in (w, w[:-1] if w.endswith("s") and len(w) > 4 else None):
            if v and v not in out:
                out.append(v)
    return out[:limit]


@dataclass
class WorkspaceContext:
    """Loaded once per message: the company profile and approved facts are small enough to hold in memory."""

    workspace_id: str
    company: dict[str, Any] = field(default_factory=dict)
    facts: list[Any] = field(default_factory=list)
    brief_count: int = 0
    exclude_brief_id: Optional[str] = None     # in a brief chat, workspace search looks at the other briefs

    @property
    def company_name(self) -> str:
        return str(self.company.get("name") or "")

    def profile_lines(self) -> list[str]:
        c = self.company
        rows = (
            ("Name", c.get("name")),
            ("Also known as", ", ".join(c.get("aliases") or [])),
            ("Industries", ", ".join(c.get("industries") or []) or c.get("industry_other")),
            ("What the company does", c.get("description")),
            ("Offerings", ", ".join(c.get("offerings") or [])),
            ("Target customer groups", ", ".join(c.get("target_customer_groups") or [])),
            ("Markets served", ", ".join(m.get("name", "") for m in c.get("markets") or [] if m.get("relation") == "serves")),
            ("Markets being considered", ", ".join(m.get("name", "") for m in c.get("markets") or [] if m.get("relation") == "considers")),
            ("Strategic goals", ", ".join(c.get("goals") or [])),
            ("Tracked competitors", ", ".join(x.get("name", "") for x in c.get("competitors") or [])),
            ("Website", c.get("website")),
        )
        return [f"{label}: {_clean(value, 600)}" for label, value in rows if value]


async def load_workspace(db: aiosqlite.Connection, workspace_id: str, *, exclude_brief_id: Optional[str] = None) -> WorkspaceContext:
    from edrak.db.repositories import company as company_repo
    from edrak.db.repositories import company_facts as company_facts_repo

    ctx = WorkspaceContext(workspace_id, exclude_brief_id=exclude_brief_id)
    try:
        ctx.company = await company_repo.get_company(db, workspace_id)
    except Exception as exc:  # noqa: BLE001 - the chat still works without a profile
        logger.info("Company profile unavailable for chat: %s", exc)
    try:
        ctx.facts = await company_facts_repo.list_facts(db, workspace_id, status="approved")
    except Exception as exc:  # noqa: BLE001
        logger.info("Company facts unavailable for chat: %s", exc)
    row = await fetch_one(db, "SELECT count(*) AS n FROM briefs WHERE workspace_id = ? AND lifecycle <> 'superseded'", (workspace_id,))
    ctx.brief_count = int((row or {}).get("n") or 0)
    return ctx


def register_profile(ws: WorkspaceContext, book: SourceBook) -> str:
    """The profile goes into every prompt as [P]; it is short and answers "who are we / who do we sell to"."""
    lines = ws.profile_lines()
    if not lines:
        return "COMPANY PROFILE [P]: (not filled in yet)"
    book.add("company", "Your company profile", "\n".join(lines), tag="P")
    return "COMPANY PROFILE [P] (what the company entered in Edrak; the company's own description, not verified research):\n" + \
        "\n".join(f"- {line}" for line in lines)


# ------------------------------------------------------------------------------------------------ tools


def search_company_facts(ws: WorkspaceContext, book: SourceBook, query: str, k: int = 8) -> str:
    """Approved company-research facts (from the company's own site, PDFs and socials) that match the query."""
    if not ws.facts:
        return "No approved company facts yet. (Facts appear after company research runs and someone approves them.)"
    words = terms(query)
    scored = []
    for i, f in enumerate(ws.facts):
        hay = " ".join(str(getattr(f, a, "") or "") for a in ("category", "proposed_category", "key", "key_label_en", "key_label_ar", "value", "quote")).lower()
        hay = hay.replace("_", " ")
        score = sum(1 for w in words if w in hay)
        if score or not words:
            scored.append((score, i, f))
    if not scored:
        return "No approved company fact matches that. Try other words, or the profile [P]."
    lines = []
    for _, _, f in sorted(scored, key=lambda x: (-x[0], x[1]))[:k]:
        source = str(getattr(f, "source_url", "") or "")
        page = getattr(f, "pdf_page", None)
        label = str(getattr(f, "key_label_en", None) or getattr(f, "key", "")).replace("_", " ")
        value, quote = _clean(getattr(f, "value", ""), 500), _clean(getattr(f, "quote", ""), 300)
        tag = book.add("company", f"{label} · {getattr(f, 'category', '')}", f"{value} {quote}", prefix="CF",
                       url=source or None, dedupe_key=f"fact:{getattr(f, 'id', label)}")
        lines.append(f"[{tag}] ({getattr(f, 'category', '')} / {label}) {value} | quote: {quote} | source: {source}"
                     f"{f' (page {page})' if page else ''} | confidence: {getattr(f, 'confidence', '')}")
    return "\n".join(lines)


async def list_briefs(db: aiosqlite.Connection, ws: WorkspaceContext, book: SourceBook, k: int = 10) -> str:
    rows = await fetch_all(
        db, """SELECT id, number, title, summary, lifecycle, evidence_status, generated_at FROM briefs
               WHERE workspace_id = ? AND lifecycle <> 'superseded' ORDER BY generated_at DESC LIMIT ?""", (ws.workspace_id, k))
    if not rows:
        return "No briefs yet: no analysis has finished in this workspace."
    lines = []
    for r in rows:
        tag = book.add("workspace", f"Brief {int(r['number']):04d} · {r['title']}", f"{r['title']}. {r.get('summary') or ''}",
                       tag=f"B{int(r['number'])}", brief_id=r["id"], brief_no=int(r["number"]))
        lines.append(f"[{tag}] Brief {int(r['number']):04d} \"{_clean(r['title'], 160)}\" ({str(r['generated_at'])[:10]}, {r['lifecycle']}, "
                     f"evidence {r['evidence_status']}): {_clean(r.get('summary'), 400)}")
    return "\n".join(lines)


async def search_workspace(db: aiosqlite.Connection, ws: WorkspaceContext, book: SourceBook, query: str, k: int = 8) -> str:
    """Evidence, cards, gaps, claims and verifier notes across every brief of the workspace."""
    from edrak.briefing.search import _match

    hits: list[dict[str, Any]] = []
    m = _match(query)
    if m:
        sql = """SELECT brief_search.brief_id AS brief_id, brief_search.kind AS kind, brief_search.ref_id AS ref_id,
                        brief_search.text AS text, b.number AS number, b.title AS title
                 FROM brief_search JOIN briefs b ON b.id = brief_search.brief_id
                 WHERE brief_search MATCH ? AND b.workspace_id = ? AND b.lifecycle <> 'superseded'"""
        params: list[Any] = [m, ws.workspace_id]
        if ws.exclude_brief_id:
            sql += " AND b.id <> ?"
            params.append(ws.exclude_brief_id)
        try:
            hits = await fetch_all(db, sql + " ORDER BY bm25(brief_search) LIMIT ?", (*params, k))
        except Exception as exc:  # noqa: BLE001 - fall back to plain matching
            logger.info("Workspace FTS search failed: %s", exc)
    if not hits:
        hits = await _like_search(db, ws, query, k)
    if not hits:
        return "Nothing in the workspace's briefs matches." + ("" if ws.brief_count else " There are no briefs yet.")
    lines = []
    for h in hits:
        n = int(h["number"])
        if h["kind"] == "evidence" and str(h["ref_id"]).isdigit():
            tag = f"B{n}:E{int(h['ref_id'])}"
            book.add("workspace", f"Brief {n:04d} · source E{h['ref_id']}", _clean(h["text"], 800), tag=tag, brief_id=h["brief_id"],
                     brief_no=n, display_no=int(h["ref_id"]))
        else:
            tag = book.add("workspace", f"Brief {n:04d} · {h['kind']}", _clean(h["text"], 800), prefix=f"B{n}:N", brief_id=h["brief_id"],
                           brief_no=n, dedupe_key=f"{h['brief_id']}:{h['kind']}:{h['ref_id']}")
        lines.append(f"[{tag}] (brief {n:04d} \"{_clean(h.get('title'), 80)}\", {h['kind']}) {_clean(h['text'], 500)}")
    return "\n".join(lines)


async def _like_search(db: aiosqlite.Connection, ws: WorkspaceContext, query: str, k: int) -> list[dict[str, Any]]:
    words = terms(query, 8)
    if not words:
        return []
    scope, params = "b.workspace_id = ? AND b.lifecycle <> 'superseded'", [ws.workspace_id]
    if ws.exclude_brief_id:
        scope += " AND b.id <> ?"
        params.append(ws.exclude_brief_id)
    ev = await fetch_all(
        db, f"""SELECT b.id AS brief_id, 'evidence' AS kind, e.display_no AS ref_id,
                       coalesce(e.source_title, '') || ': ' || e.extracted_fact AS text, b.number AS number, b.title AS title
                FROM evidence e JOIN briefs b ON b.request_id = e.request_id
                WHERE {scope} AND ({' OR '.join('lower(e.extracted_fact) LIKE ?' for _ in words)})
                ORDER BY b.generated_at DESC LIMIT ?""", [*params, *[f"%{w}%" for w in words], k])
    fi = await fetch_all(
        db, f"""SELECT b.id AS brief_id, 'finding' AS kind, f.uid AS ref_id, f.statement AS text, b.number AS number, b.title AS title
                FROM findings f JOIN briefs b ON b.request_id = f.request_id
                WHERE {scope} AND ({' OR '.join('lower(f.statement) LIKE ?' for _ in words)}) LIMIT ?""",
        [*params, *[f"%{w}%" for w in words], k])
    return [*ev, *fi][:k]


async def watchlist(db: aiosqlite.Connection, ws: WorkspaceContext, book: SourceBook, name: Optional[str] = None, k: int = 12) -> str:
    """Tracked competitors, markets and topics with what Edrak knows about each ("What we know")."""
    sql = "SELECT id, kind, name, sector, country, website_domain, summary, summary_updated_at FROM entities WHERE workspace_id = ? AND is_tracked = 1"
    params: list[Any] = [ws.workspace_id]
    if name:
        sql += " AND lower(name) LIKE ?"
        params.append(f"%{name.lower().strip()}%")
    rows = await fetch_all(db, sql + " ORDER BY kind, lower(name) LIMIT ?", (*params, k))
    if not rows:
        return f"No tracked entity named {name}." if name else "The watchlist is empty."
    lines = []
    for r in rows:
        meta = ", ".join(x for x in (r["kind"], r.get("sector"), r.get("country"), r.get("website_domain")) if x)
        summary = _clean(r.get("summary"), 600) or "(no summary yet: it is written when an analysis covers this entity)"
        tag = book.add("workspace", f"Watchlist · {r['name']}", f"{r['name']} {meta} {summary}", prefix="C", dedupe_key=f"entity:{r['id']}")
        lines.append(f"[{tag}] {r['name']} ({meta}): {summary}")
    return "\n".join(lines)


async def recent_alerts(db: aiosqlite.Connection, ws: WorkspaceContext, book: SourceBook, k: int = 8) -> str:
    rows = await fetch_all(
        db, """SELECT id, kind, importance, title, what_changed, why_it_matters, created_at FROM alerts
               WHERE workspace_id = ? AND status <> 'dismissed' ORDER BY created_at DESC LIMIT ?""", (ws.workspace_id, k))
    if not rows:
        return "No recent alerts."
    lines = []
    for r in rows:
        body = f"{_clean(r.get('what_changed'), 300)} {_clean(r.get('why_it_matters'), 300)}"
        tag = book.add("workspace", f"Alert · {r['title']}", f"{r['title']} {body}", prefix="A", dedupe_key=f"alert:{r['id']}")
        lines.append(f"[{tag}] {str(r['created_at'])[:10]} ({r['importance']}, {r['kind']}) {_clean(r['title'], 160)}: {body}")
    return "\n".join(lines)


async def search_documents(ws: WorkspaceContext, book: SourceBook, query: str, k: int = 4) -> str:
    """Passages from documents the company uploaded and Edrak indexed."""

    def run() -> list[Any]:
        from edrak.rag.retriever import InternalRetriever

        return InternalRetriever().retrieve(query, top_k=k, workspace_id=ws.workspace_id)

    try:
        found = await asyncio.to_thread(run)
    except Exception as exc:  # noqa: BLE001 - documents are optional
        logger.info("Document search unavailable: %s", exc)
        return "Document search is unavailable right now."
    lines = []
    for e in found or []:
        text = _clean(getattr(e, "excerpt", None) or getattr(e, "extracted_fact", ""), 700)
        if not text:
            continue
        title = str(getattr(e, "source_title", None) or "Uploaded document")
        tag = book.add("documents", title, text, prefix="D", dedupe_key=f"doc:{title}:{text[:60]}")
        lines.append(f"[{tag}] ({title}) {text}")
    return "\n".join(lines) or "No uploaded document matches."


async def web_search(book: SourceBook, query: str, k: int = 4) -> tuple[str, list[str]]:
    """The open web. The query is stripped of personal data first; results are always unverified."""
    from edrak.agents.competitor_intelligence import discovery

    safe_query, _ = redact_pii(query or "")
    try:
        results = await asyncio.to_thread(discovery.web_search, safe_query, k)
    except Exception as exc:  # noqa: BLE001
        logger.info("Chat web search failed: %s", exc)
        return "The web search failed.", []
    lines, texts = [], []
    for r in results or []:
        content = _clean(r.get("content"), 500)
        tag = book.add("web", str(r.get("title") or r.get("url") or "Web result"), f"{r.get('title') or ''} {r.get('content') or ''}",
                       prefix="W", url=r.get("url"), dedupe_key=f"web:{r.get('url')}")
        lines.append(f"[{tag}] {r.get('title')} ({r.get('url')}): {content}")
        texts.append(r.get("content") or "")
    return ("\n".join(lines) or "The web search returned nothing."), texts
