"""Opening a page in a headless browser so that content which loads late is there to read.

The steps are plain functions over a small page interface (the parts of a Playwright page they use),
so they run against a stand-in in tests. A page is scrolled until it stops growing, "load more"
buttons are clicked up to a cap, and tabs, accordions and details are opened. Nothing that submits a
form, logs in, buys, downloads or leaves the company's domain is ever clicked.

Playwright is optional. Without it, or without its Chromium build, RendererUnavailable is raised
when the browser is opened.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from edrak.agents.competitor_intelligence.socials import USER_AGENT
from edrak.agents.customer_trends.utils.text import phrase_pattern, search_key
from edrak.company_profile.url_import.render import RendererUnavailable, make_route_guard
from edrak.core.config import settings

logger = logging.getLogger(__name__)

InScope = Callable[[str], bool]

LOAD_MORE_WORDS = ("load more", "show more", "view all", "see more", "عرض المزيد", "المزيد", "عرض الكل")
# Text that marks a control as one that submits, logs in, buys or otherwise acts. Checked before
# anything is clicked, and it wins over a match on the load-more words.
NEVER_CLICK_WORDS = (
    "submit", "send", "log in", "login", "sign in", "sign up", "signup", "register", "subscribe", "buy",
    "purchase", "add to cart", "add to basket", "checkout", "pay", "order", "book", "apply", "delete",
    "remove", "download", "unsubscribe", "cart", "basket", "orders", "account", "password", "تسجيل الدخول", "تسجيل", "اشترك", "اشتراك", "شراء", "اشتري",
    "أضف إلى السلة", "السلة", "الدفع", "ادفع", "إرسال", "ارسل", "احجز", "حذف", "تحميل", "قدم",
)
_LOAD_MORE = [phrase_pattern(w) for w in LOAD_MORE_WORDS]
_NEVER = [phrase_pattern(w) for w in NEVER_CLICK_WORDS]
_NEVER_TYPES = frozenset({"submit", "reset", "image", "file", "password"})
_NO_NAVIGATION = ("#", "javascript:")

JS_HEIGHT = "() => document.documentElement.scrollHeight"
JS_SCROLL = "() => window.scrollTo(0, document.documentElement.scrollHeight)"
JS_TEXT_LENGTH = "() => (document.body ? document.body.innerText.length : 0)"
JS_OPEN_DETAILS = (
    "() => { const d = document.querySelectorAll('details:not([open])'); d.forEach(e => { e.open = true; }); return d.length; }"
)
# Lists the visible controls of one kind and tags each with data-edrak-click so it can be clicked by selector.
JS_CONTROLS = """(kind) => {
  const selector = kind === 'more'
    ? 'button, a, [role="button"], input[type="button"]'
    : '[role="tab"][aria-selected="false"], [aria-expanded="false"], summary';
  document.querySelectorAll('[data-edrak-click]').forEach(e => e.removeAttribute('data-edrak-click'));
  const out = [];
  for (const el of document.querySelectorAll(selector)) {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (box.width === 0 || box.height === 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    const id = out.length;
    el.setAttribute('data-edrak-click', String(id));
    out.push({
      id: id,
      tag: el.tagName.toLowerCase(),
      text: (el.innerText || el.value || '').trim().slice(0, 120),
      label: el.getAttribute('aria-label') || el.getAttribute('title') || '',
      href: el.getAttribute('href') || '',
      type: (el.getAttribute('type') || '').toLowerCase(),
      target: el.getAttribute('target') || '',
      download: el.hasAttribute('download'),
      in_form: !!el.closest('form'),
    });
  }
  return out;
}"""


class BrowserPage(Protocol):
    """The parts of a Playwright page the steps below use."""

    url: str

    async def evaluate(self, expression: str, arg: Any = None) -> Any: ...

    async def click(self, selector: str, timeout: float = ...) -> None: ...

    async def wait_for_timeout(self, timeout: float) -> None: ...


@dataclass
class Control:
    id: int
    tag: str
    text: str
    label: str = ""
    href: str = ""
    type: str = ""
    target: str = ""
    download: bool = False
    in_form: bool = False

    @property
    def name(self) -> str:
        return " ".join(f"{self.text} {self.label}".split())

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.tag, self.name, self.href)


@dataclass
class Interaction:
    scrolls: int = 0
    clicks: int = 0  # "load more" clicks
    expands: int = 0  # tabs, accordions and details opened
    navigated: bool = False  # a click took the page somewhere else


@dataclass
class RenderedPage:
    html: str
    url: str  # where the browser ended up
    scrolls: int = 0
    clicks: int = 0
    expands: int = 0


class PageRenderer(Protocol):
    async def render(self, url: str, in_scope: InScope) -> RenderedPage: ...


BrowserFactory = Callable[[], AbstractAsyncContextManager[PageRenderer]]


def _control(raw: dict[str, Any]) -> Control:
    return Control(
        id=int(raw["id"]),
        tag=str(raw.get("tag", "")),
        text=str(raw.get("text", "")),
        label=str(raw.get("label", "")),
        href=str(raw.get("href", "")),
        type=str(raw.get("type", "")),
        target=str(raw.get("target", "")),
        download=bool(raw.get("download", False)),
        in_form=bool(raw.get("in_form", False)),
    )


def _same_page(url: str, page_url: str) -> bool:
    a, b = urlsplit(url), urlsplit(page_url)
    return (a.hostname, a.path.rstrip("/")) == (b.hostname, b.path.rstrip("/"))


def is_safe_click(control: Control, page_url: str, in_scope: InScope) -> bool:
    """Whether clicking `control` can only reveal more of the current page.

    A control inside a form, a submit-type control, one whose words say it acts (log in, buy, send,
    download), and a link that goes to another page, opens a new tab or downloads are all refused.
    A link is clicked only when it points nowhere (#, javascript:) or at the page itself, as
    pagination does, and that page is on the company's domain.
    """
    if control.in_form or control.type in _NEVER_TYPES or control.download or control.target.lower() == "_blank":
        return False
    key = search_key(control.name)
    if any(p.search(key) for p in _NEVER):
        return False
    if control.tag == "a" and control.href.strip():
        href = control.href.strip()
        if href.lower().startswith(_NO_NAVIGATION):
            return True
        target = urljoin(page_url, href)
        return in_scope(target) and _same_page(target, page_url)
    return True


def is_load_more(control: Control) -> bool:
    key = search_key(control.name)
    return any(p.search(key) for p in _LOAD_MORE)


async def _controls(page: BrowserPage, kind: str) -> list[Control]:
    return [_control(raw) for raw in (await page.evaluate(JS_CONTROLS, kind)) or []]


async def _click(page: BrowserPage, control: Control, timeout_ms: float) -> bool:
    try:
        await page.click(f'[data-edrak-click="{control.id}"]', timeout=timeout_ms)
        return True
    except Exception as exc:  # covered, detached, or gone since it was listed
        logger.debug("Could not click %r: %s", control.name, exc)
        return False


async def scroll_to_end(page: BrowserPage, max_scrolls: int, pause_ms: float) -> int:
    """Scroll to the bottom until the page stops getting taller or `max_scrolls` is reached. Returns the scrolls made."""
    height = await page.evaluate(JS_HEIGHT)
    scrolls = 0
    while scrolls < max_scrolls:
        await page.evaluate(JS_SCROLL)
        scrolls += 1
        await page.wait_for_timeout(pause_ms)
        grown = await page.evaluate(JS_HEIGHT)
        if grown <= height:
            break
        height = grown
    return scrolls


async def click_load_more(
    page: BrowserPage, start_url: str, in_scope: InScope, max_clicks: int, pause_ms: float, timeout_ms: float
) -> tuple[int, bool]:
    """Click "load more" style buttons while they add text, at most `max_clicks` times.

    Returns (clicks, navigated). Navigated is true when a click took the page to another address.
    """
    clicks = 0
    while clicks < max_clicks:
        candidates = [c for c in await _controls(page, "more") if is_load_more(c) and is_safe_click(c, start_url, in_scope)]
        if not candidates:
            break
        before = await page.evaluate(JS_TEXT_LENGTH)
        if not await _click(page, candidates[0], timeout_ms):
            break
        clicks += 1
        await page.wait_for_timeout(pause_ms)
        if not _same_page(page.url, start_url) or not in_scope(page.url):
            return clicks, True
        if await page.evaluate(JS_TEXT_LENGTH) <= before:  # nothing was added, so another click will not add more
            break
    return clicks, False


async def expand_sections(
    page: BrowserPage, start_url: str, in_scope: InScope, max_expands: int, pause_ms: float, timeout_ms: float
) -> tuple[int, bool]:
    """Open details elements, then click closed tabs and accordions, at most `max_expands` clicks."""
    opened = int(await page.evaluate(JS_OPEN_DETAILS) or 0)
    done: set[tuple[str, str, str]] = set()
    clicked = 0
    while clicked < max_expands:
        pending = [
            c for c in await _controls(page, "expand")
            if c.identity not in done and is_safe_click(c, start_url, in_scope)
        ]
        if not pending:
            break
        done.add(pending[0].identity)
        if not await _click(page, pending[0], timeout_ms):
            continue
        clicked += 1
        await page.wait_for_timeout(pause_ms)
        if not _same_page(page.url, start_url) or not in_scope(page.url):
            return opened + clicked, True
    return opened + clicked, False


async def interact(page: BrowserPage, start_url: str, in_scope: InScope) -> Interaction:
    """Scroll, click "load more", scroll again if that added content, then expand tabs and accordions."""
    pause = settings.COMPANY_RESEARCH_CRAWL_SCROLL_PAUSE_MS
    timeout = settings.PROFILE_IMPORT_FETCH_TIMEOUT_S * 1000
    result = Interaction()
    result.scrolls = await scroll_to_end(page, settings.COMPANY_RESEARCH_CRAWL_MAX_SCROLLS, pause)
    result.clicks, result.navigated = await click_load_more(
        page, start_url, in_scope, settings.COMPANY_RESEARCH_CRAWL_MAX_CLICKS, pause, timeout
    )
    if result.navigated:
        return result
    if result.clicks:
        result.scrolls += await scroll_to_end(page, settings.COMPANY_RESEARCH_CRAWL_MAX_SCROLLS, pause)
    result.expands, result.navigated = await expand_sections(
        page, start_url, in_scope, settings.COMPANY_RESEARCH_CRAWL_MAX_EXPANDS, pause, timeout
    )
    return result


class PlaywrightBrowser:
    """One headless Chromium for a whole crawl. Use as `async with PlaywrightBrowser() as browser`."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    async def __aenter__(self) -> "PlaywrightBrowser":
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RendererUnavailable("Playwright is not installed.") from exc
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch()
        except Exception as exc:  # Playwright raises its own error type when the browser is missing
            await self._playwright.stop()
            self._playwright = None
            raise RendererUnavailable("Chromium is not installed (run: playwright install chromium).") from exc
        self._context = await self._browser.new_context(user_agent=USER_AGENT)
        await self._context.route("**/*", make_route_guard())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._playwright = self._browser = self._context = None

    async def render(self, url: str, in_scope: InScope) -> RenderedPage:
        """Open `url`, interact with it, and return the HTML as it then stands."""
        assert self._context is not None, "use PlaywrightBrowser as an async context manager"
        timeout_ms = int(settings.PROFILE_IMPORT_FETCH_TIMEOUT_S * 1000)
        page = await self._context.new_page()

        async def stay_on_site(route: Any) -> None:
            # A page of the company's own site is never left for another one, whatever script asks.
            request = route.request
            if request.is_navigation_request() and request.frame.parent_frame is None and not in_scope(request.url):
                await route.abort()
            else:
                await route.fallback()

        try:
            await page.route("**/*", stay_on_site)
            await page.goto(url, wait_until="load", timeout=timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
            except Exception:  # a page that never goes idle is still read as it stands
                pass
            done = await interact(page, url, in_scope)
            if done.navigated:  # a click went elsewhere: read the page again as it first loaded
                await page.goto(url, wait_until="load", timeout=timeout_ms)
            return RenderedPage(
                html=await page.content(), url=page.url, scrolls=done.scrolls, clicks=done.clicks, expands=done.expands
            )
        finally:
            await page.close()
