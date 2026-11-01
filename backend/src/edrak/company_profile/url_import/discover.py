"""Choose which few pages of a company website are worth reading.

Candidates come from the homepage's links and the sitemap. Each is scored by the words in its path
and in its link text (English and Arabic), junk pages are dropped, and the best few are kept. When
the site is published in English and Arabic, the English page wins each section and the Arabic
about page is added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from lxml import etree

from edrak.agents.customer_trends.utils.text import phrase_pattern, search_key
from edrak.company_profile.models import Language, detect_language
from edrak.company_profile.url_import.parse import Link, language_hint
from edrak.company_profile.url_import.urls import path_of, same_site

ABOUT = "about"
PRODUCTS = "products"
OTHER = "other"

# section -> (weight, keywords). Keywords are matched against the path and the link text.
_SECTIONS: dict[str, tuple[int, tuple[str, ...]]] = {
    "about": (5, ("about", "about us", "company", "who we are", "who-we-are", "our story", "من نحن", "عن الشركة", "عن شركتنا", "نبذة عنا", "نبذة عن")),
    "products": (4, ("products", "product", "services", "service", "solutions", "solution", "platform", "what we do", "منتجاتنا", "المنتجات", "خدماتنا", "الخدمات", "الحلول", "حلولنا")),
    "pricing": (3, ("pricing", "plans", "prices", "الأسعار", "الاسعار", "الباقات")),
    "industries": (2, ("industries", "sectors", "القطاعات")),
    "clients": (2, ("clients", "customers", "partners", "عملاؤنا", "عملائنا", "شركاؤنا")),
    "contact": (2, ("contact", "contact us", "اتصل بنا", "تواصل معنا")),
}
_PATTERNS = {
    section: (weight, [phrase_pattern(word) for word in words]) for section, (weight, words) in _SECTIONS.items()
}

_FILE = re.compile(
    r"\.(pdf|jpe?g|png|gif|svg|webp|ico|zip|rar|gz|docx?|xlsx?|pptx?|mp[34]|avi|mov|css|js|json|xml|txt|woff2?)$", re.I
)
_BLOCKED_SEGMENTS = frozenset(
    {"login", "log-in", "signin", "sign-in", "signup", "sign-up", "register", "cart", "checkout", "basket",
     "account", "my-account", "wp-admin", "wp-login.php", "admin", "tag", "category", "author", "feed",
     "privacy", "privacy-policy", "terms", "terms-of-service", "terms-and-conditions", "cookie", "cookies",
     "cookie-policy", "legal", "policy", "policies", "disclaimer", "gdpr", "الخصوصية", "سياسة-الخصوصية", "الشروط-والاحكام"}
)
# A section index is fine; a page under it is a job ad or an article.
_DETAIL_PARENTS = frozenset(
    {"careers", "career", "jobs", "job", "vacancies", "وظائف", "blog", "blogs", "news", "article", "articles",
     "post", "posts", "press", "press-releases", "media", "insights", "stories", "مدونة", "الاخبار", "أخبار"}
)
_DATED_POST = re.compile(r"/(19|20)\d\d/\d{1,2}(/|$)")
_LANG_SEGMENT = re.compile(r"^(ar|en)(?:[-_][a-z]{2})?$", re.I)
_LANG_QUERY = frozenset({"lang", "l", "locale", "hl", "language"})
_NAV_BONUS = 1
_PER_SECTION = 2  # pages kept per section, so five product pages do not crowd out the rest
_MAX_SITEMAP_URLS = 5000


@dataclass
class Candidate:
    url: str
    text: str = ""
    in_nav: bool = False
    lang: Optional[str] = None  # "ar", "en", or None when nothing says
    score: int = 0
    section: str = OTHER
    key: str = ""

    @property
    def label(self) -> str:
        """`about`, `products` or `other`: how the page ranks when sources are packed."""
        return self.section if self.section in (ABOUT, PRODUCTS) else OTHER


def identity(url: str) -> str:
    """The URL with its fragment, trailing slash and case differences removed, for deduping."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, parts.query, ""))


def _segments(url: str) -> list[str]:
    return [s for s in path_of(url).split("/") if s]


def _arabic_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "؀" <= c <= "ۿ") / len(letters)


def url_language(url: str) -> Optional[str]:
    """`ar` or `en` when the URL says so (/ar/, ar.example.com, ?lang=ar, an Arabic slug)."""
    parts = urlsplit(url)
    host_label = (parts.hostname or "").split(".")[0]
    segments = _segments(url)
    for token in ([host_label] + segments[:1]):
        match = _LANG_SEGMENT.match(token or "")
        if match:
            return match.group(1).lower()
    for key, value in parse_qsl(parts.query):
        if key.lower() in _LANG_QUERY and language_hint(value):
            return language_hint(value)
    if _arabic_share(path_of(url)) > 0.5:
        return "ar"
    return None


def _is_junk(url: str) -> bool:
    path = path_of(url)
    segments = [s.lower() for s in _segments(url)]
    if _FILE.search(path) or _DATED_POST.search(path):
        return True
    if any(s in _BLOCKED_SEGMENTS for s in segments):
        return True
    content = [s for i, s in enumerate(segments) if not (i == 0 and _LANG_SEGMENT.match(s))]
    if len(content) >= 2 and content[0] in _DETAIL_PARENTS:
        return True
    query = parse_qsl(urlsplit(url).query)
    return any(k.lower() not in _LANG_QUERY for k, _ in query)


