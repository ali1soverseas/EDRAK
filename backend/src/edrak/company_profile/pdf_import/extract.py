"""Read a PDF into one piece of text per page.

pymupdf4llm turns each page into markdown, which keeps headings, tables and the reading order across
columns. A page with almost no text but an image on it is a scan, and is read with OCR when
Tesseract is installed. Arabic that comes out in the wrong order is retried with plain extraction.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

from edrak.company_profile.pdf_import import ocr as ocr_module
from edrak.company_profile.pdf_import.arabic import (
    MIN_ARABIC_LETTERS,
    arabic_letter_count,
    letters_reversed,
    words_reversed,
)
from edrak.company_profile.pdf_import.errors import PdfRejected
from edrak.core.config import settings

logger = logging.getLogger(__name__)

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class PageText:
    number: int  # 1-based
    text: str
    title: str = ""
    links: list[str] = field(default_factory=list)  # URLs of hyperlinks on the page
    scanned: bool = False
    ocr: bool = False


@dataclass
class ExtractedPdf:
    pages: list[PageText] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page_count: int = 0  # pages in the file, including any that were not read


def first_heading(markdown: str) -> str:
    """The text of the first markdown heading, without emphasis marks."""
    match = _HEADING.search(markdown or "")
    return re.sub(r"[*_`]+", "", match.group(1)).strip() if match else ""


def page_ranges(numbers: list[int]) -> str:
    """[1, 2, 3, 7, 9, 10] as "1-3, 7, 9-10"."""
    spans: list[str] = []
    start = prev = None
    for n in sorted(numbers):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            spans.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = n
    if start is not None:
        spans.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(spans)


def open_pdf(path: str, *, page_limit: Optional[int] = None) -> Any:
    """Open the PDF at `path`, or raise PdfRejected for a file that is corrupt, locked, empty or too long.

    A file over the upload page limit is refused unless `page_limit` is given, which leaves the
    choice of how many pages to read to the caller.
    """
    import pymupdf

    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # PyMuPDF raises several types for a damaged file
        raise PdfRejected("corrupt", "This file could not be read as a PDF. It may be damaged.") from exc
    if doc.needs_pass:  # a PDF with only an owner password opens, and is read
        doc.close()
        raise PdfRejected("encrypted", "This PDF is password protected. Remove the password and upload it again.")
    if doc.page_count == 0:
        doc.close()
        raise PdfRejected("corrupt", "This PDF has no pages.")
    if page_limit is None and doc.page_count > settings.PROFILE_IMPORT_PDF_MAX_PAGES:
        count = doc.page_count
        doc.close()
        raise PdfRejected(
            "too_many_pages",
            f"This PDF has {count} pages. The limit is {settings.PROFILE_IMPORT_PDF_MAX_PAGES}.",
        )
    return doc


def _markdown_by_page(doc: Any, count: Optional[int] = None) -> Optional[dict[int, str]]:
    """Markdown for the first `count` pages (all by default) in one pass, or None when pymupdf4llm
    cannot handle the document."""
    try:
        import pymupdf4llm

        pages = list(range(count)) if count is not None and count < doc.page_count else None
        chunks = pymupdf4llm.to_markdown(doc, pages=pages, page_chunks=True, use_ocr=False)
        return {i + 1: str(chunk.get("text") or "") for i, chunk in enumerate(chunks)}
    except Exception as exc:
        logger.warning("pymupdf4llm failed, falling back to plain text: %s", exc)
        return None


def _plain(page: Any) -> str:
    return unicodedata.normalize("NFKC", str(page.get_text("text", sort=True)))


def _page_links(page: Any) -> list[str]:
    try:
        return [str(link["uri"]) for link in page.get_links() if link.get("uri")]
    except Exception:
        return []


def _arabic_in_order(markdown: str, page: Any, number: int, warnings: list[str]) -> str:
    """`markdown`, or the page's plain text when the markdown reads backwards.

    Plain extraction is the retry. If that reads backwards too it is kept all the same and the
    draft warns, since there is no better source for the page.
    """
    if arabic_letter_count(markdown) < MIN_ARABIC_LETTERS:
        return markdown
    plain = _plain(page)
    if not (letters_reversed(markdown) or words_reversed(markdown, plain)):
        return markdown
    if letters_reversed(plain):
        warnings.append(f"The Arabic text on page {number} may be in reverse order.")
    return plain


def extract_document(
    path: str, *, ocr: Optional[ocr_module.OcrStatus] = None, page_limit: Optional[int] = None
) -> ExtractedPdf:
    """Per-page text of the PDF at `path`. Raises PdfRejected; everything else becomes a warning.

    Without `page_limit` a file over the upload page limit is refused. With it, only the first
    `page_limit` pages are read and a warning says so.
    """
    out = ExtractedPdf()
    status = ocr if ocr is not None else ocr_module.ocr_status()
    doc = open_pdf(path, page_limit=page_limit)
    try:
        out.page_count = doc.page_count
        read = doc.page_count if page_limit is None else min(doc.page_count, max(1, page_limit))
        if read < doc.page_count:
            out.warnings.append(f"The document has {doc.page_count} pages; only the first {read} were read.")
        markdown = _markdown_by_page(doc) if read == doc.page_count else _markdown_by_page(doc, read)
        if markdown is None:
            out.warnings.append("Headings and tables could not be read, so plain text was used.")
        no_ocr: list[int] = []
        unreadable: list[int] = []
        arabic_warnings: list[str] = []

        for index in range(read):
            number = index + 1
            page = doc[index]
            plain = _plain(page)
            # A scan is a page that is almost all picture.
            scanned = len(plain.strip()) < settings.PROFILE_IMPORT_SCANNED_MIN_CHARS and bool(page.get_images())
            text = markdown.get(number, "") if markdown is not None else plain
            used_ocr = False

            if scanned:
                if status.available:
                    try:
                        found = unicodedata.normalize("NFKC", ocr_module.ocr_page(page)).strip()
                    except Exception as exc:
                        logger.warning("OCR failed on page %d: %s", number, exc)
                        found = ""
                    if found:
                        text, used_ocr = found, True
                    else:
                        unreadable.append(number)
                else:
                    no_ocr.append(number)
            if not used_ocr and text.strip():
                text = _arabic_in_order(text, page, number, arabic_warnings)
            text = unicodedata.normalize("NFKC", text).strip()
            if not text:
                continue
            out.pages.append(
                PageText(
                    number=number,
                    text=text,
                    title=first_heading(text),
                    links=_page_links(page),
                    scanned=scanned,
                    ocr=used_ocr,
                )
            )

        if no_ocr:
            out.warnings.append(
                f"Page(s) {page_ranges(no_ocr)} are mostly scanned images and OCR is not available on this "
                "server, so the text in those images was not read."
            )
        if unreadable:
            out.warnings.append(f"No text could be read from the scanned page(s) {page_ranges(unreadable)}.")
        out.warnings.extend(arabic_warnings)
    finally:
        doc.close()
    return out
