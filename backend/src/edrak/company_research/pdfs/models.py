"""What the PDF stage returns: one document per PDF page kept, a report per file, stats and warnings."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Via(str, Enum):
    """How a PDF was found."""

    EXTENSION = "extension"  # a link to a .pdf file
    VIEWER = "viewer"  # the file named in a viewer page's address
    EMBED = "embed"  # an iframe, embed or object
    DOWNLOAD = "download"  # a download button or link whose address does not say it is a PDF
    SITEMAP = "sitemap"


class Outcome(str, Enum):
    READ = "read"
    NOT_ALLOWED = "not_allowed"  # the host is not the company's and no page of the company links it
    ROBOTS = "robots"
    UNREACHABLE = "unreachable"
    NOT_PDF = "not_pdf"  # the response is not a PDF (its type, or its first bytes)
    TOO_LARGE = "too_large"
    ENCRYPTED = "encrypted"
    CORRUPT = "corrupt"
    DUPLICATE = "duplicate"  # the same bytes as a file already read
    EMPTY = "empty"  # no text could be read
    NOT_TRIED = "not_tried"  # the run's limits were reached first


@dataclass
class PdfCandidate:
    url: str  # the file, with a viewer page already unwrapped
    text: str = ""  # link text, or the label of the frame
    found_on: str = ""  # the page that shows or links it
    via: Via = Via.EXTENSION
    page_section: str = "other"  # the section the finding page belongs to
    score: int = 0
    year: int = 0  # the latest year in the file name or link text; 0 when there is none


@dataclass
class PdfPageDocument:
    """The text of one PDF page, with what a later step needs to cite it."""

    source_url: str
    pdf_page: int  # 1-based
    title: str  # the page's first heading, or ""
    text: str
    found_on: str = ""
    ocr: bool = False


@dataclass
class PdfReport:
    url: str
    outcome: Outcome
    via: Via
    found_on: str = ""
    pages_total: int = 0
    pages_kept: int = 0
    detail: str = ""


@dataclass
class PdfStats:
    candidates: int = 0
    attempted: int = 0  # downloads started
    read: int = 0
    pages_kept: int = 0
    ocr_pages: int = 0
    not_allowed: int = 0
    robots: int = 0
    unreachable: int = 0
    not_pdf: int = 0
    too_large: int = 0
    encrypted: int = 0
    corrupt: int = 0
    duplicate: int = 0
    empty: int = 0
    not_tried: int = 0
    elapsed_s: float = 0.0


@dataclass
class PdfReadResult:
    documents: list[PdfPageDocument] = field(default_factory=list)
    reports: list[PdfReport] = field(default_factory=list)
    stats: PdfStats = field(default_factory=PdfStats)
    warnings: list[str] = field(default_factory=list)
