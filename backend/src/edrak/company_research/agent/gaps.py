"""Which categories have no facts yet, and which unread pages of the company's site might fill them."""

from __future__ import annotations

from typing import Iterable, Sequence
from urllib.parse import unquote, urlsplit

from edrak.agents.competitor_intelligence.socials import platform_of
from edrak.agents.customer_trends.utils.text import phrase_pattern, search_key
from edrak.company_profile.url_import.parse import Link
from edrak.company_research.categories import CATEGORIES, OTHER
from edrak.company_research.crawler import CrawledPage
from edrak.company_research.crawler.rules import in_scope, page_key, skip_reason

# Words in a link's address or text that suggest the page holds a category, English and Arabic.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "leadership": ("leadership", "management", "executive", "executives", "board", "directors", "our team", "team", "ceo", "chairman", "القيادة", "الإدارة", "مجلس الإدارة", "فريق الإدارة", "الإدارة التنفيذية"),
    "ownership_and_funding": ("investors", "investor relations", "shareholders", "ownership", "funding", "investment", "المستثمرين", "علاقات المستثمرين", "المساهمين", "الملكية"),
    "financials": ("financials", "financial", "annual report", "reports", "results", "investors", "investor relations", "revenue", "التقارير", "التقرير السنوي", "النتائج المالية", "البيانات المالية"),
    "customers": ("customers", "clients", "case studies", "success stories", "العملاء", "قصص النجاح"),
    "partners": ("partners", "partnerships", "alliances", "الشركاء", "شركاؤنا"),
    "locations": ("locations", "branches", "offices", "contact", "find us", "الفروع", "فروعنا", "المواقع", "اتصل بنا"),
    "team": ("team", "our people", "people", "فريق العمل", "فريقنا"),
    "pricing": ("pricing", "prices", "plans", "packages", "الأسعار", "الباقات"),
    "technology": ("technology", "platform", "solutions", "technical", "التقنية", "التكنولوجيا", "المنصة"),
    "certifications": ("certifications", "certificates", "accreditation", "iso", "الشهادات", "الاعتمادات"),
    "regulatory": ("regulatory", "compliance", "licenses", "licences", "legal", "التراخيص", "الامتثال"),
    "news_and_events": ("news", "press", "media", "events", "newsroom", "الأخبار", "المركز الإعلامي", "الفعاليات"),
    "awards": ("awards", "recognition", "achievements", "الجوائز", "الإنجازات"),
    "history": ("history", "our story", "about us", "milestones", "who we are", "تاريخنا", "قصتنا", "من نحن", "نبذة عنا"),
}
_PATTERNS = {category: [phrase_pattern(w) for w in words] for category, words in CATEGORY_KEYWORDS.items()}

ANCHOR_POINTS = 2  # a keyword in the link text says more than one in the address
PATH_POINTS = 1


def empty_categories(have: Iterable[str]) -> list[str]:
    """The categories with no fact among `have` (a list of category keys), in the list's order. "other" is never a gap."""
    present = set(have)
    return [c.key for c in CATEGORIES if c.key != OTHER and c.key not in present]


def _path_words(url: str) -> str:
    return search_key(unquote(urlsplit(url).path).replace("-", " ").replace("_", " ").replace("/", " "))


def relevance(category: str, link: Link) -> int:
    """How strongly a link's text and address point to `category`; 0 when they do not."""
    anchor, path = search_key(link.text), _path_words(link.url)
    points = 0
    for pattern in _PATTERNS.get(category, ()):
        if pattern.search(anchor):
            points += ANCHOR_POINTS
        if pattern.search(path):
            points += PATH_POINTS
    return points


def plan_targets(
    categories: Sequence[str],
    pages: Sequence[CrawledPage],
    home: str,
    visited: Iterable[str],
    already_chosen: Iterable[str],
    limit: int,
) -> list[str]:
    """Up to `limit` unvisited pages of the company's own site, the best match for each empty category in turn.

    A page qualifies when it is on the company's domain, is not one the crawl skips by rule (login,
    search, files), is not a social profile, and has not been read or chosen before. Pages are taken
    round-robin across the categories, so one category does not use up every slot.
    """
    seen = set(visited) | {page_key(u) for u in already_chosen}
    links: dict[str, Link] = {}
    for page in pages:
        for link in page.links:
            key = page_key(link.url)
            if key in seen or key in links or not in_scope(link.url, home):
                continue
            if skip_reason(link.url) is not None or platform_of(link.url):
                continue
            links[key] = link
    ranked: dict[str, list[tuple[int, str]]] = {}
    for category in categories:
        scored = [(relevance(category, link), key) for key, link in links.items()]
        ranked[category] = sorted((s for s in scored if s[0] > 0), key=lambda s: -s[0])
    chosen: list[str] = []
    taken: set[str] = set()
    while len(chosen) < limit and any(ranked.values()):
        for category in categories:
            queue = ranked.get(category, [])
            while queue and queue[0][1] in taken:
                queue.pop(0)
            if queue and len(chosen) < limit:
                _, key = queue.pop(0)
                taken.add(key)
                chosen.append(links[key].url)
    return chosen

