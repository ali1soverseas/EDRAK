"""A mocked company site with a PDF, a scripted model and helpers to run the research graph in tests."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path

import httpx

import pdf_fixtures as pdfs
from company_research_fixtures import new_workspace
from crawl_fixtures import Site, limits as crawl_limits
from pdf_site_fixtures import NO_OCR, pdf_limits, served  # noqa: F401

from edrak.company_profile.url_import.fetch import SiteFetcher
from edrak.company_research.agent import ResearchDeps, ResearchOptions, ResearchRunManager
from edrak.company_research.extraction import ExtractionLimits
from edrak.db.connection import get_db
from edrak.db.repositories import company as company_repo
from edrak.db.repositories import company_facts as facts_repo
from edrak.db.repositories import research_runs as runs_repo

HOME = "https://acme.example/"
FILLER = "Acme Pay writes about its work in plain sentences so that every page has enough text to be read. " * 3

CEO_QUOTE = "Amal Nasr is the Chief Executive Officer of Acme Pay"
FOUNDED_QUOTE = "Acme Pay was founded in 2009 in Cairo"
BRANCH_QUOTE = "Acme Pay operates a branch in Alexandria, Egypt"
OWNER_QUOTE = "Acme Pay is owned by Nile Capital"
REVENUE_QUOTE = "Total revenue for 2023 was EGP 1.2 billion"
AWARD_QUOTE = "Acme Pay won the Fintech Award in 2022"


def page(title: str, body: str, links: str = "") -> str:
    return (
        f"<html lang='en'><head><title>{title}</title></head><body><main><h1>{title}</h1>"
        f"<p>{FILLER}</p><p>{body}</p></main><nav>{links}</nav></body></html>"
    )


NAV = (
    "<a href='/about'>About us</a><a href='/leadership-team'>Leadership</a><a href='/locations'>Our branches</a>"
    "<a href='/investors'>Investor relations</a><a href='/files/annual-report-2024.pdf'>Annual report 2024</a>"
)


def acme_site(pdf: bytes | None = None, **extra) -> Site:
    """Home, about, leadership, locations and investors pages, and an annual report PDF."""
    pages = {
        "/": page("Acme Pay", f"{FOUNDED_QUOTE}, Egypt.", NAV),
        "/about": page("About us", "Acme Pay builds card payment terminals for merchants."),
        "/leadership-team": page("Leadership", f"{CEO_QUOTE}."),
        "/locations": page("Our branches", f"{BRANCH_QUOTE}."),
        "/investors": page("Investor relations", f"{OWNER_QUOTE}."),
        "/sitemap.xml": (404, "", "text/plain"),
    }
    if pdf is not None:
        pages["/files/annual-report-2024.pdf"] = served(pdf)
    pages.update(extra)
    return Site(pages)


def report_pdf(folder: Path) -> bytes:
    return pdfs.long_document(folder / "report.pdf", 1, {1: REVENUE_QUOTE}).read_bytes()


# ------------------------------------------------------------------ the model


class Model:
    """A model that answers by what each prompt contains.

    `rules` are (text found in a source, fact fields). A fact is returned for each rule whose text
    appears in a source of the call, citing that source by its id. Every call's prompt is kept.
    """

    def __init__(self, *rules, fail: Exception | None = None, on_call=None) -> None:
        self.rules = rules
        self.fail = fail
        self.on_call = on_call
        self.calls: list[str] = []

    def __call__(self, messages):
        text = messages[1]["content"]
        self.calls.append(text)
        if self.on_call:
            self.on_call()
        if self.fail:
            raise self.fail
        facts = []
        for quote, fields in self.rules:
            for match in re.finditer(r"\[(S\d+)\][^\n]*\n(.*?)(?=\n\[S\d+\]|\Z)", text, re.S):
                if quote in match.group(2):
                    facts.append({
                        "category": "other", "key": "x", "label": "x", "value": "x", "value_type": "text",
                        "confidence": "high", **fields, "source": match.group(1), "quote": quote,
                    })
        return json.dumps({"facts": facts}, ensure_ascii=False)


def ceo(value="Amal Nasr", quote=CEO_QUOTE):
    return quote, dict(category="leadership", key="ceo", label="CEO", value=value)


FOUNDED = (FOUNDED_QUOTE, dict(category="history", key="founded_year", label="Founded", value="2009", value_type="number"))
BRANCH = (BRANCH_QUOTE, dict(category="locations", key="branch", label="Branch", value="Alexandria, Egypt"))
OWNER = (OWNER_QUOTE, dict(category="ownership_and_funding", key="shareholder", label="Owner", value="Nile Capital"))
REVENUE = (REVENUE_QUOTE, dict(category="financials", key="annual_revenue", label="Revenue 2023", value="EGP 1.2 billion"))
AWARD = (AWARD_QUOTE, dict(category="awards", key="award", label="Award", value="Fintech Award 2022"))


ALL_RULES = (ceo(), FOUNDED, BRANCH, OWNER, REVENUE)


# ------------------------------------------------------------------ running it


def options(**overrides) -> ResearchOptions:
    values = dict(
        time_budget_s=900.0, max_run_s=3600.0, important=("leadership", "ownership_and_funding", "financials", "locations", "history"),
        gap_rounds=2, gap_targets=4, third_party=False, third_party_searches=4, third_party_results=3, third_party_pages=6,
        extract_max_batches=30,
    )
    values.update(overrides)
    return ResearchOptions(**values)


def deps(site: Site, model, **overrides) -> ResearchDeps:
    values = dict(
        options=options(), fetcher=lambda: site.fetcher(), llm=model, crawl_limits=crawl_limits(),
        pdf_limits=pdf_limits(), extraction_limits=ExtractionLimits(batch_chars=24000, max_batches=30, ocr_tolerance=0.9),
    )
    values.update(overrides)
    return ResearchDeps(**values)


async def company(db, *, website: str | None = HOME, name: str = "Acme Pay") -> str:
    """A workspace whose company has `website` saved."""
    ws = await new_workspace(db, name)
    links = [{"platform": "website", "url": website}] if website else []
    await company_repo.save_company(db, ws, {"name": name, "links": links})
    return ws


async def research(site: Site, model, *, seed_url: str | None = None, ws: str | None = None, **overrides):
    """Run the graph to its end. Returns (workspace id, the finished run, the stored facts)."""
    manager = ResearchRunManager(deps(site, model, **overrides))
    async with get_db() as db:
        ws = ws or await company(db)
    run = await manager.start(ws, seed_url=seed_url)
    await manager.wait(run.id)
    async with get_db() as db:
        return ws, await runs_repo.get_research_run(db, ws, run.id), await facts_repo.list_facts(db, ws)


def run_it(site: Site, model, **kwargs):
    return asyncio.run(research(site, model, **kwargs))


def held(site: Site, gate: asyncio.Event):
    """A fetcher factory whose homepage waits for `gate`, so a run stays active until the test lets it go."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            await gate.wait()
        return site.handler(request)

    async def resolve(host, port):
        return ["93.184.216.34"]

    return lambda: SiteFetcher(transport=httpx.MockTransport(handler), resolver=resolve)


def row(path: Path, sql: str, *params):
    """A synchronous read of the database file, usable from a model call running in a thread."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()