def score_url(url: str, text: str = "", in_nav: bool = False) -> tuple[int, str]:
    """(score, section) for a page, from the words in its path and in its link text. 0 means not worth reading."""
    if _is_junk(url):
        return 0, OTHER
    path_key = search_key(re.sub(r"[-_/.+]+", " ", path_of(url)))
    text_key = search_key(text)
    best, section = 0, OTHER
    for name, (weight, patterns) in _PATTERNS.items():
        hits = 0
        if any(p.search(path_key) for p in patterns):
            hits += 1
        if text_key and any(p.search(text_key) for p in patterns):
            hits += 1
        if hits * weight > best:
            best, section = hits * weight, name
    if best == 0:
        return 0, OTHER
    segments = _segments(url)
    depth = len([s for i, s in enumerate(segments) if not (i == 0 and _LANG_SEGMENT.match(s))])
    score = best + (_NAV_BONUS if in_nav else 0) - max(0, depth - 1)
    return max(score, 0), section


def candidates_from_links(
    links: Iterable[Link], *, home_url: str, home_lang: Optional[str] = None
) -> list[Candidate]:
    """Scored pages among the same-site `links`, leaving out the homepage and anything not worth reading."""
    home = identity(home_url)
    out: list[Candidate] = []
    for link in links:
        if not same_site(link.url, home_url) or identity(link.url) == home:
            continue
        score, section = score_url(link.url, link.text, link.in_nav)
        if score <= 0:
            continue
        lang = url_language(link.url) or language_hint(link.lang or "")
        if lang is None and _arabic_share(link.text) > 0.5:
            lang = "ar"
        if lang is None and link.text:
            lang = "en" if _arabic_share(link.text) == 0 else None
        out.append(Candidate(link.url, link.text, link.in_nav, lang or home_lang, score, section, identity(link.url)))
    return out


def candidates_from_urls(urls: Iterable[str], *, home_url: str) -> list[Candidate]:
    """Scored pages among the same-site sitemap `urls`. They have no link text, so only the path counts."""
    home = identity(home_url)
    out: list[Candidate] = []
    for url in list(urls)[:_MAX_SITEMAP_URLS]:
        if not same_site(url, home_url) or identity(url) == home:
            continue
        score, section = score_url(url)
        if score > 0:
            out.append(Candidate(url, "", False, url_language(url), score, section, identity(url)))
    return out


def merge(candidates: Iterable[Candidate]) -> list[Candidate]:
    """One candidate per page, keeping the best score and the first language and text found."""
    best: dict[str, Candidate] = {}
    for c in candidates:
        known = best.get(c.key)
        if known is None:
            best[c.key] = c
            continue
        if c.score > known.score:
            c.lang = c.lang or known.lang
            c.text = c.text or known.text
            best[c.key] = c
        else:
            known.lang = known.lang or c.lang
            known.text = known.text or c.text
    return list(best.values())


def select_pages(candidates: Iterable[Candidate], limit: int) -> list[Candidate]:
    """The best `limit` pages, at most two per section.

    On a site with both English and Arabic pages, the English ones are chosen section by section and
    the best Arabic about page is added. On a site in one language everything competes together.
    """
    ranked = sorted(merge(candidates), key=lambda c: (-c.score, c.key))
    if limit <= 0:
        return []
    arabic = [c for c in ranked if c.lang == "ar"]
    other = [c for c in ranked if c.lang != "ar"]
    arabic_about = next((c for c in arabic if c.section == ABOUT), None) if arabic and other else None
    pool = other if arabic and other else ranked
    room = limit - (1 if arabic_about else 0)

    chosen: list[Candidate] = []
    per_section: dict[str, int] = {}
    for c in pool:
        if len(chosen) >= room:
            break
        if per_section.get(c.section, 0) >= _PER_SECTION:
            continue
        per_section[c.section] = per_section.get(c.section, 0) + 1
        chosen.append(c)
    if arabic_about and arabic_about not in chosen:
        chosen.append(arabic_about)
    return chosen


def page_language(html_lang: str, text: str) -> Optional[str]:
    """`ar` or `en` for a page, from its html lang attribute, else from its text."""
    hint = language_hint(html_lang)
    if hint:
        return hint
    detected = detect_language(text) if text else None
    return {Language.AR: "ar", Language.EN: "en"}.get(detected) if detected else None


# ------------------------------------------------------------------ sitemaps


def parse_sitemap(xml: bytes) -> tuple[str, list[str]]:
    """("index" or "urlset", the <loc> URLs) of a sitemap. Unreadable XML gives ("urlset", [])."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=True)
    try:
        root = etree.fromstring(xml, parser=parser)
    except etree.XMLSyntaxError:
        return "urlset", []
    if root is None:
        return "urlset", []
    kind = "index" if etree.QName(root).localname.lower() == "sitemapindex" else "urlset"
    locs = [
        (node.text or "").strip()
        for node in root.iter()
        if isinstance(node.tag, str) and etree.QName(node).localname == "loc"
    ]
    return kind, [u for u in locs if u.startswith(("http://", "https://"))][:_MAX_SITEMAP_URLS]
