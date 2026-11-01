"""Company website in, ProfileDraft out. Nothing is saved.

Reads the homepage and a few high-value pages of the same site (about, products, services and the
like), turns their main text and structured metadata into SourceDocuments, and hands them to the
extraction core for the one and only model call.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

from edrak.agents.competitor_intelligence.socials import platform_of
from edrak.company_profile.draft import build_profile_draft
from edrak.company_profile.extractor import LLMCall
from edrak.company_profile.models import ProfileDraft, SourceDocument, SourceType
from edrak.company_profile.url_import.discover import (
    ABOUT,
    PRODUCTS,
    Candidate,
    candidates_from_links,
    candidates_from_urls,
    page_language,
    parse_sitemap,
    select_pages,
    url_language,
)
from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked
from edrak.company_profile.url_import.extract import extract_main_text
from edrak.company_profile.url_import.fetch import SITEMAP_TYPES, Fetched, SiteFetcher
from edrak.company_profile.url_import.parse import PageInfo, language_hint, parse_page, social_profiles
from edrak.company_profile.url_import.render import Renderer, RendererUnavailable, render_pages
from edrak.company_profile.url_import.urls import normalize_url, same_site
from edrak.core.config import settings

logger = logging.getLogger(__name__)

# Lower packs first, so it is cut last: metadata, the homepage, about, products and services, the rest.
PRIORITY_METADATA, PRIORITY_HOME, PRIORITY_ABOUT, PRIORITY_PRODUCTS, PRIORITY_OTHER = 0, 1, 2, 3, 4
_PRIORITY = {ABOUT: PRIORITY_ABOUT, PRODUCTS: PRIORITY_PRODUCTS}
_SITEMAPS_READ = 2
_SITEMAP_CHILDREN_READ = 3

NEEDS_JS_WARNING = (
    "This site seems to need JavaScript to show its content, and a browser to render it is not "
    "available here, so little text could be read."
)


@dataclass
class Collected:
    documents: list[SourceDocument] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Page:
    url: str
    html: str
    info: PageInfo
    text: str


def metadata_text(info: PageInfo) -> str:
    """The page's meta tags and JSON-LD organizations as plain lines the model can cite."""
    lines: list[str] = []

    def add(label: str, value: str) -> None:
        if value:
            lines.append(f"{label}: {value}")

    add("Page title", info.title)
    add("Meta description", info.description)
    add("Site name (og:site_name)", info.site_name)
    add("Description (og:description)", info.og_description)
    add("Page language (html lang)", info.lang)
    for org in info.orgs:
        lines.append("Organization (JSON-LD):")
        add("  name", org.name)
        add("  alternate names", "; ".join(org.alternate_names))
        add("  description", org.description)
        add("  address", org.address)
        add("  area served", "; ".join(org.area_served))
        add("  same as", "; ".join(org.same_as))
    return "\n".join(lines)


async def _read_page(html: str, url: str) -> _Page:
    info = parse_page(html, url)
    text = await asyncio.to_thread(extract_main_text, html, url)
    return _Page(url=url, html=html, info=info, text=text)


async def _sitemap_urls(site: SiteFetcher, origin: str, home_url: str) -> list[str]:
    """Page URLs from the robots.txt sitemaps and /sitemap.xml, reading a sitemap index one level deep."""
    roots = list(dict.fromkeys(await site.sitemaps(origin) + [f"{origin}/sitemap.xml"]))
    roots = [r for r in roots if same_site(r, home_url)][:_SITEMAPS_READ]
    urls: list[str] = []

    async def read(url: str) -> Optional[tuple[str, list[str]]]:
        try:
            if not await site.allowed(url):
                return None
            fetched = await site.get(url, kinds=SITEMAP_TYPES)
        except (SiteUnreachable, UrlBlocked):
            return None
        return parse_sitemap(fetched.body)

    for root in roots:
        parsed = await read(root)
        if parsed is None:
            continue
        kind, locs = parsed
        if kind != "index":
            urls.extend(locs)
            continue
        for child in [u for u in locs if same_site(u, home_url)][:_SITEMAP_CHILDREN_READ]:
            child_parsed = await read(child)
            if child_parsed and child_parsed[0] == "urlset":  # a nested index is not followed
                urls.extend(child_parsed[1])
    return urls


def _other_language_home(info: PageInfo, home_url: str, home_lang: Optional[str]) -> Optional[str]:
    """The same site's homepage in the other of English and Arabic, when the page points at one."""
    wanted = "en" if home_lang == "ar" else "ar"
    for code, url in info.alternates.items():
        if language_hint(code) == wanted and same_site(url, home_url):
            return url
    for link in info.links:
        parts = urlsplit(link.url)
        if same_site(link.url, home_url) and url_language(link.url) == wanted and parts.path.strip("/") in ("", wanted) and not parts.query:
            return link.url
    return None


async def _fetch_page(site: SiteFetcher, candidate: Candidate, home_url: str) -> tuple[Optional[Fetched], str]:
    """(page, reason). The reason is "robots" or "failed" when there is no page."""
    try:
        if not await site.allowed(candidate.url):
            return None, "robots"
        fetched = await site.get(candidate.url)
    except (SiteUnreachable, UrlBlocked):
        return None, "failed"
    if not same_site(fetched.url, home_url):  # a redirect off the company's site
        return None, "failed"
    return fetched, ""


