import asyncio
import sys
import types

import pytest

from edrak.company_profile.url_import.render import RendererUnavailable
from edrak.company_research.crawler import browser as b
from edrak.company_research.crawler.rules import in_scope as scope_of

URL = "https://acme.example/resources"


def in_scope(url):
    return scope_of(url, URL)


def control(id, text="", tag="button", **extra):
    return {"id": id, "tag": tag, "text": text, **extra}


class FakePage:
    """A page that answers the browser steps from a script instead of from a browser."""

    def __init__(self, heights=(1000,), more=(), expand=(), grow=200, opens=0, url=URL, goes_to=None):
        self.heights = list(heights)
        self.more = list(more)
        self.expand = list(expand)
        self.grow = grow
        self.opens = opens
        self.url = url
        self.goes_to = goes_to or {}  # control id -> the address clicking it leads to
        self.text_length = 1000
        self.height_reads = 0
        self.scrolls = 0
        self.clicked: list[int] = []
        self.waits = 0

    async def evaluate(self, expression, arg=None):
        if expression == b.JS_HEIGHT:
            value = self.heights[min(self.height_reads, len(self.heights) - 1)]
            self.height_reads += 1
            return value
        if expression == b.JS_SCROLL:
            self.scrolls += 1
            return None
        if expression == b.JS_TEXT_LENGTH:
            return self.text_length
        if expression == b.JS_OPEN_DETAILS:
            return self.opens
        if expression == b.JS_CONTROLS:
            return self.more if arg == "more" else self.expand
        raise AssertionError(f"unexpected script {expression!r}")

    async def click(self, selector, timeout=0):
        id = int(selector.split('"')[1])
        self.clicked.append(id)
        if id in self.goes_to:
            self.url = self.goes_to[id]
        else:
            self.text_length += self.grow

    async def wait_for_timeout(self, timeout):
        self.waits += 1


@pytest.fixture(autouse=True)
def small_limits(monkeypatch):
    for name, value in dict(MAX_SCROLLS=5, MAX_CLICKS=3, MAX_EXPANDS=4, SCROLL_PAUSE_MS=0).items():
        monkeypatch.setattr(b.settings, f"COMPANY_RESEARCH_CRAWL_{name}", value)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ scrolling


def test_scrolling_stops_when_the_page_stops_growing():
    page = FakePage(heights=[1000, 2000, 3000, 3000, 4000])
    assert run(b.scroll_to_end(page, max_scrolls=10, pause_ms=0)) == 3
    assert page.scrolls == 3 and page.waits == 3  # the 4000 that comes after the stop is never reached


def test_a_page_that_never_grows_is_scrolled_once():
    page = FakePage(heights=[1000])
    assert run(b.scroll_to_end(page, max_scrolls=10, pause_ms=0)) == 1


def test_scrolling_stops_at_the_cap_on_a_page_that_always_grows():
    page = FakePage(heights=[1000 * n for n in range(1, 50)])
    assert run(b.scroll_to_end(page, max_scrolls=4, pause_ms=0)) == 4
    assert page.scrolls == 4


# ------------------------------------------------------------------ load more


def test_load_more_is_clicked_until_the_cap_while_it_keeps_adding_text():
    page = FakePage(more=[control(0, "Load more")])
    clicks, navigated = run(b.click_load_more(page, URL, in_scope, max_clicks=3, pause_ms=0, timeout_ms=100))
    assert (clicks, navigated) == (3, False)
    assert page.clicked == [0, 0, 0]


def test_load_more_stops_when_a_click_adds_nothing():
    page = FakePage(more=[control(0, "Show more")], grow=0)
    clicks, _ = run(b.click_load_more(page, URL, in_scope, max_clicks=5, pause_ms=0, timeout_ms=100))
    assert clicks == 1


def test_load_more_stops_when_the_button_is_gone():
    page = FakePage(more=[])
    assert run(b.click_load_more(page, URL, in_scope, max_clicks=5, pause_ms=0, timeout_ms=100)) == (0, False)
    assert page.clicked == []


@pytest.mark.parametrize("text", ["Load more", "load MORE", "Show more", "View all", "See more", "عرض المزيد", "المزيد", "عرض الكل", "Load more results"])
def test_the_words_that_name_a_load_more_control(text):
    assert b.is_load_more(b.Control(0, "button", text))


@pytest.mark.parametrize("text", ["Contact us", "Moreover", "Read the story", "", "Order now"])
def test_other_buttons_are_not_load_more(text):
    assert not b.is_load_more(b.Control(0, "button", text))


# ------------------------------------------------------------------ what is never clicked


