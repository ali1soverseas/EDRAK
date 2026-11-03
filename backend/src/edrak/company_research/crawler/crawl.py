"""Read a company's own website much more deeply than the profile import does.

The crawl starts at the homepage and reads the sitemaps, then follows links, best-scoring page first
and nearer pages before farther ones. It stays on the registered domain of the starting page and its
subdomains, checks every redirect hop for scope, robots.txt and non-public addresses before the
request is made, and waits between requests to the same host. A page whose static text is thin, or
whose HTML shows it builds content with scripts, is read again in a headless browser when one is
installed. No model is called, and PDFs are only listed, not downloaded.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Optional
from urllib.parse import urlsplit

from edrak.company_profile.url_import.discover import ABOUT, page_language, parse_sitemap, url_language
from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked
from edrak.company_profile.url_import.extract import extract_main_text
from edrak.company_profile.url_import.fetch import SITEMAP_TYPES, SiteFetcher
from edrak.company_profile.url_import.parse import Link, PageInfo, language_hint, parse_page
from edrak.company_profile.url_import.render import RendererUnavailable
from edrak.company_profile.url_import.urls import normalize_url
from edrak.company_research.crawler.browser import BrowserFactory, PageRenderer, PlaywrightBrowser, RenderedPage
from edrak.company_research.crawler.models import (
    CrawledPage,
    CrawlResult,
    FetchMode,
    PdfLink,
    StopReason,
)
from edrak.company_research.crawler.rules import (
    in_scope,
    is_login_page,
    is_pdf_link,
    page_key,
    render_reason,
    score,
    skip_reason,
    twin_key,
)
from edrak.core.config import settings

logger = logging.getLogger(__name__)

BROWSER_UNAVAILABLE_WARNING = (
    "Some pages need a browser to show all of their content, and none is available here, so only "
    "their static text was read."
)
_SITEMAP_ROOTS = 4
_SITEMAP_CHILDREN = 8
_ATTEMPTS_PER_PAGE = 3  # fetches tried per page wanted, so a site full of dead links ends


class _RobotsDisallowed(SiteUnreachable):
    pass


class _OffSite(SiteUnreachable):
    pass


@dataclass
class CrawlLimits:
    """The limits of one crawl. `from_settings` reads them from config."""

    max_pages: int
    max_depth: int
    time_budget_s: float
    concurrency: int
    host_delay_s: float
    max_bytes: int
    dynamic: bool
    dynamic_min_chars: int
    max_dynamic_pages: int

    @classmethod
    def from_settings(cls) -> "CrawlLimits":
        return cls(
            max_pages=settings.COMPANY_RESEARCH_CRAWL_MAX_PAGES,
            max_depth=settings.COMPANY_RESEARCH_CRAWL_MAX_DEPTH,
            time_budget_s=settings.COMPANY_RESEARCH_CRAWL_TIME_BUDGET_S,
            concurrency=max(1, settings.COMPANY_RESEARCH_CRAWL_CONCURRENCY),
            host_delay_s=settings.COMPANY_RESEARCH_CRAWL_HOST_DELAY_S,
            max_bytes=settings.COMPANY_RESEARCH_CRAWL_MAX_BYTES,
            dynamic=settings.COMPANY_RESEARCH_CRAWL_DYNAMIC,
            dynamic_min_chars=settings.COMPANY_RESEARCH_CRAWL_DYNAMIC_MIN_CHARS,
            max_dynamic_pages=settings.COMPANY_RESEARCH_CRAWL_MAX_DYNAMIC_PAGES,
        )


class HostThrottle:
    """Spaces requests to one host at least `delay` seconds apart, however many tasks ask at once."""

    def __init__(self, delay: float, clock: Callable[[], float], sleep: Callable[[float], Awaitable[None]]) -> None:
        self._delay = delay
        self._clock = clock
        self._sleep = sleep
        self._next: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        if self._delay <= 0:
            return
        now = self._clock()
        at = max(now, self._next.get(host, 0.0))
        self._next[host] = at + self._delay  # reserved before waiting, so a second task queues behind this one
        if at > now:
            await self._sleep(at - now)


@dataclass
class _Item:
    url: str
    key: str
    depth: int
    score: int
    section: str
    text: str
    seq: int
    lang: Optional[str] = None
    twin: str = ""
    superseded: str = ""  # why the page is no longer wanted: "language" or "variant"
    started: bool = False


def _merge_links(first: list[Link], second: list[Link]) -> list[Link]:
    seen = {link.url for link in first}
    return first + [link for link in second if link.url not in seen]


class _Crawl:
    def __init__(
        self,
        site: SiteFetcher,
        start_url: str,
        limits: CrawlLimits,
        browser: Optional[BrowserFactory],
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self.site = site
        self.start_url = start_url
        self.home_url = start_url  # replaced by the address the homepage ended up at
        self.limits = limits
        self.clock = clock
        self.throttle = HostThrottle(limits.host_delay_s, clock, sleep)
        self.result = CrawlResult(start_url=start_url)
        self.stats = self.result.stats
        self.begun = clock()
        self.deadline = self.begun + limits.time_budget_s
        self.frontier: list[tuple[int, int, int, _Item]] = []
        self.seen: set[str] = set()
        self.variants: dict[str, _Item] = {}  # pages queued with a query string, which a plain address replaces
        self.too_deep: set[str] = set()
        self.latin_twins: set[str] = set()  # pages known in English (or with no language marker)
        self.arabic_pending: dict[str, _Item] = {}  # Arabic pages queued before their English copy was seen
        self.arabic_about = False
        self.seq = 0
        self.pdf_seen: set[str] = set()
        self.truncated_hosts = 0
        self._browser_factory = browser if browser is not None else PlaywrightBrowser
        self._renderer: Optional[PageRenderer] = None
        self._browser_unavailable = False
        self._browser_lock = asyncio.Lock()
        self._browser_stack = AsyncExitStack()
        self._dynamic_tried = 0

    # ------------------------------------------------------------------ guards

    def in_scope(self, url: str) -> bool:
        return in_scope(url, self.home_url)

    async def _before_request(self, url: str, *, scoped: bool = True) -> None:
        """Runs before every request, and so before every redirect hop."""
        if scoped and not self.in_scope(url):
            raise _OffSite("The page redirected away from the company's website.")
        if not await self.site.allowed(url):
            raise _RobotsDisallowed("robots.txt does not allow this page.")
        await self.throttle.wait((urlsplit(url).hostname or "").lower())

    async def _guard(self, url: str) -> None:
        await self._before_request(url)

    def _remaining(self) -> float:
        return self.deadline - self.clock()

    # ------------------------------------------------------------------ queue

    def _push(self, item: _Item) -> None:
        heappush(self.frontier, (-item.score, item.depth, item.seq, item))
        self.stats.urls_discovered += 1

    def _note_pdf(self, url: str, text: str, found_on: str) -> Optional[PdfLink]:
        if url in self.pdf_seen:
            return None
        self.pdf_seen.add(url)
        link = PdfLink(url=url, text=text, found_on=found_on, same_site=self.in_scope(url))
        self.result.pdf_links.append(link)
        return link

    def _enqueue(self, url: str, depth: int, *, text: str = "", in_nav: bool = False, hreflang: str = "", from_sitemap: bool = False) -> None:
        if not self.in_scope(url):
            return
        skipped = skip_reason(url)
        if skipped is not None:
            if skipped != "pdf":  # a PDF is listed from the page that links to it
                self.stats.skipped_filtered += 1
            return
        key = page_key(url)
        plain = not urlsplit(url).query
        if key in self.seen:
            # The address without a query string is the page itself; one with it is a variant (sorted, paged, filtered).
            variant = self.variants.get(key)
            if not (plain and variant is not None and not variant.started):
                return
            variant.superseded = "variant"
            del self.variants[key]
        if depth > self.limits.max_depth:
            self.too_deep.add(key)
            return
        points, section = score(url, text, in_nav)
        if from_sitemap and points == 0:  # a sitemap lists everything; only pages with a section word are worth a slot
            return
        lang = url_language(url) or language_hint(hreflang)
        twin = twin_key(url)
        item = _Item(url, key, depth, points, section, text, self.seq, lang=lang, twin=twin)
        self.seq += 1
        if lang == "ar":
            if twin in self.latin_twins:
                if section == ABOUT and not self.arabic_about:
                    self.arabic_about = True  # the one Arabic about page that is kept
                else:
                    self.stats.skipped_language += 1
                    return
            else:
                self.arabic_pending[twin] = item
        else:
            self.latin_twins.add(twin)
            waiting = self.arabic_pending.pop(twin, None)
            if waiting is not None and not waiting.started:
                if waiting.section == ABOUT and not self.arabic_about:
                    self.arabic_about = True
                else:
                    waiting.superseded = "language"
        self.seen.add(key)
        if not plain:
            self.variants[key] = item
        self._push(item)

    def _enqueue_links(self, links: list[Link], depth: int) -> None:
        any_nav = any(link.in_nav for link in links)  # a page with no nav element keeps its menu in plain divs
        for link in links:
            if is_pdf_link(link.url, link.text):
                continue
            self._enqueue(link.url, depth, text=link.text, in_nav=link.in_nav or not any_nav, hreflang=link.lang or "")

    # ------------------------------------------------------------------ reading a page

    async def _read(self, html: str, url: str) -> tuple[PageInfo, str]:
        info = parse_page(html, url)
        text = await asyncio.to_thread(extract_main_text, html, url)
        return info, text

    async def _open_browser(self) -> Optional[PageRenderer]:
        async with self._browser_lock:
            if self._renderer is None and not self._browser_unavailable:
                try:
                    self._renderer = await self._browser_stack.enter_async_context(self._browser_factory())
                except RendererUnavailable as exc:
                    logger.info("Browser not available: %s", exc)
                    self._browser_unavailable = True
                    self.result.warnings.append(BROWSER_UNAVAILABLE_WARNING)
                except Exception as exc:  # a browser that cannot start should not stop the crawl
                    logger.warning("Browser could not start: %s", exc)
                    self._browser_unavailable = True
                    self.result.warnings.append(BROWSER_UNAVAILABLE_WARNING)
            return self._renderer

    async def _render(self, url: str) -> Optional[RenderedPage]:
        renderer = await self._open_browser()
        if renderer is None:
            return None
        self._dynamic_tried += 1
        try:
            rendered = await asyncio.wait_for(renderer.render(url, self.in_scope), timeout=max(0.1, self._remaining()))
        except asyncio.TimeoutError:
            logger.info("Rendering %s ran past the time budget", url)
            return None
        except RendererUnavailable:
            return None
        except Exception as exc:
            logger.info("Could not render %s: %s", url, exc)
            return None
        self.stats.browser_pages += 1
        self.stats.scrolls += rendered.scrolls
        self.stats.clicks += rendered.clicks
        self.stats.expands += rendered.expands
        return rendered

    def _pdf_links(self, links: list[Link], found_on: str) -> list[PdfLink]:
        found = []
        for link in links:
            if is_pdf_link(link.url, link.text):
                known = self._note_pdf(link.url, link.text, found_on)
                found.append(known or PdfLink(link.url, link.text, found_on, self.in_scope(link.url)))
        return found

    async def _page(self, url: str, html: str, depth: int, section: str) -> CrawledPage:
        """The page at `url` from its static HTML, read again in the browser when that is warranted."""
        info, text = await self._read(html, url)
        links = info.links
        mode = FetchMode.STATIC
        reason = ""
        if self.limits.dynamic and not self._browser_unavailable and self._dynamic_tried < self.limits.max_dynamic_pages:
            reason = render_reason(text, html, self.limits.dynamic_min_chars)
        if reason:
            rendered = await self._render(url)
            if rendered is not None:
                dynamic_info, dynamic_text = await self._read(rendered.html, url)
                merged = _merge_links(links, dynamic_info.links)
                if len(dynamic_text) > len(text) or len(merged) > len(links):
                    mode = FetchMode.DYNAMIC
                    info = dynamic_info if len(dynamic_text) > len(text) else info
                    text = dynamic_text if len(dynamic_text) > len(text) else text
                    links = merged
        language = page_language(info.lang, text) or url_language(url)
        return CrawledPage(
            url=url,
            title=info.title,
            language=language,
            text=text,
            fetch_mode=mode,
            links=links,
            pdf_links=self._pdf_links(links, url),
            depth=depth,
            section=section,
            render_reason=reason if mode is FetchMode.DYNAMIC else "",
        )

    async def _visit(self, item: _Item) -> Optional[CrawledPage]:
        try:
            fetched = await self.site.get(item.url, before_request=self._guard)
        except _RobotsDisallowed:
            self.stats.skipped_robots += 1
            return None
        except _OffSite:
            self.stats.skipped_off_site += 1
            return None
        except (SiteUnreachable, UrlBlocked):
            self.stats.failed += 1
            return None
        if fetched.truncated:
            self.stats.truncated += 1
        final = page_key(fetched.url)
        if final != item.key:
            if final in self.seen:  # redirected to a page already queued or read
                return None
            self.seen.add(final)
        html = fetched.text()
        if is_login_page(html):
            self.stats.skipped_filtered += 1
            return None
        return await self._page(fetched.url, html, item.depth, item.section)

    def _accept(self, page: CrawledPage) -> None:
        self.result.pages.append(page)
        self.stats.pages_crawled += 1
        if page.fetch_mode is FetchMode.DYNAMIC:
            self.stats.dynamic_pages += 1
        else:
            self.stats.static_pages += 1
        self._enqueue_links(page.links, page.depth + 1)

    # ------------------------------------------------------------------ sitemaps

    async def _sitemap_urls(self) -> list[str]:
        parts = urlsplit(self.home_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        roots = list(dict.fromkeys(await self.site.sitemaps(origin) + [f"{origin}/sitemap.xml"]))
        roots = [r for r in roots if self.in_scope(r)][:_SITEMAP_ROOTS]

        async def read(url: str) -> Optional[tuple[str, list[str]]]:
            try:
                fetched = await self.site.get(url, kinds=SITEMAP_TYPES, before_request=self._guard)
            except (SiteUnreachable, UrlBlocked):
                return None
            return parse_sitemap(fetched.body)

        urls: list[str] = []
        for root in roots:
            parsed = await read(root)
            if parsed is None:
                continue
            kind, locs = parsed
            if kind != "index":
                urls.extend(locs)
                continue
            for child in [u for u in locs if self.in_scope(u)][:_SITEMAP_CHILDREN]:
                child_parsed = await read(child)
                if child_parsed and child_parsed[0] == "urlset":  # a nested index is not followed
                    urls.extend(child_parsed[1])
        return urls

    # ------------------------------------------------------------------ the crawl

    async def _first_page(self) -> CrawledPage:
        """The homepage. Raises when it cannot be read, since there is nothing to crawl then."""
        await self.site.check(self.start_url)

        async def guard(url: str) -> None:  # the homepage may move to another domain, so scope is set after it
            await self._before_request(url, scoped=False)

        try:
            fetched = await self.site.get(self.start_url, before_request=guard)
        except _RobotsDisallowed:
            if await self.site.robots_unreadable(self.start_url):
                raise SiteUnreachable("The website did not answer when its robots.txt was requested, so its pages were not read.") from None
            raise SiteUnreachable("The website's robots.txt does not allow reading its pages.") from None
        self.home_url = fetched.url
        if fetched.truncated:
            self.stats.truncated += 1
        self.seen.add(page_key(self.start_url))
        self.seen.add(page_key(fetched.url))
        self.latin_twins.add(twin_key(fetched.url))
        if not self.in_scope(self.start_url):
            self.result.warnings.append(f"The website moved to {urlsplit(fetched.url).hostname}; that address was crawled.")
        return await self._page(fetched.url, fetched.text(), 0, "home")

    async def run(self) -> CrawlResult:
        async with self._browser_stack:
            home = await self._first_page()
            self._accept(home)
            await self._seed_from_sitemaps()
            await self._loop()
        return self._finish()

    async def _seed_from_sitemaps(self) -> None:
        try:
            urls = await asyncio.wait_for(self._sitemap_urls(), timeout=max(0.0, self._remaining()))
        except asyncio.TimeoutError:
            self.stats.stopped = StopReason.TIME_BUDGET
            return
        for url in urls:
            if is_pdf_link(url):
                if self.in_scope(url):
                    self._note_pdf(url, "", "sitemap")
                continue
            self._enqueue(url, 1, from_sitemap=True)

    def _next_batch(self, room: int) -> list[_Item]:
        batch: list[_Item] = []
        while self.frontier and len(batch) < room:
            item = heappop(self.frontier)[3]
            if item.superseded:
                if item.superseded == "language":
                    self.stats.skipped_language += 1
                else:
                    self.stats.skipped_filtered += 1
                continue
            item.started = True
            batch.append(item)
        return batch

    async def _loop(self) -> None:
        attempts = 0
        max_attempts = self.limits.max_pages * _ATTEMPTS_PER_PAGE
        while self.frontier:
            if self.stats.pages_crawled >= self.limits.max_pages:
                self.stats.stopped = StopReason.MAX_PAGES
                return
            if self._remaining() <= 0:
                self.stats.stopped = StopReason.TIME_BUDGET
                return
            if attempts >= max_attempts:
                return
            room = min(self.limits.concurrency, self.limits.max_pages - self.stats.pages_crawled)
            batch = self._next_batch(room)
            attempts += len(batch)
            try:
                pages = await asyncio.wait_for(
                    asyncio.gather(*(self._visit(item) for item in batch), return_exceptions=True),
                    timeout=max(0.0, self._remaining()),
                )
            except asyncio.TimeoutError:
                self.stats.stopped = StopReason.TIME_BUDGET
                return
            for page in pages:
                if isinstance(page, BaseException):  # a page that breaks the reader should not end the crawl
                    logger.warning("A page could not be processed: %r", page)
                    self.stats.failed += 1
                elif page is not None:
                    self._accept(page)

    def _finish(self) -> CrawlResult:
        stats, warnings = self.stats, self.result.warnings
        stats.pdf_links = len(self.result.pdf_links)
        stats.skipped_depth = len(self.too_deep)
        stats.elapsed_s = round(self.clock() - self.begun, 3)
        if stats.stopped is StopReason.MAX_PAGES:
            left = sum(1 for entry in self.frontier if not entry[3].superseded)
            warnings.append(f"The crawl stopped at {stats.pages_crawled} pages; {left} more were queued and not read.")
        elif stats.stopped is StopReason.TIME_BUDGET:
            warnings.append(f"The crawl ran out of its {self.limits.time_budget_s:g} second time budget after {stats.pages_crawled} pages.")
        if stats.skipped_robots:
            warnings.append(f"{stats.skipped_robots} page(s) were skipped because the website's robots.txt does not allow them.")
        if stats.failed:
            warnings.append(f"{stats.failed} page(s) could not be read.")
        if stats.skipped_off_site:
            warnings.append(f"{stats.skipped_off_site} page(s) redirected away from the company's website and were not read.")
        if stats.truncated:
            warnings.append(f"{stats.truncated} page(s) were longer than the size limit and were cut.")
        return self.result


async def crawl_company_site(
    url: str,
    *,
    fetcher: Optional[SiteFetcher] = None,
    browser: Optional[BrowserFactory] = None,
    limits: Optional[CrawlLimits] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CrawlResult:
    """Crawl the website at `url` and return its pages, candidate PDF links, stats and warnings.

    Limits come from config unless `limits` is given; a `fetcher` passed in keeps its own size cap
    and concurrency. `browser` is a factory for an async context
    manager that yields a page renderer; it defaults to headless Chromium through Playwright, and
    when that is not installed the static text is kept and the result carries a warning.

    Raises UrlBlocked for an address that is invalid or not public, and SiteUnreachable when the
    homepage cannot be read or robots.txt does not allow it. Every other problem is counted in the
    stats and described in the warnings.
    """
    start = normalize_url(url)
    limits = limits or CrawlLimits.from_settings()
    async with (fetcher or SiteFetcher(max_bytes=limits.max_bytes, concurrency=limits.concurrency)) as site:
        return await _Crawl(site, start, limits, browser, clock, sleep).run()
