"""Which URLs a crawl of one company website follows, and in what order.

Scope is the registered domain of the starting page, with its subdomains. Within it, login, signup,
cart, checkout, search and form pages, files other than HTML and PDF, and query-string copies of a
page are left out. Priority comes from the website import's keyword scoring with more section words.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from edrak.company_profile.url_import.discover import (
    BLOCKED_SEGMENTS,
    DATED_POST,
    LANG_QUERY,
    LANG_SEGMENT,
    SECTIONS,
    SectionScorer,
    Sections,
)
from edrak.company_profile.url_import.urls import path_of, registered_domain

# Section words added to the website import's table: (weight, English and Arabic keywords).
EXTRA_SECTIONS: Sections = {
    "investors": (4, ("investors", "investor", "investor relations", "المستثمرين", "علاقات المستثمرين")),
    "team": (4, ("team", "leadership", "board", "فريق العمل", "مجلس الإدارة")),
    "resources": (3, ("resources", "downloads", "download", "reports", "report", "annual report", "الموارد", "التحميلات", "التقارير")),
    "media": (3, ("media", "press", "press releases", "المركز الإعلامي")),
    "history": (3, ("history", "our history", "تاريخنا")),
    "locations": (3, ("branches", "locations", "فروعنا")),
    "certifications": (3, ("certifications", "certification", "الشهادات")),
    "sustainability": (3, ("sustainability", "الاستدامة")),
    # Only the index page: a job ad under /careers/ is skipped by `skip_reason`.
    "careers": (1, ("careers", "career")),
}
CRAWL_SECTIONS: Sections = {**SECTIONS, **EXTRA_SECTIONS}
SCORER = SectionScorer(CRAWL_SECTIONS)

_SKIPPED_SEGMENTS = BLOCKED_SEGMENTS | frozenset(
    {"search", "results", "find", "logout", "log-out", "forgot-password", "reset-password", "password",
     "subscribe", "newsletter", "wishlist", "compare", "order", "orders", "payment", "pay", "form", "forms",
     "apply", "booking", "wp-json", "wp-content", "wp-includes", "cdn-cgi", "xmlrpc",
     "تسجيل-الدخول", "تسجيل", "البحث", "السلة", "الدفع"}
)
# A careers page is fine, a job ad under it is not.
_JOB_PARENTS = frozenset({"careers", "career", "jobs", "job", "vacancies", "وظائف"})
_SEARCH_PARAMS = frozenset({"s", "q", "query", "search", "keyword", "keywords", "searchterm", "term"})
_FILE_EXTENSION = re.compile(r"\.([a-z0-9]{2,5})$", re.IGNORECASE)
_PAGE_EXTENSIONS = frozenset({"html", "htm", "xhtml", "shtml", "php", "asp", "aspx", "jsp", "jspx", "cfm"})
_INDEX_FILE = re.compile(r"/index\.(html?|php|aspx?)$", re.IGNORECASE)


def in_scope(url: str, home: str) -> bool:
    """Whether `url` is on the registered domain of `home` (a URL), including its subdomains."""
    host = (urlsplit(url).hostname or "").lower()
    return bool(host) and registered_domain(host) == registered_domain((urlsplit(home).hostname or "").lower())


def is_pdf_link(url: str, text: str = "") -> bool:
    """Whether a link looks like a PDF: a .pdf path, a .pdf in the query, or "PDF" in its text."""
    parts = urlsplit(url)
    if unquote(parts.path).lower().endswith(".pdf") or ".pdf" in unquote(parts.query).lower():
        return True
    return bool(re.search(r"(?<![a-z])pdf(?![a-z])", text.lower()))


def skip_reason(url: str) -> Optional[str]:
    """Why a crawl does not read `url` as a page, or None when it should."""
    parts = urlsplit(url)
    path = path_of(url)
    if is_pdf_link(url):
        return "pdf"  # collected as a candidate, never fetched as a page
    last = path.rsplit("/", 1)[-1]
    extension = _FILE_EXTENSION.search(last)
    if extension and extension.group(1).lower() not in _PAGE_EXTENSIONS:
        return "file"
    if DATED_POST.search(path):
        return "archive"
    segments = [s.lower().rsplit(".", 1)[0] if "." in s else s.lower() for s in path.split("/") if s]
    if any(s in _SKIPPED_SEGMENTS for s in segments):
        return "page type"
    content = [s for i, s in enumerate(segments) if not (i == 0 and LANG_SEGMENT.match(s))]
    if len(content) >= 2 and content[0] in _JOB_PARENTS:
        return "job ad"
    if any(k.lower() in _SEARCH_PARAMS for k, _ in parse_qsl(parts.query)):
        return "search"
    return None


def page_key(url: str) -> str:
    """Identity of a page: host without www, path without a trailing slash or index file, and the
    language parameter only. Other query parameters give variants of the same page, not other pages."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = _INDEX_FILE.sub("/", unquote(parts.path or "/")).rstrip("/").lower() or "/"
    language = [(k.lower(), v.lower()) for k, v in parse_qsl(parts.query) if k.lower() in LANG_QUERY]
    return f"{host}{path}" + (f"?{urlencode(language)}" if language else "")


