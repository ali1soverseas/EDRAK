"""A mocked company website that serves PDFs generated in the test, for the company PDF tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pdf_fixtures as pdfs
from crawl_fixtures import PRIVATE_HOST, Site, crawl

from edrak.company_profile.pdf_import.ocr import OcrStatus
from edrak.company_research.crawler import CrawlResult
from edrak.company_research.pdfs import PdfLimits, read_company_pdfs

NO_OCR = OcrStatus(False, "the Tesseract program is not installed")
HOME = "https://acme.example/"
CDN = "cdn.acme-files.example"
PDF_TYPE = "application/pdf"


def pdf_limits(**overrides) -> PdfLimits:
    values = dict(max_pdfs=8, max_bytes=20 * 1024 * 1024, max_pages=60, max_attempts=24, context_chars=48000, host_delay_s=0.0)
    values.update(overrides)
    return PdfLimits(**values)


def home(body: str, title: str = "Acme Pay") -> str:
    return f"<html lang='en'><head><title>{title}</title></head><body><main><h1>{title}</h1><p>{'Acme Pay builds payment terminals. ' * 12}</p>{body}</main></body></html>"


def served(content: bytes, kind: str = PDF_TYPE) -> tuple:
    return (200, content, kind)


class Files:
    """PDFs built once per test, each different from the others."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def single(self, name: str = "a") -> bytes:
        return pdfs.english_single(self.folder / f"{name}.pdf").read_bytes()

    def numbered(self, number: int, pages: int = 2) -> bytes:
        """A PDF whose text is its own, so no two have the same bytes."""
        return pdfs.long_document(self.folder / f"n{number}.pdf", pages, {1: f"Report number {number}"}).read_bytes()

    def deck(self) -> bytes:
        return pdfs.deck(self.folder / "deck.pdf").read_bytes()

    def encrypted(self) -> bytes:
        return pdfs.encrypted(self.folder / "locked.pdf").read_bytes()

    def long(self, pages: int, keyword_pages: dict[int, str] | None = None) -> bytes:
        return pdfs.long_document(self.folder / f"long{pages}.pdf", pages, keyword_pages or {}).read_bytes()

    def scanned(self) -> bytes:
        return pdfs.image_only(self.folder / "scan.pdf").read_bytes()

    def arabic(self) -> bytes:
        return pdfs.arabic(self.folder / "ar.pdf").read_bytes()


def website(body: str, files: dict | None = None, **pages) -> Site:
    """A site whose homepage has `body`. `files` maps a path or "host/path" to the bytes or response served."""
    entries = {"/": home(body), "/sitemap.xml": (404, "", "text/plain")}
    entries.update({(path if path.startswith('/') else '/' + path): page for path, page in pages.items()})
    for path, content in (files or {}).items():
        entries[path] = content if isinstance(content, tuple) else served(content)
    return Site(entries)


def crawl_and_read(site: Site, *, limits=None, crawl_limits=None, ocr=NO_OCR, clock=None, sleep=None, **crawl_kwargs):
    """Crawl the site, then read its PDFs. Returns (crawl result, PDF result)."""
    limits = limits or pdf_limits()
    if crawl_limits is not None:
        crawl_kwargs["limits"] = crawl_limits
    crawled = crawl(site, **crawl_kwargs)
    extra = {}
    if clock is not None:
        extra = dict(clock=clock, sleep=sleep)
    result = asyncio.run(read_company_pdfs(crawled, fetcher=site.fetcher(max_bytes=limits.max_bytes), limits=limits, ocr=ocr, **extra))
    return crawled, result


def outcomes(result) -> dict[str, str]:
    """url -> outcome value, for every file the run reported."""
    return {r.url: r.outcome.value for r in result.reports}


def crawl_of(*links: tuple[str, str, str], pages: int = 1) -> CrawlResult:
    """A crawl result by hand: (url, text, found_on) PDF links on an Acme page, for ranking and allowlist tests."""
    from edrak.company_research.crawler import CrawledPage, FetchMode, PdfLink

    result = CrawlResult(start_url=HOME, home_url=HOME)
    result.pages.append(CrawledPage(HOME, "Acme", "en", "text", FetchMode.STATIC))
    for url, text, found_on in links:
        result.pdf_links.append(PdfLink(url, text, found_on))
    return result


__all__ = [
    "CDN", "HOME", "NO_OCR", "PDF_TYPE", "PRIVATE_HOST", "Files", "crawl_and_read", "crawl_of", "home", "outcomes",
    "pdf_limits", "served", "website",
]