def test_only_safe_controls_are_clicked():
    controls = [
        control(0, "Submit"),
        control(1, "Subscribe to the newsletter"),
        control(2, "Log in to load more"),
        control(3, "Load more", in_form=True),
        control(4, "View all", tag="a", href="https://other.example/all"),
        control(5, "View all", tag="a", href="/products"),
        control(6, "Show more", tag="input", type="submit"),
        control(7, "Load more", tag="a", href="/files/list.pdf", download=True),
        control(8, "Load more", tag="a", href="/page/2", target="_blank"),
        control(9, "Buy more"),
        control(10, "Add to cart"),
        control(11, "تسجيل الدخول لعرض المزيد"),
        control(12, "عرض المزيد"),
    ]
    page = FakePage(more=controls)
    clicks, navigated = run(b.click_load_more(page, URL, in_scope, max_clicks=1, pause_ms=0, timeout_ms=100))
    assert page.clicked == [12]  # the first control that is a load-more and safe
    assert (clicks, navigated) == (1, False)

    page = FakePage(more=controls[:12])
    assert run(b.click_load_more(page, URL, in_scope, max_clicks=5, pause_ms=0, timeout_ms=100)) == (0, False)
    assert page.clicked == []


@pytest.mark.parametrize(
    "item, safe",
    [
        (control(0, "Load more"), True),
        (control(0, "Load more", tag="a", href="#"), True),
        (control(0, "Load more", tag="a", href="javascript:void(0)"), True),
        (control(0, "Load more", tag="a", href="?page=2"), True),  # pagination: the same page
        (control(0, "Load more", tag="a", href="/resources?page=2"), True),
        (control(0, "Load more", tag="a", href="/products"), False),
        (control(0, "Load more", tag="a", href="https://other.example/resources"), False),
        (control(0, "Load more", tag="a", href="mailto:a@b.example"), False),
        (control(0, "Load more", in_form=True), False),
        (control(0, "Load more", tag="input", type="submit"), False),
        (control(0, "Load more", tag="a", href="/resources", download=True), False),
        (control(0, "Load more", tag="a", href="/resources", target="_blank"), False),
        (control(0, "Sign in"), False),
        (control(0, "Sign up for more"), False),
        (control(0, "Checkout"), False),
        (control(0, "Pay now"), False),
        (control(0, "Send"), False),
        (control(0, "", label="Download brochure"), False),
        (control(0, "الدفع"), False),
        (control(0, "اشترك الآن"), False),
        (control(0, "Payments"), True),  # whole words only: payments is not pay
    ],
)
def test_what_may_be_clicked(item, safe):
    assert b.is_safe_click(b._control(item), URL, in_scope) is safe


def test_a_click_that_leaves_the_page_ends_the_clicking():
    page = FakePage(more=[control(0, "Load more")], goes_to={0: "https://acme.example/elsewhere"})
    assert run(b.click_load_more(page, URL, in_scope, max_clicks=5, pause_ms=0, timeout_ms=100)) == (1, True)
    page = FakePage(more=[control(0, "Load more")], goes_to={0: "https://other.example/"})
    assert run(b.click_load_more(page, URL, in_scope, max_clicks=5, pause_ms=0, timeout_ms=100)) == (1, True)


# ------------------------------------------------------------------ tabs and accordions


def test_details_are_opened_and_tabs_and_accordions_are_clicked_once_each_up_to_the_cap():
    expanders = [control(i, f"Tab {i}", role="tab") for i in range(6)] + [control(9, "Subscribe")]
    page = FakePage(expand=expanders, opens=2)
    expands, navigated = run(b.expand_sections(page, URL, in_scope, max_expands=4, pause_ms=0, timeout_ms=100))
    assert (expands, navigated) == (2 + 4, False)
    assert page.clicked == [0, 1, 2, 3]  # each once, in order, and never the Subscribe control


def test_an_expander_listed_again_is_not_clicked_again():
    page = FakePage(expand=[control(0, "Our story"), control(1, "Our values")])
    expands, _ = run(b.expand_sections(page, URL, in_scope, max_expands=10, pause_ms=0, timeout_ms=100))
    assert expands == 2 and page.clicked == [0, 1]


def test_unsafe_expanders_are_left_alone():
    page = FakePage(expand=[control(0, "Log in", tag="summary"), control(1, "Details", tag="summary", in_form=True), control(2, "Apply", tag="summary")])
    assert run(b.expand_sections(page, URL, in_scope, max_expands=10, pause_ms=0, timeout_ms=100)) == (0, False)
    assert page.clicked == []


# ------------------------------------------------------------------ the whole interaction


