"""Choose which pages of a long PDF are sent to the model."""

from __future__ import annotations

import re

from edrak.agents.customer_trends.utils.text import phrase_pattern, search_key
from edrak.company_profile.models import SourceDocument, SourceType
from edrak.company_profile.context import SEPARATOR, pack_context
from edrak.company_profile.pdf_import.extract import PageText, page_ranges
from edrak.core.config import settings

ALWAYS_KEPT = 3  # leading pages: the cover, the contents, the opening statement

_KEYWORDS = (
    "about", "about us", "who we are", "overview", "company profile", "product", "products", "service", "services",
    "solution", "solutions", "market", "markets", "client", "clients", "customers", "vision", "mission",
    "من نحن", "نبذة", "منتجاتنا", "خدماتنا", "حلولنا", "الأسواق", "عملاؤنا", "عملائنا", "رؤيتنا", "رسالتنا",
)
_PATTERNS = [phrase_pattern(k) for k in _KEYWORDS]
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_HEADING_WEIGHT = 3
_BODY_HITS_MAX = 5


def score_page(text: str) -> int:
    """Keyword hits: a heading that names a topic counts more than the word turning up in the body."""
    headings = search_key(" ".join(_HEADING_LINE.findall(text)))
    body = search_key(text)
    heading_hits = sum(1 for p in _PATTERNS if p.search(headings))
    body_hits = sum(1 for p in _PATTERNS if p.search(body))
    return _HEADING_WEIGHT * heading_hits + min(body_hits, _BODY_HITS_MAX)


def page_document(page: PageText) -> SourceDocument:
    """One SourceDocument for a page, in reading order (the page number is its priority)."""
    return SourceDocument(
        source_type=SourceType.PDF_PAGE,
        ref=f"p.{page.number}",
        title=page.title,
        text=page.text,
        priority=page.number,
    )


def _rendered_size(document: SourceDocument) -> int:
    """How many characters the core's context spends on this page, labels and markers included."""
    return len(pack_context([document], budget=10**9).text)


def select_pages(pages: list[PageText], budget: int | None = None) -> tuple[list[PageText], list[str]]:
    """The pages to send, in page order, and warnings naming any that were left out.

    Everything is sent when it fits `budget`. Otherwise the first three pages are kept, the rest are
    ranked by keyword score (earlier pages first on a tie) and added while they fit.
    """
    limit = settings.PROFILE_IMPORT_CONTEXT_CHARS if budget is None else budget
    sizes = {p.number: _rendered_size(page_document(p)) for p in pages}
    if sum(sizes.values()) + len(SEPARATOR) * max(len(pages) - 1, 0) <= limit:
        return pages, []

    chosen = pages[:ALWAYS_KEPT]
    used = sum(sizes[p.number] for p in chosen) + len(SEPARATOR) * max(len(chosen) - 1, 0)
    ranked = sorted(pages[ALWAYS_KEPT:], key=lambda p: (-score_page(p.text), p.number))
    for page in ranked:
        cost = sizes[page.number] + len(SEPARATOR)
        if used + cost <= limit:
            chosen.append(page)
            used += cost
    chosen.sort(key=lambda p: p.number)
    dropped = [p.number for p in pages if p not in chosen]
    warnings = []
    if dropped:
        warnings.append(
            f"The document is longer than the size limit, so page(s) {page_ranges(dropped)} were left out."
        )
    return chosen, warnings
