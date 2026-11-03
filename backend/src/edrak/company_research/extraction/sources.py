"""Source documents for extraction, and how they are grouped into model calls.

A document is a crawled page or one PDF page. Documents are grouped in the order given, each group
within a character budget, and a document is never split across groups. A page that only repeats the
company's profile is left out; a page that also says something else is kept whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from edrak.agents.customer_trends.utils.text import phrase_pattern, search_key
from edrak.company_profile.context import SEPARATOR, TRUNCATION_MARK
from edrak.company_profile.models import ProfileFields
from edrak.company_research.crawler import CrawlResult
from edrak.company_research.models import SourceTier
from edrak.company_research.pdfs import PdfReadResult
from edrak.guardrails.content import is_high_risk, sanitize, wrap

_LABEL_CHARS = 200
_CLOSING_TAG = re.compile(r"</\s*untrusted_source[^>]*>?", re.IGNORECASE)
_WORD = re.compile(r"\w+")
_SENTENCE = re.compile(r"[.!?؟\n]+")
# What the PDF reader's markdown adds around text it took from a picture or a table cell.
_PDF_MARKUP = re.compile(r"<!--.*?-->|<br\s*/?>", re.IGNORECASE | re.DOTALL)

# A sentence is covered by the profile when, with the profile's own words taken out, at most this
# many content words are left ("builds", but not "founded 2009"). A page is covered when it uses the
# profile's words and every sentence is covered, so one line that says anything more keeps it.
COVERED_RESIDUE_WORDS = 1
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in into is it its of on or our that the their this to was we were "
    "which who will with you your all any more most new also can "
    "في من على إلى الى عن مع هو هي هذا هذه التي الذي أن ان و أو او كل بين لدى كما قد ما لا".split()
)


@dataclass
class SourceDoc:
    """One piece of source text. `id`, and the text that is sent, are set when documents are batched."""

    url: str
    text: str
    tier: SourceTier = SourceTier.OWN_SITE
    pdf_page: Optional[int] = None
    title: str = ""
    ocr: bool = False  # read from a scan, so a quote may differ from the text by a few characters
    id: str = ""
    clean: str = ""  # `text` without instruction-like lines; quotes are checked against it


@dataclass
class Batch:
    documents: list[SourceDoc] = field(default_factory=list)
    text: str = ""


@dataclass
class Batched:
    batches: list[Batch] = field(default_factory=list)
    skipped_empty: int = 0
    skipped_risky: int = 0
    skipped_profile: int = 0
    skipped_over_limit: int = 0  # documents left out because the run's call limit was reached
    cut: int = 0  # documents longer than one call, sent cut
    removed_lines: int = 0


def documents_from_crawl(crawl: CrawlResult) -> list[SourceDoc]:
    """The crawled pages, in the order read, as own-site documents."""
    return [SourceDoc(url=p.url, text=p.text, tier=SourceTier.OWN_SITE, title=p.title) for p in crawl.pages]


def documents_from_pdfs(pdfs: PdfReadResult) -> list[SourceDoc]:
    """The PDF pages kept, as own-PDF documents carrying their page number."""
    return [
        SourceDoc(url=d.source_url, text=strip_pdf_markup(d.text), tier=SourceTier.OWN_PDF, pdf_page=d.pdf_page, title=d.title, ocr=d.ocr)
        for d in pdfs.documents
    ]


def strip_pdf_markup(text: str) -> str:
    """PDF page text without HTML comments (the reader marks text taken from pictures with them) and with line
    tags as line breaks. A model leaves them out of a quote, and they say nothing about the company."""
    return _PDF_MARKUP.sub(lambda m: "\n" if m.group(0).lower().startswith("<br") else "", text)


# ------------------------------------------------------------------ the profile

def _profile_phrases(profile: ProfileFields) -> list[re.Pattern[str]]:
    values = [profile.name or "", *profile.aliases, *profile.products, *profile.markets, *profile.goals, profile.industry or ""]
    values += _SENTENCE.split(profile.notes or "")
    phrases = {search_key(v).strip() for v in values if v and v.strip()}
    longest_first = sorted((p for p in phrases if len(p) >= 2), key=len, reverse=True)
    return [phrase_pattern(p) for p in longest_first]  # whole words for Latin text, as the profile import matches names


def _content_words(text: str) -> int:
    return sum(1 for w in _WORD.findall(text) if w not in _STOPWORDS and (len(w) > 2 or w.isdigit()))


def covered_by_profile(text: str, profile: Optional[ProfileFields]) -> bool:
    """Whether `text` says nothing beyond the profile's own fields.

    Each sentence is read with the profile's names, products, markets, goals and description taken
    out. A sentence with at most one content word left only restates the profile. The page is
    covered when it uses the profile's words and every sentence is of that kind.
    """
    if profile is None:
        return False
    phrases = _profile_phrases(profile)
    if not phrases:
        return False
    used_profile = False
    for sentence in _SENTENCE.split(search_key(text)):
        rest = sentence
        for phrase in phrases:
            rest, found = phrase.subn(" ", rest)
            used_profile = used_profile or bool(found)
        if _content_words(rest) > COVERED_RESIDUE_WORDS:
            return False
    return used_profile


# ------------------------------------------------------------------ batching

def _label(value: str) -> str:
    return sanitize(" ".join(value.split()))[0][:_LABEL_CHARS]


def _block(doc: SourceDoc, body: str) -> str:
    page = f" page={doc.pdf_page}" if doc.pdf_page else ""
    title = f" title={_label(doc.title)}" if doc.title else ""
    header = f"[{doc.id}] tier={doc.tier.value} url={_label(doc.url)}{page}{title}\n"
    return header + wrap(body, doc.id)


def make_batches(
    documents: Sequence[SourceDoc], budget: int, max_batches: int, profile: Optional[ProfileFields] = None
) -> Batched:
    """Group `documents` into batches of at most `budget` characters, in order, without splitting any.

    Documents are given the ids S1, S2, ... in the order kept. One that does not fit with the others
    starts the next batch; one that is longer than a whole batch is sent alone, cut to fit. Empty
    pages, pages that are mostly instructions to the model, and pages that only restate the profile
    are left out. At most `max_batches` batches are made; the documents after them are counted.
    """
    out = Batched()
    kept: list[SourceDoc] = []
    for original in documents:
        if not original.text.strip():
            out.skipped_empty += 1
            continue
        if is_high_risk(original.text):
            out.skipped_risky += 1
            continue
        if covered_by_profile(original.text, profile):
            out.skipped_profile += 1
            continue
        body, removed = sanitize(original.text.strip())
        out.removed_lines += removed
        doc = SourceDoc(
            url=original.url, text=original.text, tier=original.tier, pdf_page=original.pdf_page,
            title=original.title, ocr=original.ocr, id=f"S{len(kept) + 1}", clean=_CLOSING_TAG.sub("", body),
        )
        kept.append(doc)

    current: list[str] = []
    members: list[SourceDoc] = []
    used = 0

    def close() -> None:
        nonlocal current, members, used
        if members:
            out.batches.append(Batch(members, SEPARATOR.join(current)))
        current, members, used = [], [], 0

    for index, doc in enumerate(kept):
        block = _block(doc, doc.clean)
        if len(block) > budget:
            room = budget - len(_block(doc, "")) - len(TRUNCATION_MARK)
            out.cut += 1
            block = _block(doc, doc.clean[: max(room, 0)].rstrip() + TRUNCATION_MARK)
        gap = len(SEPARATOR) if members else 0
        if members and used + gap + len(block) > budget:
            close()
            gap = 0
        if len(out.batches) >= max_batches:  # the call limit is reached; this and the rest are not sent
            out.skipped_over_limit = len(kept) - index
            break
        current.append(block)
        members.append(doc)
        used += gap + len(block)
    close()
    return out
