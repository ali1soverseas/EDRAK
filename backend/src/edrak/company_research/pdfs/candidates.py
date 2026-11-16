"""Which PDFs a crawled site offers, which of them may be downloaded, and in what order to read them.

A PDF is a candidate when a page links to a .pdf file, shows one in a frame, opens one in a viewer
page, or has a download button or link that may lead to one (the download itself settles that, by
the type the server answers with). Candidates are ranked by their link text and file name, by the
page they were found on, and then by the latest year in the name.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable, Optional
from urllib.parse import unquote, urlsplit

from edrak.agents.competitor_intelligence.socials import platform_of
from edrak.agents.customer_trends.utils.text import phrase_pattern, search_key
from edrak.company_research.crawler import CrawlResult
from edrak.company_research.crawler.pdf_links import has_pdf_extension, viewer_target
from edrak.company_research.crawler.rules import in_scope, page_key, score as section_of, skip_reason
from edrak.company_research.pdfs.models import PdfCandidate, Via

# Words that mark a link as a download of a company document.
DOWNLOAD_WORDS = (
    "download", "تحميل", "تنزيل", "brochure", "company profile", "annual report", "البروشور", "الملف التعريفي",
    "التقرير السنوي",
)
_DOWNLOAD = [phrase_pattern(w) for w in DOWNLOAD_WORDS]

# What a document is called, by how much it says about the company.
STRONG_WORDS = (
    "annual report", "integrated report", "company profile", "corporate profile", "brochure", "investor presentation",
    "fact sheet", "factsheet", "sustainability report", "esg report", "التقرير السنوي", "تقرير سنوي", "الملف التعريفي",
    "البروشور", "تقرير الاستدامة",
)
MEDIUM_WORDS = (
    "profile", "overview", "presentation", "report", "capabilities", "catalog", "catalogue", "results", "about us",
    "عرض تقديمي", "تقرير", "نبذة", "كتيب",
)
JUNK_WORDS = (
    "terms", "conditions", "privacy", "cookie", "cookies", "policy", "application form", "form", "resume", "cv",
    "الشروط", "الخصوصية", "سياسة", "نموذج",
)
_STRONG = [phrase_pattern(w) for w in STRONG_WORDS]
_MEDIUM = [phrase_pattern(w) for w in MEDIUM_WORDS]
_JUNK = [phrase_pattern(w) for w in JUNK_WORDS]

STRONG_POINTS = 6
MEDIUM_POINTS = 3
JUNK_POINTS = -8
EXTENSION_POINTS = 2  # the address itself says it is a PDF
SAME_SITE_POINTS = 1
# The page a file was found on: the pages where a company keeps its documents come first.
PAGE_POINTS = {"investors": 5, "resources": 4, "media": 3, "about": 3, "sustainability": 2, "history": 1}

_YEAR = re.compile(r"(?<!\d)((?:19[89]|20[0-9])\d)(?!\d)")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


def file_name(url: str) -> str:
    """The name of the file at `url`, without its extension, as words."""
    last = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    stem = last[:-4] if last.lower().endswith(".pdf") else last
    return " ".join(_CAMEL.sub(" ", re.sub(r"[-_+.]+", " ", stem)).split())


def latest_year(text: str, current: int) -> int:
    """The latest year in `text` that is not in the future (next year is allowed), or 0."""
    years = [int(y) for y in _YEAR.findall(text) if int(y) <= current + 1]
    return max(years, default=0)


def _hits(patterns: list[re.Pattern[str]], key: str) -> bool:
    return any(p.search(key) for p in patterns)


def score_candidate(candidate: PdfCandidate, home: str, current_year: int) -> tuple[int, int]:
    """(points, year) for a candidate. Points come from the link text and file name, the page it was
    found on, and the address; the year is used to order candidates with equal points."""
    label = f"{candidate.text} {file_name(candidate.url)}"
    key = search_key(label)
    points = 0
    if _hits(_STRONG, key):
        points += STRONG_POINTS
    elif _hits(_MEDIUM, key):
        points += MEDIUM_POINTS
    if _hits(_JUNK, key):
        points += JUNK_POINTS
    points += PAGE_POINTS.get(candidate.page_section, 0)
    if candidate.via in (Via.EXTENSION, Via.VIEWER, Via.EMBED):
        points += EXTENSION_POINTS
    if in_scope(candidate.url, home):
        points += SAME_SITE_POINTS
    return points, latest_year(label, current_year)


def is_download_link(url: str, text: str) -> bool:
    """Whether a link's text or path says it downloads a company document."""
    key = search_key(f"{text} {unquote(urlsplit(url).path).replace('-', ' ').replace('_', ' ')}")
    return _hits(_DOWNLOAD, key)


def allowed_source(candidate: PdfCandidate, home: str) -> bool:
    """Whether the file may be downloaded: it is on the company's domain, or a page of the company links it.

    That is what lets a file sit on a CDN or file host. A file on another host that no page of the
    company points to (it was found some other way) is not allowed.
    """
    return in_scope(candidate.url, home) or (
        urlsplit(candidate.found_on).scheme in ("http", "https") and in_scope(candidate.found_on, home)
    )


def _via(link_via: str, unwrapped: bool, url: str) -> Via:
    if link_via == "embed":
        return Via.EMBED
    if link_via == "sitemap":
        return Via.SITEMAP
    if unwrapped:
        return Via.VIEWER
    return Via.EXTENSION if has_pdf_extension(url) else Via.DOWNLOAD


def collect_candidates(crawl: CrawlResult) -> list[PdfCandidate]:
    """Every PDF the crawl found, once each, in the order found. Not ranked."""
    sections = {page.url: page.section for page in crawl.pages}
    pages_read = {page_key(page.url) for page in crawl.pages}
    found: dict[str, PdfCandidate] = {}

    def add(url: str, text: str, found_on: str, link_via: str) -> None:
        target = viewer_target(url)
        final = target or url
        section = (sections.get(found_on) or section_of(found_on)[1]) if found_on.startswith("http") else "other"
        candidate = PdfCandidate(final, text, found_on, _via(link_via, target is not None, final), section)
        known = found.get(final)
        if known is None:
            found[final] = candidate
            return
        known.text = known.text or text
        if PAGE_POINTS.get(section, 0) > PAGE_POINTS.get(known.page_section, 0):
            known.found_on, known.page_section = found_on, section

    for link in crawl.pdf_links:
        add(link.url, link.text, link.found_on, link.via)
    for page in crawl.pages:
        for link in page.links:
            if viewer_target(link.url):
                add(link.url, link.text, page.url, "link")
            elif (
                is_download_link(link.url, link.text)
                and page_key(link.url) not in pages_read
                and skip_reason(link.url) in (None, "pdf")
                and not platform_of(link.url)
            ):
                add(link.url, link.text, page.url, "link")
    return list(found.values())


def rank_candidates(candidates: Iterable[PdfCandidate], home: str, today: Optional[date] = None) -> list[PdfCandidate]:
    """The candidates best first: points, then the latest year in the name, then the order found."""
    current = (today or date.today()).year
    ranked = list(candidates)
    for candidate in ranked:
        candidate.score, candidate.year = score_candidate(candidate, home, current)
    order = {id(c): i for i, c in enumerate(ranked)}
    return sorted(ranked, key=lambda c: (-c.score, -c.year, order[id(c)]))


def find_pdfs(crawl: CrawlResult, today: Optional[date] = None) -> list[PdfCandidate]:
    """The PDFs a crawl found, ranked, read first at the front."""
    return rank_candidates(collect_candidates(crawl), crawl.home_url or crawl.start_url, today)
