"""The nodes of the research graph.

Each node takes the shared state and returns the fields it changes. A node handles its own errors:
one that fails adds a warning and the run goes on with what it has. Only a failure that leaves
nothing to work with (no website, an unreadable homepage, facts that cannot be saved) sets `error`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import asdict, replace
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from edrak.agents.competitor_intelligence.socials import normalise_profile, platform_of
from edrak.company_profile.models import ProfileFields
from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked
from edrak.company_profile.url_import.extract import extract_main_text
from edrak.company_profile.url_import.fetch import SiteFetcher
from edrak.company_profile.url_import.parse import parse_page
from edrak.company_profile.url_import.urls import normalize_url
from edrak.agents.customer_trends.tools.urls import non_public_reason
from edrak.company_research.agent.gaps import empty_categories, plan_targets
from edrak.company_research.agent.search import SearchUnavailable, serper_search
from edrak.company_research.agent.state import Pending, ResearchDeps, ResearchState
from edrak.company_research.categories import get_category
from edrak.company_research.crawler import CrawlLimits, CrawlResult, crawl_company_site
from edrak.company_research.crawler.rules import in_scope, page_key
from edrak.company_research.extraction import (
    ExtractionLimits,
    ExtractionResult,
    apply_extraction,
    documents_from_crawl,
    documents_from_pdfs,
    extract_company_facts,
)
from edrak.company_research.extraction import SourceDoc
from edrak.company_research.models import CompanyFact, FactStatus, ResearchRunStatus, SourceTier
from edrak.company_research.pdfs import Outcome, PdfLimits, find_pdfs, read_pdfs
from edrak.core.config import settings
from edrak.db.connection import get_db
from edrak.db.repositories import company as company_repo
from edrak.db.repositories import company_facts as facts_repo
from edrak.db.repositories import fact_keys as keys_repo
from edrak.db.repositories import research_runs as runs_repo
from edrak.db.util import now_iso

logger = logging.getLogger(__name__)

Node = Callable[[ResearchState], Awaitable[dict[str, Any]]]


class NoWebsite(Exception):
    """The company has no saved website and the request gave none."""


def resolve_seed(company: dict[str, Any], override: str | None) -> str:
    """The address to research: the request's, else the company's saved website. Raises NoWebsite or UrlBlocked."""
    raw = (override or "").strip() or (company.get("website") or "").strip()
    if not raw:
        raise NoWebsite("Add the company's website to its profile, or give an address to research.")
    url = normalize_url(raw)
    reason = non_public_reason((urlsplit(url).hostname or "").lower())  # no network: names are checked when fetched
    if reason:
        raise UrlBlocked(f"That address cannot be researched: {reason}.")
    return url


def profile_of(company: dict[str, Any]) -> ProfileFields:
    """What the saved profile already says, so research does not repeat it."""
    return ProfileFields(
        name=company.get("name") or None,
        aliases=list(company.get("aliases") or []),
        notes=company.get("description") or None,
        products=list(company.get("offerings") or []),
        industry=company.get("industry") or None,
        markets=[m["name"] for m in company.get("markets") or []],
        goals=list(company.get("goals") or []),
    )


def _add(total: dict[str, Any], more: dict[str, Any]) -> dict[str, Any]:
    """`total` with the numbers of `more` added; other values are replaced."""
    out = dict(total)
    for key, value in more.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and isinstance(out.get(key, 0), (int, float)):
            out[key] = round(out.get(key, 0) + value, 3)
        else:
            out[key] = value
    return out


MAX_WARNINGS = 100


def tidy_warnings(warnings: list[str]) -> list[str]:
    """Each warning once, in order, and at most MAX_WARNINGS of them, with a last line counting the rest."""
    unique = list(dict.fromkeys(warnings))
    if len(unique) <= MAX_WARNINGS:
        return unique
    return [*unique[:MAX_WARNINGS], f"{len(unique) - MAX_WARNINGS} more warning(s) were left out."]


def build_stats(state: ResearchState) -> dict[str, Any]:
    """Counts from every step so far, for the run record."""
    pending = state.get("pending", [])
    return {
        "rounds": state.get("round", 0),
        "crawl": state.get("crawl_stats", {}),
        "pdfs": state.get("pdf_stats", {}),
        "extraction": state.get("extraction", {}),
        "batches_used": state.get("batches_used", 0),
        "empty_categories": state.get("empty", []),
        "new_keys": len(state.get("new_keys", {})),
        "third_party": state.get("third_party", {}),
        "facts_found": state.get("created") or len(pending),
    }


def _stored(pending: Pending) -> CompanyFact:
    now = now_iso()
    data = pending.fact.model_dump(mode="json")
    data.update(id=pending.id, created_at=now, updated_at=now, observed_at=pending.fact.observed_at or now)
    return CompanyFact.model_validate(data)


def _fold(state: ResearchState, result: ExtractionResult) -> dict[str, Any]:
    """Add one extraction's changes to what this run has found so far.

    Changes that point at a fact found earlier in this run (not stored yet) are applied to it here;
    changes that point at a stored fact wait for merge_and_save.
    """
    pending = list(state.get("pending", []))
    by_id = {p.id: p for p in pending}
    supersede = list(state.get("supersede", []))
    groups = dict(state.get("conflict_groups", {}))
    for fact_id in result.supersede:
        if fact_id in by_id:
            pending = [p for p in pending if p.id != fact_id]
            del by_id[fact_id]
        else:
            supersede.append(fact_id)
    for fact_id, group in result.conflict_groups.items():
        if fact_id in by_id:
            by_id[fact_id].fact.conflict_group = group
        else:
            groups[fact_id] = group
    seq = state.get("pending_seq", 0)
    for fact in result.facts:
        seq += 1
        pending.append(Pending(f"pending-{seq}", fact))
    keys = dict(state.get("new_keys", {}))
    for new in result.new_keys:
        known = keys.get((new.category, new.key))
        if known is None:
            keys[(new.category, new.key)] = new
        else:
            known.facts += new.facts
    return {
        "pending": pending, "pending_seq": seq, "supersede": supersede, "conflict_groups": groups, "new_keys": keys,
        "extraction": _add(state.get("extraction", {}), asdict(result.stats)),
        "batches_used": state.get("batches_used", 0) + result.stats.batches,
        "warnings": result.warnings,
    }


def _have_categories(stored: list[CompanyFact], pending: list[Pending]) -> set[str]:
    return {f.category for f in stored if f.status != FactStatus.REJECTED} | {p.fact.category for p in pending}


class Nodes:
    """The graph's nodes, sharing one set of dependencies."""

    def __init__(self, deps: ResearchDeps) -> None:
        self.deps = deps
        self.options = deps.options

    # ------------------------------------------------------------------ helpers

    def time_left(self, state: ResearchState) -> float:
        return self.options.time_budget_s - (self.deps.clock() - state.get("began", self.deps.clock()))

    async def _extract_docs(self, state: ResearchState, docs: list[SourceDoc]) -> dict[str, Any]:
        """Run extraction on `docs` against what is stored and what this run has found, and fold it in."""
        if not docs:
            return {}
        base = self.deps.extraction_limits or ExtractionLimits.from_settings()
        left = base.max_batches - state.get("batches_used", 0)
        if left <= 0:
            return {"warnings": [f"{len(docs)} document(s) were not read: the run reached its limit of model calls."]}
        async with get_db() as db:
            stored = await facts_repo.list_facts(db, state["workspace_id"])
            keys = await keys_repo.list_fact_keys(db)
        existing = [*stored, *(_stored(p) for p in state.get("pending", []))]
        result = await extract_company_facts(
            docs, workspace_id=state["workspace_id"], run_id=state["run_id"], profile=state.get("profile"),
            existing=existing, keys=keys, llm=self.deps.llm, limits=replace(base, max_batches=left),
        )
        return _fold(state, result)

    # ------------------------------------------------------------------ nodes

    async def seed(self, state: ResearchState) -> dict[str, Any]:
        async with get_db() as db:
            company = await company_repo.get_company(db, state["workspace_id"])
        try:
            url = resolve_seed(company, state.get("seed_url"))
        except (NoWebsite, UrlBlocked) as exc:
            return {"error": str(exc), "warnings": [str(exc)]}
        links = [(l["platform"], l["url"]) for l in company["links"] if l["platform"] != "website"]
        return {
            "seed_url": url, "profile": profile_of(company), "company_name": company.get("name") or "",
            "social_links": links, "began": self.deps.clock(), "round": 0, "targets": [],
        }

    async def crawl(self, state: ResearchState) -> dict[str, Any]:
        targets = state.get("targets") or []
        try:
            update = await (self._crawl_targets(state, targets) if targets else self._crawl_site(state))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("The crawl step failed")
            message = f"The crawl step stopped with an unexpected error ({type(exc).__name__})."
            # Nothing was read at all only on the first round, and only then is the run without a website.
            update = {"new_pages": [], "warnings": [message], **({} if targets else {"error": message})}
        pages = update.pop("new_pages")
        known = {page_key(p.url) for p in state.get("pages", [])}
        pages = [p for p in pages if page_key(p.url) not in known]
        update.update(
            pages=[*state.get("pages", []), *pages], round_pages=pages, new_docs=documents_from_crawl(
                CrawlResult(start_url=state.get("home_url", ""), pages=pages)
            ),
            pages_crawled=state.get("pages_crawled", 0) + len(pages),
        )
        return update

    async def _crawl_site(self, state: ResearchState) -> dict[str, Any]:
        limits = self.deps.crawl_limits or CrawlLimits.from_settings()
        try:
            crawl = await crawl_company_site(
                state["seed_url"], fetcher=self.deps.new_fetcher(), browser=self.deps.browser, limits=limits,
                clock=self.deps.clock, sleep=self.deps.sleep,
            )
        except (UrlBlocked, SiteUnreachable) as exc:
            message = f"The company's website could not be read: {exc}"
            return {"new_pages": [], "error": message, "warnings": [message]}
        return {
            "new_pages": crawl.pages, "home_url": crawl.home_url or crawl.start_url, "round_pdf_links": crawl.pdf_links,
            "crawl_stats": _add(state.get("crawl_stats", {}), _crawl_stats(crawl)), "warnings": crawl.warnings,
        }

    async def _crawl_targets(self, state: ResearchState, targets: list[str]) -> dict[str, Any]:
        """Read the pages chosen by the gap check, one page each, on the company's own site."""
        home = state["home_url"]
        base = self.deps.crawl_limits or CrawlLimits.from_settings()
        pages, links, warnings = [], [], []
        totals = state.get("crawl_stats", {})
        for url in targets:
            left = self.time_left(state)
            if left <= 0:
                warnings.append("The run ran out of time before every extra page was read.")
                break
            limits = replace(base, max_pages=1, max_depth=0, time_budget_s=min(base.time_budget_s, left))
            try:
                crawl = await crawl_company_site(
                    url, fetcher=self.deps.new_fetcher(), browser=self.deps.browser, limits=limits,
                    clock=self.deps.clock, sleep=self.deps.sleep, sitemaps=False,
                )
            except (UrlBlocked, SiteUnreachable) as exc:
                warnings.append(f"An extra page could not be read: {exc}")
                continue
            totals = _add(totals, _crawl_stats(crawl))
            for page in crawl.pages:
                if in_scope(page.url, home):  # a page that redirected away is not the company's own
                    pages.append(page)
                else:
                    warnings.append("An extra page redirected away from the company's website and was not read.")
            links += crawl.pdf_links
            warnings += crawl.warnings
        return {"new_pages": pages, "round_pdf_links": links, "crawl_stats": totals, "warnings": warnings}

    async def pdfs(self, state: ResearchState) -> dict[str, Any]:
        pages = state.get("round_pages", [])
        base = self.deps.pdf_limits or PdfLimits.from_settings()
        room, attempts = base.max_pdfs - state.get("pdfs_read", 0), base.max_attempts - state.get("pdf_attempts", 0)
        if not pages or room <= 0 or attempts <= 0:
            return {}
        home = state["home_url"]
        crawl = CrawlResult(start_url=home, home_url=home, pages=pages, pdf_links=state.get("round_pdf_links", []))
        seen = set(state.get("pdf_seen", []))
        candidates = [c for c in find_pdfs(crawl) if c.url not in seen]
        if not candidates:
            return {}
        result = await read_pdfs(
            candidates, home, fetcher=self.deps.new_fetcher(), limits=replace(base, max_pdfs=room, max_attempts=attempts),
            clock=self.deps.clock, sleep=self.deps.sleep,
        )
        tried = [r.url for r in result.reports if r.outcome != Outcome.NOT_TRIED]
        return {
            "new_docs": [*state.get("new_docs", []), *documents_from_pdfs(result)],
            "pdf_seen": [*seen, *tried], "pdf_attempts": state.get("pdf_attempts", 0) + result.stats.attempted,
            "pdfs_read": state.get("pdfs_read", 0) + result.stats.read,
            "pdf_stats": _add(state.get("pdf_stats", {}), asdict(result.stats)), "warnings": result.warnings,
        }

    async def extract(self, state: ResearchState) -> dict[str, Any]:
        update = await self._extract_docs(state, state.get("new_docs", []))
        update["new_docs"] = []
        return update

    async def gap_check(self, state: ResearchState) -> dict[str, Any]:
        """Find the categories with no facts. If important ones are empty and budget remains, choose pages to read next."""
        async with get_db() as db:
            stored = await facts_repo.list_facts(db, state["workspace_id"])
        empty = empty_categories(_have_categories(stored, state.get("pending", [])))
        update: dict[str, Any] = {"empty": empty, "targets": []}
        wanted = [c for c in self.options.important if c in empty]
        rounds = state.get("round", 0)
        if not wanted or rounds >= self.options.gap_rounds:
            return update
        if self.time_left(state) <= 0 or state.get("batches_used", 0) >= (self.deps.extraction_limits or ExtractionLimits.from_settings()).max_batches:
            return update
        visited = [page_key(p.url) for p in state.get("pages", [])]
        targets = plan_targets(
            wanted, state.get("pages", []), state["home_url"], visited, state.get("targeted", []), self.options.gap_targets
        )
        if targets:
            update.update(targets=targets, targeted=[*state.get("targeted", []), *targets], round=rounds + 1)
        return update

    async def own_social(self, state: ResearchState) -> dict[str, Any]:
        """Read the bio or about text of the company's own social profiles, for categories still empty. Never logs in."""
        if not state.get("empty"):
            return {}
        reader = self.deps.social_reader
        if reader is None:
            return {
                "warnings": [
                    "The company's social profiles were not read: no tool in this project reads a public bio or "
                    "about text without a paid service or a login."
                ]
            }
        profiles: dict[str, str] = {}
        for platform, url in state.get("social_links", []):
            profiles.setdefault(platform_of(url) or platform, url)
        for page in state.get("pages", []):
            for link in page.links:
                profile = normalise_profile(link.url)
                if profile and platform_of(profile):
                    profiles.setdefault(platform_of(profile) or "", profile)
        docs, warnings = [], []
        for platform, url in list(profiles.items())[:5]:
            try:
                text = await reader(platform, url)
            except Exception as exc:
                logger.warning("A social profile could not be read: %r", exc)
                warnings.append(f"The {platform} profile could not be read.")
                continue
            if text and text.strip():
                docs.append(SourceDoc(url=url, text=text, tier=SourceTier.OWN_SOCIAL, title=f"{platform} profile"))
        update = await self._extract_docs(state, docs)
        update["warnings"] = [*warnings, *update.get("warnings", [])]
        return update

    async def third_party(self, state: ResearchState) -> dict[str, Any]:
        """Search the web for the empty categories and read the top results. Runs only when the flag is on."""
        categories = state.get("empty", [])[: self.options.third_party_searches]
        if not categories:
            return {}
        search = self.deps.search or serper_search
        name = state.get("company_name") or urlsplit(state.get("home_url", "")).hostname or ""
        home, urls, warnings, searches = state.get("home_url", ""), {}, [], 0
        for category in categories:
            query = f"{name} {get_category(category).label_en}"  # type: ignore[union-attr]
            try:
                hits = await search(query, self.options.third_party_results + 2)  # a few more, for results we skip
            except SearchUnavailable as exc:
                warnings.append(f"Third-party search was skipped: {exc}")
                break
            except Exception as exc:
                logger.warning("Third-party search failed: %r", exc)
                warnings.append(f"A third-party search for {get_category(category).label_en} failed.")  # type: ignore[union-attr]
                continue
            searches += 1
            for hit in list(hits)[: self.options.third_party_results]:  # only the top results are read
                if not in_scope(hit.url, home) and not platform_of(hit.url) and not hit.url.lower().endswith(".pdf"):
                    urls.setdefault(hit.url, hit.title)
        docs = await self._read_third_party(list(urls)[: self.options.third_party_pages], warnings)
        update = await self._extract_docs(state, docs)
        update["warnings"] = [*warnings, *update.get("warnings", [])]
        update["third_party"] = {"searches": searches, "pages": len(docs)}
        return update

    async def _read_third_party(self, urls: list[str], warnings: list[str]) -> list[SourceDoc]:
        docs: list[SourceDoc] = []
        async with (self.deps.new_fetcher() or SiteFetcher()) as site:

            async def robots(url: str) -> None:
                if not await site.allowed(url):
                    raise SiteUnreachable("robots.txt does not allow this page.")

            for url in urls:
                try:
                    fetched = await site.get(url, before_request=robots)
                except (UrlBlocked, SiteUnreachable) as exc:
                    host = urlsplit(url).hostname or "unknown host"  # no path or query: addresses can carry tokens
                    warnings.append(f"A third-party page on {host} could not be read: {exc}")
                    continue
                html = fetched.text()
                text = await asyncio.to_thread(extract_main_text, html, fetched.url)
                if text.strip():
                    docs.append(SourceDoc(
                        url=fetched.url, text=text, tier=SourceTier.THIRD_PARTY, title=parse_page(html, fetched.url).title
                    ))
        return docs

    async def merge_and_save(self, state: ResearchState) -> dict[str, Any]:
        """Write what the run found, as suggested facts, in one transaction."""
        pending = state.get("pending", [])
        if not pending and not state.get("supersede") and not state.get("conflict_groups"):
            return {"created": 0}
        result = ExtractionResult(
            facts=[p.fact for p in pending], supersede=state.get("supersede", []),
            conflict_groups=state.get("conflict_groups", {}),
        )
        async with get_db() as db:
            created = await apply_extraction(db, state["workspace_id"], result)
        return {"created": len(created)}

    async def finish(self, state: ResearchState) -> dict[str, Any]:
        failed = state.get("error")
        if not failed and not state.get("pages") and not state.get("created"):
            failed = "No page of the company's website could be read."
        extraction = state.get("extraction", {})
        if not failed and extraction.get("batches") and extraction.get("failed_batches") == extraction["batches"] and not state.get("created"):
            failed = "The language model could not read any source."
        warnings = tidy_warnings(state.get("warnings", []))
        stats = build_stats(state)
        async with get_db() as db:
            await runs_repo.update_research_run(
                db, state["run_id"], commit=False, pages_crawled=state.get("pages_crawled", 0),
                pdfs_read=state.get("pdfs_read", 0), facts_found=state.get("created", 0),
            )
            await runs_repo.finish_research_run(
                db, state["run_id"], status=ResearchRunStatus.FAILED if failed else ResearchRunStatus.DONE,
                error=failed, warnings=warnings, stats=stats,
            )
        return {"error": failed}


