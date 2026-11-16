"""What the research graph shares between its nodes, and what is injected into it."""

from __future__ import annotations

import asyncio
import operator
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Awaitable, Callable, Optional, Sequence, TypedDict

from edrak.company_profile.extractor import LLMCall
from edrak.company_profile.models import ProfileFields
from edrak.company_profile.url_import.fetch import SiteFetcher
from edrak.company_research.crawler import CrawlLimits, CrawledPage
from edrak.company_research.crawler.browser import BrowserFactory
from edrak.company_research.extraction import ExtractionLimits, NewKey, SourceDoc
from edrak.company_research.models import NewFact
from edrak.company_research.pdfs import PdfLimits
from edrak.core.config import settings

MAX_EXTRA_ROUNDS = 2  # the most extra crawl rounds a run makes, whatever the setting


@dataclass
class SearchHit:
    url: str
    title: str = ""


SearchFn = Callable[[str, int], Awaitable[Sequence[SearchHit]]]
SocialReader = Callable[[str, str], Awaitable[Optional[str]]]  # (platform, profile URL) -> public bio text
Progress = Callable[["ResearchState", str], Awaitable[None]]


@dataclass
class ResearchOptions:
    """The run's settings, read once when the run is built."""

    time_budget_s: float
    max_run_s: float
    important: tuple[str, ...]
    gap_rounds: int
    gap_targets: int
    third_party: bool
    third_party_searches: int
    third_party_results: int
    third_party_pages: int
    extract_max_batches: int

    @classmethod
    def from_settings(cls) -> "ResearchOptions":
        names = settings.COMPANY_RESEARCH_IMPORTANT_CATEGORIES.split(",")
        return cls(
            time_budget_s=settings.COMPANY_RESEARCH_RUN_TIME_BUDGET_S,
            max_run_s=settings.COMPANY_RESEARCH_RUN_MAX_S,
            important=tuple(n.strip() for n in names if n.strip()),
            gap_rounds=min(MAX_EXTRA_ROUNDS, max(0, settings.COMPANY_RESEARCH_GAP_MAX_ROUNDS)),
            gap_targets=max(1, settings.COMPANY_RESEARCH_GAP_MAX_TARGETS),
            third_party=settings.COMPANY_RESEARCH_THIRD_PARTY,
            third_party_searches=max(1, settings.COMPANY_RESEARCH_THIRD_PARTY_MAX_SEARCHES),
            third_party_results=max(1, settings.COMPANY_RESEARCH_THIRD_PARTY_RESULTS_PER_SEARCH),
            third_party_pages=max(1, settings.COMPANY_RESEARCH_THIRD_PARTY_MAX_PAGES),
            extract_max_batches=max(1, settings.COMPANY_RESEARCH_EXTRACT_MAX_BATCHES),
        )


@dataclass
class ResearchDeps:
    """What the graph uses from outside. Everything defaults to the real thing; tests replace the network and the model."""

    options: ResearchOptions = field(default_factory=ResearchOptions.from_settings)
    fetcher: Optional[Callable[[], SiteFetcher]] = None  # a new fetcher per stage
    browser: Optional[BrowserFactory] = None
    llm: Optional[LLMCall] = None
    search: Optional[SearchFn] = None  # web search for the third-party step; Serper when not given
    social_reader: Optional[SocialReader] = None  # none exists in the repository, so the social step is skipped
    crawl_limits: Optional[CrawlLimits] = None
    pdf_limits: Optional[PdfLimits] = None
    extraction_limits: Optional[ExtractionLimits] = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    progress: Optional[Progress] = None

    def new_fetcher(self) -> Optional[SiteFetcher]:
        return self.fetcher() if self.fetcher else None


@dataclass
class Pending:
    """A fact found in this run that is not stored yet. `id` stands in for a database id while merging."""

    id: str
    fact: NewFact


class ResearchState(TypedDict, total=False):
    workspace_id: str
    run_id: str
    seed_url: str  # the request's override, when there is one
    home_url: str  # the site being researched, after redirects
    company_name: str
    profile: ProfileFields
    social_links: list[tuple[str, str]]  # (platform, url) saved on the company
    began: float  # the run's clock when it started
    # what the crawl found
    pages: list[CrawledPage]  # every page read, all rounds
    round_pages: list[CrawledPage]  # the pages of this round only
    round_pdf_links: list[Any]
    new_docs: list[SourceDoc]  # read and not yet sent to the model
    pdf_seen: list[str]  # PDF addresses already tried
    pdf_attempts: int
    # what extraction found, merged across rounds
    pending: list[Pending]
    pending_seq: int
    supersede: list[str]
    conflict_groups: dict[str, str]
    new_keys: dict[tuple[str, str], NewKey]
    batches_used: int
    extraction: dict[str, int]
    # the gap loop
    round: int  # extra rounds made
    targets: list[str]  # pages to read in the next round
    targeted: list[str]  # pages already chosen as targets
    empty: list[str]  # categories with no facts
    # counters and the outcome
    pages_crawled: int
    pdfs_read: int
    crawl_stats: dict[str, Any]
    pdf_stats: dict[str, int]
    third_party: dict[str, int]
    created: int
    error: Optional[str]
    warnings: Annotated[list[str], operator.add]
