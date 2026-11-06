"""An in-memory company website, and stand-ins for the clock and the headless browser, for the crawl tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from edrak.company_profile.url_import.fetch import SiteFetcher
from edrak.company_profile.url_import.render import RendererUnavailable
from edrak.company_research.crawler import CrawlLimits, crawl_company_site
from edrak.company_research.crawler.browser import RenderedPage

FIXTURES = Path(__file__).parent / "fixtures" / "company_crawl"
ROBOTS = "User-agent: *\nDisallow: /private/\nSitemap: https://acme.example/sitemap.xml\n"
PRIVATE_HOST = "intranet.acme.example"  # resolves to a private address


def fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def acme_pages() -> dict:
    return {
        "/": fx("home.html"),
        "/about": fx("about.html"),
        "/ar/about": fx("ar_about.html"),
        "/ar/team": fx("ar_team.html"),
        "/team": fx("team.html"),
        "/team/leadership": fx("leadership.html"),
        "/team/leadership/board": fx("board.html"),
        "/team/leadership/board/archive/2019": fx("archive.html"),
        "/investors": fx("investors.html"),
        "/investors/reports": fx("reports.html"),
        "/media": fx("media.html"),
        "/careers": fx("careers.html"),
        "/locations": fx("locations.html"),
        "/history": fx("history.html"),
        "/products": fx("products.html"),
        "/sustainability": fx("sustainability.html"),
        "/resources": fx("resources_spa.html"),
        "/members": fx("login.html"),
        "/sitemap.xml": fx("sitemap.xml"),
        "/moved": (302, "", f"https://{PRIVATE_HOST}/secret"),
        "/old-about": (302, "", "https://other.example/about"),
        "/private/plan": fx("moved_target.html"),
        "news.acme.example/": fx("news_home.html"),
    }


class Site:
    """Requests seen by the fake website, as (host, path, query) in the order they were made."""

    def __init__(self, pages: dict, robots: str = ROBOTS, on_request=None) -> None:
        self.pages = pages
        self.robots = robots
        self.requests: list[tuple[str, str]] = []
        self.on_request = on_request

    @property
    def paths(self) -> list[str]:
        return [path for _, path in self.requests]

    def fetched(self, host: str, path: str) -> bool:
        return (host, path) in self.requests

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        target = request.url.raw_path.decode()
        self.requests.append((host, target))
        if self.on_request:
            self.on_request(request)
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots, headers={"content-type": "text/plain"})
        entry = self.pages.get(f"{host}{target}") or self.pages.get(f"{host}{path}")
        if entry is None and host in ("acme.example", "www.acme.example"):
            entry = self.pages.get(target) or self.pages.get(path)
        if entry is None:
            return httpx.Response(404, text="not found")
        if isinstance(entry, tuple):
            status, body, value = entry
            headers = {"location": value} if status in (301, 302, 303, 307, 308) else {"content-type": value}
            return httpx.Response(status, content=body.encode() if isinstance(body, str) else body, headers=headers)
        ctype = "application/xml" if path.endswith(".xml") else "text/html; charset=utf-8"
        return httpx.Response(200, content=entry.encode() if isinstance(entry, str) else entry, headers={"content-type": ctype})

    def fetcher(self, **kwargs) -> SiteFetcher:
        async def resolve(host: str, port: int) -> list[str]:
            return ["10.0.0.5"] if host == PRIVATE_HOST else ["93.184.216.34"]

        return SiteFetcher(transport=httpx.MockTransport(self.handler), resolver=resolve, **kwargs)


def limits(**overrides) -> CrawlLimits:
    """Crawl limits for tests: no delay between requests and no browser unless a test turns it on."""
    values = dict(
        max_pages=40, max_depth=3, time_budget_s=180.0, concurrency=3, host_delay_s=0.0, max_bytes=2_000_000,
        dynamic=False, dynamic_min_chars=300, max_dynamic_pages=15,
    )
    values.update(overrides)
    return CrawlLimits(**values)


def crawl(site: Site, url: str = "https://acme.example/", fetcher_args: dict | None = None, **kwargs):
    kwargs.setdefault("limits", limits())
    return asyncio.run(crawl_company_site(url, fetcher=site.fetcher(**(fetcher_args or {})), **kwargs))


class Clock:
    """A clock the tests move by hand."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeBrowser:
    """A browser factory. `pages` maps a URL to the HTML the browser would end up with."""

    def __init__(self, pages: dict[str, str] | None = None, unavailable: bool = False, delay: float = 0.0, **counts) -> None:
        self.pages = pages or {}
        self.unavailable = unavailable
        self.delay = delay
        self.counts = counts  # scrolls, clicks, expands reported for each page
        self.opened = 0
        self.rendered: list[str] = []

    def __call__(self):
        browser = self

        @asynccontextmanager
        async def session():
            if browser.unavailable:
                raise RendererUnavailable("no browser here")
            browser.opened += 1
            yield browser

        return session()

    async def render(self, url: str, in_scope) -> RenderedPage:
        self.rendered.append(url)
        if self.delay:
            await asyncio.sleep(self.delay)
        return RenderedPage(html=self.pages[url], url=url, **self.counts)