def _crawl_stats(crawl: CrawlResult) -> dict[str, Any]:
    stats = asdict(crawl.stats)
    stats["stopped"] = crawl.stats.stopped.value
    return stats


def safe(name: str, node: Node, deps: ResearchDeps, *, fatal: bool = False, on_error: dict[str, Any] | None = None) -> Node:
    """Wrap a node: report its start and end to the progress hook, and turn an error into a warning.

    A `fatal` node also sets the run's error, since the run has nothing to show without it.
    `on_error` is the state to leave behind when the node fails, so a later step does not act on what
    an earlier pass left there (for example the pages the gap check chose last time).
    """

    @functools.wraps(node)
    async def run(state: ResearchState) -> dict[str, Any]:
        await _report(deps, state, name)
        try:
            update = await node(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Research step %s failed", name)
            message = f"The {name.replace('_', ' ')} step stopped with an unexpected error ({type(exc).__name__})."
            update = {"warnings": [message], **(on_error or {})}
            if fatal:
                update["error"] = message
        view: dict[str, Any] = {**state, **update, "warnings": [*state.get("warnings", []), *update.get("warnings", [])]}
        await _report(deps, view, name)  # type: ignore[arg-type]
        return update

    return run


async def _report(deps: ResearchDeps, state: ResearchState, stage: str) -> None:
    if deps.progress is None:
        return
    try:
        await deps.progress(state, stage)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Could not record the progress of a research run")