def test_a_page_is_scrolled_then_loaded_further_then_scrolled_again_then_expanded():
    page = FakePage(heights=[1000, 2000, 2000], more=[control(0, "Load more")], expand=[control(1, "Tab", role="tab")], opens=1)
    done = run(b.interact(page, URL, in_scope))
    assert done.clicks == 3 and not done.navigated
    assert done.scrolls == 2 + 1  # two to reach the end, then one more after the clicks added content
    assert done.expands == 1 + 1
    assert page.clicked.count(0) == 3 and 1 in page.clicked


def test_interaction_stops_at_a_navigation_without_expanding():
    page = FakePage(more=[control(0, "Load more")], expand=[control(1, "Tab")], goes_to={0: "https://acme.example/other"})
    done = run(b.interact(page, URL, in_scope))
    assert done.navigated and done.expands == 0 and page.clicked == [0]


# ------------------------------------------------------------------ the Playwright wrapper


def test_without_playwright_the_browser_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)

    async def go():
        async with b.PlaywrightBrowser():
            pass

    with pytest.raises(RendererUnavailable):
        run(go())


def install_fake_playwright(monkeypatch, launch_error=None):
    events = []

    class Route:
        def __init__(self, url, navigation=False, main=True):
            self.request = types.SimpleNamespace(
                url=url, resource_type="document", is_navigation_request=lambda: navigation,
                frame=types.SimpleNamespace(parent_frame=None if main else object()),
            )
            self.result = None

        async def abort(self):
            self.result = "abort"

        async def fallback(self):
            self.result = "fallback"

    class Page:
        url = URL

        def __init__(self):
            self.handler = None

        async def route(self, pattern, handler):
            self.handler = handler

        async def goto(self, url, **kw):
            events.append(("goto", url))

        async def wait_for_load_state(self, *a, **kw):
            pass

        async def evaluate(self, expression, arg=None):
            return {b.JS_HEIGHT: 1000, b.JS_OPEN_DETAILS: 0}.get(expression, [] if expression == b.JS_CONTROLS else 0)

        async def wait_for_timeout(self, ms):
            pass

        async def content(self):
            return "<html><body>rendered</body></html>"

        async def close(self):
            events.append(("page closed",))

    pages = []

    class Context:
        async def route(self, pattern, handler):
            events.append(("context route",))

        async def new_page(self):
            pages.append(Page())
            return pages[-1]

    class Browser:
        async def new_context(self, **kw):
            events.append(("context", kw.get("user_agent")))
            return Context()

        async def close(self):
            events.append(("browser closed",))

    class Chromium:
        async def launch(self):
            if launch_error:
                raise launch_error
            return Browser()

    class Playwright:
        chromium = Chromium()

        async def stop(self):
            events.append(("stopped",))

    class Starter:
        async def start(self):
            return Playwright()

    module = types.ModuleType("playwright.async_api")
    module.async_playwright = lambda: Starter()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    return events, pages, Route


def test_the_browser_opens_a_guarded_context_renders_a_page_and_closes(monkeypatch):
    events, pages, _ = install_fake_playwright(monkeypatch)

    async def go():
        async with b.PlaywrightBrowser() as browser:
            return await browser.render(URL, in_scope)

    rendered = run(go())
    assert "rendered" in rendered.html and rendered.url == URL and rendered.scrolls == 1
    assert ("context route",) in events and ("page closed",) in events and ("browser closed",) in events and ("stopped",) in events
    assert ("goto", URL) in events


def test_a_navigation_away_from_the_company_site_is_aborted_in_the_page(monkeypatch):
    _, pages, Route = install_fake_playwright(monkeypatch)

    async def go():
        async with b.PlaywrightBrowser() as browser:
            await browser.render(URL, in_scope)
        handler = pages[0].handler
        away, same, script, frame = (
            Route("https://other.example/", navigation=True),
            Route("https://news.acme.example/", navigation=True),
            Route("https://cdn.other.example/app.js", navigation=False),
            Route("https://other.example/embed", navigation=True, main=False),
        )
        for route in (away, same, script, frame):
            await handler(route)
        return [r.result for r in (away, same, script, frame)]

    assert run(go()) == ["abort", "fallback", "fallback", "fallback"]


def test_a_missing_chromium_build_is_reported_as_unavailable(monkeypatch):
    events, _, _ = install_fake_playwright(monkeypatch, launch_error=RuntimeError("Executable doesn't exist"))

    async def go():
        async with b.PlaywrightBrowser():
            pass

    with pytest.raises(RendererUnavailable):
        run(go())
    assert ("stopped",) in events