async def _render(renderer: Renderer, urls: list[str]) -> Optional[dict[str, str]]:
    """Rendered HTML by URL, or None when no browser is available."""
    try:
        return await renderer(urls)
    except RendererUnavailable as exc:
        logger.info("Rendering skipped: %s", exc)
        return None
    except Exception as exc:  # a browser that crashes should not fail the import
        logger.warning("Rendering failed: %s", exc)
        return None


async def collect_site(site: SiteFetcher, start_url: str, renderer: Renderer) -> Collected:
    """Read the site at `start_url` into SourceDocuments, links and warnings. Raises when the first page cannot be read."""
    out = Collected()
    parts = urlsplit(start_url)
    origin = f"{parts.scheme}://{parts.netloc}"

    await site.check(start_url)
    if not await site.allowed(start_url):
        raise SiteUnreachable("The website's robots.txt does not allow reading its pages.")
    first = await site.get(start_url)
    home_url = first.url
    if not await site.allowed(home_url):
        raise SiteUnreachable("The website's robots.txt does not allow reading its pages.")
    home = await _read_page(first.text(), home_url)

    # A site that shows almost nothing without scripts is read again in a browser, if there is one.
    thin = len(home.text) < settings.PROFILE_IMPORT_RENDER_MIN_CHARS
    browser_ok = False
    if thin:
        rendered = await _render(renderer, [home_url])
        if rendered is None:
            out.warnings.append(NEEDS_JS_WARNING)
        else:
            browser_ok = True
            if home_url in rendered:
                better = await _read_page(rendered[home_url], home_url)
                if len(better.text) > len(home.text):
                    home = better

    home_lang = page_language(home.info.lang, home.text)
    candidates = candidates_from_links(home.info.links, home_url=home_url, home_lang=home_lang)

    other_home = _other_language_home(home.info, home_url, home_lang)
    if other_home:
        try:
            if await site.allowed(other_home):
                alt = await site.get(other_home)
                alt_info = parse_page(alt.text(), alt.url)
                candidates += candidates_from_links(
                    alt_info.links, home_url=home_url, home_lang="en" if home_lang == "ar" else "ar"
                )
        except (SiteUnreachable, UrlBlocked):
            pass

    candidates += candidates_from_urls(await _sitemap_urls(site, origin, home_url), home_url=home_url)
    selected = select_pages(candidates, settings.PROFILE_IMPORT_MAX_PAGES - 1)

    fetched = await asyncio.gather(*(_fetch_page(site, c, home_url) for c in selected))
    robots_skipped = sum(1 for _, why in fetched if why == "robots")
    failed = sum(1 for _, why in fetched if why == "failed")
    pages: list[tuple[Candidate, _Page]] = []
    for candidate, (response, _) in zip(selected, fetched):
        if response is not None:
            pages.append((candidate, await _read_page(response.text(), response.url)))

    if thin and browser_ok and pages:
        rendered = await _render(renderer, [p.url for _, p in pages]) or {}
        for index, (candidate, page) in enumerate(pages):
            html = rendered.get(page.url)
            if html:
                better = await _read_page(html, page.url)
                if len(better.text) > len(page.text):
                    pages[index] = (candidate, better)

    # Documents, most useful first.
    if home.info.has_metadata:
        out.documents.append(
            SourceDocument(
                source_type=SourceType.PAGE_METADATA,
                ref=home_url,
                title="Homepage metadata",
                text=metadata_text(home.info),
                priority=PRIORITY_METADATA,
            )
        )
    blank = 0
    if home.text:
        out.documents.append(
            SourceDocument(source_type=SourceType.WEB_PAGE, ref=home_url, title=home.info.title, text=home.text, priority=PRIORITY_HOME)
        )
    else:
        blank += 1
    for candidate, page in pages:
        if page.text:
            out.documents.append(
                SourceDocument(
                    source_type=SourceType.WEB_PAGE,
                    ref=page.url,
                    title=page.info.title or candidate.text,
                    text=page.text,
                    priority=_PRIORITY.get(candidate.label, PRIORITY_OTHER),
                )
            )
        else:
            blank += 1

    if robots_skipped:
        out.warnings.append(f"{robots_skipped} page(s) were skipped because the website's robots.txt does not allow them.")
    if failed:
        out.warnings.append(f"{failed} page(s) could not be read.")
    if blank:
        out.warnings.append(f"{blank} page(s) had no readable text.")

    # Links: what the company says about itself first (JSON-LD sameAs), then the pages' own links.
    page_infos = [home.info] + [p.info for _, p in pages]
    urls = [u for org in home.info.orgs for u in org.same_as]
    urls += [link.url for info in page_infos for link in info.links]
    root = f"{urlsplit(home_url).scheme}://{urlsplit(home_url).netloc}"
    out.links = [{"platform": "website", "url": root}] + [
        {"platform": platform_of(u) or "", "url": u} for u in social_profiles(urls)
    ]
    return out


async def import_profile_from_url(
    url: str,
    *,
    fetcher: Optional[SiteFetcher] = None,
    renderer: Optional[Renderer] = None,
    llm: Optional[LLMCall] = None,
) -> ProfileDraft:
    """A draft company profile read from the website at `url`.

    Raises UrlBlocked for an address that is invalid or not public, SiteUnreachable when the site
    cannot be read, and the core's ProfileLLMError or ProfileExtractionError when the model step fails.
    """
    start = normalize_url(url)
    async with (fetcher or SiteFetcher()) as site:
        collected = await collect_site(site, start, renderer or render_pages)
    draft = await asyncio.to_thread(build_profile_draft, collected.documents, collected.links, llm=llm)
    draft.warnings = collected.warnings + draft.warnings
    return draft
