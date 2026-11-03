"""What a crawl of a company website returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from edrak.company_profile.url_import.parse import Link


class FetchMode(str, Enum):
    STATIC = "static"
    DYNAMIC = "dynamic"


class StopReason(str, Enum):
    COMPLETE = "complete"  # nothing left worth reading
    MAX_PAGES = "max_pages"
    TIME_BUDGET = "time_budget"


@dataclass
class PdfLink:
    """A link that looks like a PDF. Nothing is downloaded by the crawl."""

    url: str
    text: str = ""
    found_on: str = ""  # the page (or sitemap) that links to it
    same_site: bool = True


@dataclass
class CrawledPage:
    url: str  # after redirects
    title: str
    language: Optional[str]  # "ar", "en", or None when nothing says
    text: str  # main text; the longer of the static and browser readings
    fetch_mode: FetchMode
    links: list[Link] = field(default_factory=list)  # every http(s) link found, PDF links included
    pdf_links: list[PdfLink] = field(default_factory=list)
    depth: int = 0  # links followed from the homepage
    section: str = "other"  # the keyword section the URL matched, for ranking
    render_reason: str = ""  # why the browser was used, when it was


@dataclass
class CrawlStats:
    pages_crawled: int = 0
    static_pages: int = 0
    dynamic_pages: int = 0
    urls_discovered: int = 0  # pages queued, sitemap and links together
    skipped_robots: int = 0
    skipped_filtered: int = 0  # login, search, files, and the like
    skipped_language: int = 0  # an Arabic copy of a page already read in English
    skipped_depth: int = 0
    skipped_off_site: int = 0  # redirects that left the company's domain
    failed: int = 0
    truncated: int = 0  # responses cut at the size cap
    pdf_links: int = 0
    browser_pages: int = 0  # pages opened in the browser
    scrolls: int = 0
    clicks: int = 0  # "load more" clicks
    expands: int = 0  # tabs, accordions and details opened
    elapsed_s: float = 0.0
    stopped: StopReason = StopReason.COMPLETE


@dataclass
class CrawlResult:
    start_url: str
    pages: list[CrawledPage] = field(default_factory=list)
    pdf_links: list[PdfLink] = field(default_factory=list)  # all pages together, without repeats
    stats: CrawlStats = field(default_factory=CrawlStats)
    warnings: list[str] = field(default_factory=list)