def twin_key(url: str) -> str:
    """`page_key` with the language marker (/ar/, ar.example.com, ?lang=ar) removed, so the English
    and Arabic copies of a page give the same key."""
    parts = urlsplit(url)
    labels = (parts.hostname or "").split(".")
    if len(labels) > 2 and LANG_SEGMENT.match(labels[0]):
        labels = labels[1:]
    segments = parts.path.split("/")
    for i, segment in enumerate(segments):
        if segment:
            if LANG_SEGMENT.match(segment):
                del segments[i]
            break
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in LANG_QUERY])
    return page_key(urlunsplit((parts.scheme, ".".join(labels), "/".join(segments) or "/", query, "")))


def score(url: str, text: str = "", in_nav: bool = False) -> tuple[int, str]:
    """(score, section) from the section words in the path and link text. The caller has already
    dropped the pages it does not want, so the website import's own junk test is not applied."""
    return SCORER.score(url, text, in_nav, junk=lambda _: False)


# ------------------------------------------------------------------ pages that need a browser

_APP_ROOT = re.compile(r"""<div[^>]+id\s*=\s*["']?(?:root|app|__next|__nuxt|app-root)["']?[^>]*>\s*</div>""", re.IGNORECASE)
_NEEDS_SCRIPT = re.compile(
    r"<noscript[^>]*>[^<]{0,300}?(?:enable|requires?|turn on|تفعيل|تمكين)[^<]{0,60}?(?:javascript|جافا)", re.IGNORECASE
)
_LAZY_MARKER = re.compile(r"load-?more|infinite-?scroll|lazy-?load|data-infinite|js-load-more", re.IGNORECASE)
_MORE_BUTTON = re.compile(r">\s*(?:load more|show more|view all|see more|عرض المزيد|عرض الكل)\s*<", re.IGNORECASE)
_PASSWORD_FIELD = re.compile(r"""<input[^>]+type\s*=\s*["']?password""", re.IGNORECASE)


def client_side_signs(html: str) -> list[str]:
    """What in the page's HTML says its content is built or extended by scripts."""
    signs = []
    if _APP_ROOT.search(html):
        signs.append("empty app root")
    if _NEEDS_SCRIPT.search(html):
        signs.append("asks for JavaScript")
    if _LAZY_MARKER.search(html) or _MORE_BUTTON.search(html):
        signs.append("lazy loading")
    return signs


def is_login_page(html: str) -> bool:
    return bool(_PASSWORD_FIELD.search(html))


def render_reason(text: str, html: str, min_chars: int) -> str:
    """Why a page should be opened in the browser, or "" when the static reading will do."""
    if len(text) < min_chars:
        return f"less than {min_chars} characters of main text"
    return ", ".join(client_side_signs(html))
