"""Download the best PDFs a company site offers and read them page by page.

Each file is fetched with the website import's fetcher, so every request, redirect hops included, is
checked against the non-public address rules, robots.txt is honoured, and the size is capped. The
bytes go to a temporary file that is deleted as soon as the pages are read; nothing is stored.
Reading is the PDF import's: pymupdf4llm, OCR for scans when it is installed, the Arabic order
retry, and the page picking for a long file. No model is called.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from edrak.company_profile.pdf_import.errors import PdfRejected
from edrak.company_profile.pdf_import.importer import PDF_MAGIC, extract_bytes
from edrak.company_profile.pdf_import.ocr import OcrStatus, ocr_status
from edrak.company_profile.pdf_import.select import select_pages
from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked
from edrak.company_profile.url_import.fetch import Fetched, SiteFetcher, UnexpectedContentType
from edrak.company_research.crawler import CrawlResult
from edrak.company_research.crawler.crawl import HostThrottle
from edrak.company_research.pdfs.candidates import allowed_source, find_pdfs
from edrak.company_research.pdfs.models import (
    Outcome,
    PdfCandidate,
    PdfPageDocument,
    PdfReadResult,
    PdfReport,
)
from edrak.core.config import settings

logger = logging.getLogger(__name__)

# What a server may call a PDF it sends. The first bytes are checked as well, so a wrong label costs nothing.
PDF_TYPES = frozenset(
    {"application/pdf", "application/x-pdf", "application/acrobat", "text/pdf", "application/octet-stream",
     "binary/octet-stream", "application/force-download", "application/download", "application/x-download"}
)
_NAME_CHARS = 70


@dataclass
class PdfLimits:
    max_pdfs: int = 8
    max_bytes: int = 20 * 1024 * 1024
    max_pages: int = 60
    max_attempts: int = 24
    context_chars: int = 48000
    host_delay_s: float = 1.0

    @classmethod
    def from_settings(cls) -> "PdfLimits":
        return cls(
            max_pdfs=max(1, settings.COMPANY_RESEARCH_PDF_MAX_PDFS),
            max_bytes=max(1, settings.COMPANY_RESEARCH_PDF_MAX_MB) * 1024 * 1024,
            max_pages=max(1, settings.COMPANY_RESEARCH_PDF_MAX_PAGES),
            max_attempts=max(1, settings.COMPANY_RESEARCH_PDF_MAX_ATTEMPTS),
            context_chars=max(1, settings.COMPANY_RESEARCH_PDF_CONTEXT_CHARS),
            host_delay_s=max(0.0, settings.COMPANY_RESEARCH_CRAWL_HOST_DELAY_S),
        )


class _RobotsDisallowed(SiteUnreachable):
    """robots.txt does not allow the file."""


def _label(url: str) -> str:
    parts = urlsplit(url)
    shown = f"{parts.hostname or ''}{parts.path}"
    return shown if len(shown) <= _NAME_CHARS else shown[: _NAME_CHARS - 1] + "…"


class _Reader:
    def __init__(
        self,
        site: SiteFetcher,
        home: str,
        limits: PdfLimits,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        ocr: OcrStatus,
    ) -> None:
        self.site = site
        self.home = home
        self.limits = limits
        self.clock = clock
        self.ocr = ocr
        self.throttle = HostThrottle(limits.host_delay_s, clock, sleep)
        self.result = PdfReadResult()
        self.stats = self.result.stats
        self.hashes: set[str] = set()

    async def _before_request(self, url: str) -> None:
        """Runs before every request, so before every redirect hop too."""
        if not await self.site.allowed(url):
            raise _RobotsDisallowed("robots.txt does not allow this file.")
        await self.throttle.wait((urlsplit(url).hostname or "").lower())

    def _report(self, candidate: PdfCandidate, outcome: Outcome, detail: str = "", total: int = 0, kept: int = 0) -> None:
        self.result.reports.append(
            PdfReport(candidate.url, outcome, candidate.via, candidate.found_on, total, kept, detail)
        )
        if outcome is not Outcome.READ:
            field = outcome.value
            setattr(self.stats, field, getattr(self.stats, field) + 1)
            self.result.warnings.append(f"Skipped {_label(candidate.url)}: {detail}")

    async def _download(self, candidate: PdfCandidate) -> Optional[Fetched]:
        try:
            return await self.site.get(candidate.url, kinds=PDF_TYPES, before_request=self._before_request)
        except _RobotsDisallowed:
            self._report(candidate, Outcome.ROBOTS, "robots.txt does not allow it.")
        except UnexpectedContentType:
            self._report(candidate, Outcome.NOT_PDF, "the address is not a PDF.")
        except UrlBlocked:
            self._report(candidate, Outcome.NOT_ALLOWED, "the address is not a public one.")
        except SiteUnreachable as exc:
            self._report(candidate, Outcome.UNREACHABLE, str(exc).rstrip(".").lower() + ".")
        return None

    async def read(self, candidate: PdfCandidate) -> bool:
        """Download and read one candidate. True when it gave pages."""
        if not allowed_source(candidate, self.home):
            self._report(candidate, Outcome.NOT_ALLOWED, "it is on a website that none of the company's pages links to.")
            return False
        self.stats.attempted += 1
        fetched = await self._download(candidate)
        if fetched is None:
            return False
        if fetched.truncated:
            self._report(candidate, Outcome.TOO_LARGE, f"it is over {self.limits.max_bytes // (1024 * 1024)} MB.")
            return False
        if not fetched.body.startswith(PDF_MAGIC):
            self._report(candidate, Outcome.NOT_PDF, "its contents are not a PDF.")
            return False
        digest = hashlib.sha256(fetched.body).hexdigest()
        if digest in self.hashes:
            self._report(candidate, Outcome.DUPLICATE, "it is the same file as one already read.")
            return False
        self.hashes.add(digest)
        try:
            extracted = await asyncio.to_thread(extract_bytes, fetched.body, ocr=self.ocr, page_limit=self.limits.max_pages)
        except PdfRejected as exc:
            if exc.code == "encrypted":
                self._report(candidate, Outcome.ENCRYPTED, "it is password protected.")
            else:
                self._report(candidate, Outcome.CORRUPT, "it could not be read as a PDF.")
            return False
        except Exception as exc:  # a file that breaks the reader should not stop the run
            logger.warning("Reading %s failed: %r", candidate.url, exc)
            self._report(candidate, Outcome.CORRUPT, "it could not be read as a PDF.")
            return False
        chosen, selection_warnings = select_pages(extracted.pages, self.limits.context_chars)
        if not chosen:
            self._report(candidate, Outcome.EMPTY, "no text could be read from it.", extracted.page_count)
            self.result.warnings.extend(f"{_label(candidate.url)}: {w}" for w in extracted.warnings)
            return False
        for page in chosen:
            self.result.documents.append(
                PdfPageDocument(candidate.url, page.number, page.title, page.text, candidate.found_on, page.ocr)
            )
        self.stats.read += 1
        self.stats.pages_kept += len(chosen)
        self.stats.ocr_pages += sum(1 for p in chosen if p.ocr)
        self.result.warnings.extend(f"{_label(candidate.url)}: {w}" for w in extracted.warnings + selection_warnings)
        self._report(candidate, Outcome.READ, total=extracted.page_count, kept=len(chosen))
        return True

    async def run(self, candidates: list[PdfCandidate]) -> PdfReadResult:
        begun = self.clock()
        self.stats.candidates = len(candidates)
        for index, candidate in enumerate(candidates):
            if self.stats.read >= self.limits.max_pdfs:
                self._not_tried(candidates[index:], f"{self.limits.max_pdfs} PDFs were already read.")
                break
            if self.stats.attempted >= self.limits.max_attempts:
                self._not_tried(candidates[index:], f"{self.limits.max_attempts} downloads were already tried.")
                break
            await self.read(candidate)
        self.stats.elapsed_s = round(self.clock() - begun, 3)
        return self.result

    def _not_tried(self, rest: list[PdfCandidate], why: str) -> None:
        """Candidates left when a limit is reached. Counted, and named in one warning, not one each."""
        self.stats.not_tried += len(rest)
        for candidate in rest:
            self.result.reports.append(PdfReport(candidate.url, Outcome.NOT_TRIED, candidate.via, candidate.found_on, detail=why))
        self.result.warnings.append(f"{len(rest)} more PDF(s) were not tried: {why}")


async def read_pdfs(
    candidates: list[PdfCandidate],
    home: str,
    *,
    fetcher: Optional[SiteFetcher] = None,
    limits: Optional[PdfLimits] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ocr: Optional[OcrStatus] = None,
) -> PdfReadResult:
    """Download and read `candidates` in the order given, until a limit is reached.

    `home` is the company's website; a file is downloaded only from its domain, or from a host that
    a page of the company links to (the candidate's `found_on`). A `fetcher` passed in keeps its own
    size cap.
    """
    limits = limits or PdfLimits.from_settings()
    status = ocr if ocr is not None else ocr_status()
    async with (fetcher or SiteFetcher(max_bytes=limits.max_bytes, concurrency=1)) as site:
        return await _Reader(site, home, limits, clock, sleep, status).run(candidates)


async def read_company_pdfs(
    crawl: CrawlResult,
    *,
    fetcher: Optional[SiteFetcher] = None,
    limits: Optional[PdfLimits] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ocr: Optional[OcrStatus] = None,
) -> PdfReadResult:
    """Find the PDFs in a crawl of a company site, rank them, and read the best ones.

    Returns one `PdfPageDocument` per PDF page kept, with the file's address and the page number,
    a report per file, stats and warnings. A file that cannot be used (wrong host, not a PDF, too
    large, password protected, unreadable) is skipped with a warning and never raises.
    """
    return await read_pdfs(
        find_pdfs(crawl), crawl.home_url or crawl.start_url, fetcher=fetcher, limits=limits, clock=clock, sleep=sleep, ocr=ocr
    )
