import asyncio
import json
from pathlib import Path

import httpx
import pytest

from edrak.company_profile.url_import.errors import SiteUnreachable
from edrak.company_profile.url_import.fetch import SiteFetcher
from edrak.company_profile.url_import.importer import NEEDS_JS_WARNING, import_profile_from_url, metadata_text
from edrak.company_profile.url_import.parse import parse_page
from edrak.company_profile.url_import.render import RendererUnavailable, make_route_guard
from edrak.core.config import settings

FIXTURES = Path(__file__).parent / "fixtures" / "company_sites"


def fx(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_site(pages, robots="User-agent: *\nAllow: /\n"):
    """A fetcher over an in-memory site. `pages` maps a path to HTML, bytes, or (status, body, type)."""
    calls = []

    def handler(request):
        path = request.url.raw_path.decode()
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots, headers={"content-type": "text/plain"})
        entry = pages.get(path) or pages.get(request.url.path)
        if entry is None:
            return httpx.Response(404, text="not found")
        if isinstance(entry, tuple):
            status, body, ctype = entry
            return httpx.Response(status, content=body if isinstance(body, bytes) else body.encode(), headers={"content-type": ctype})
        ctype = "application/xml" if path.endswith(".xml") else "text/html; charset=utf-8"
        return httpx.Response(200, content=entry if isinstance(entry, bytes) else entry.encode(), headers={"content-type": ctype})

    async def public(h, p):
        return ["93.184.216.34"]

    return SiteFetcher(transport=httpx.MockTransport(handler), resolver=public), calls


class FakeLLM:
    def __init__(self, profile=None, sources=None):
        self.calls = []
        self.profile = profile or {"name": "Acme Pay", "products": ["Acme Terminal"], "notes": "Acme Pay sells card payment terminals.", "markets": ["Egypt"]}
        self.sources = sources or {"name": ["S1"], "products": ["S2"], "notes": ["S2"], "markets": ["S2"]}

    def __call__(self, messages):
        self.calls.append(messages)
        return json.dumps({"profile": self.profile, "field_sources": self.sources})


def run(url, fetcher, renderer=None, llm=None):
    async def no_browser(urls):
        raise RendererUnavailable("no browser here")

    return asyncio.run(import_profile_from_url(url, fetcher=fetcher, renderer=renderer or no_browser, llm=llm or FakeLLM()))


ACME_PAGES = {
    "/": fx("en_home.html"),
    "/about": fx("about.html"),
    "/products": fx("products.html"),
    "/pricing": (404, "gone", "text/html"),
    "/contact": fx("about.html"),
    "/sitemap.xml": fx("sitemap_pages.xml"),
}


def test_a_site_becomes_a_draft_with_sources_in_priority_order():
    site, calls = make_site(ACME_PAGES)
    llm = FakeLLM()
    draft = run("acme.example", site, llm=llm)

    assert [(s.id, s.type.value) for s in draft.sources[:2]] == [("S1", "page_metadata"), ("S2", "web_page")]
    refs = [s.ref for s in draft.sources]
    assert refs[1] == "https://acme.example/"
    assert refs.index("https://acme.example/about") < refs.index("https://acme.example/products")  # about before products
    assert draft.profile.name == "Acme Pay" and draft.profile.products == ["Acme Terminal"]
    assert draft.field_sources["name"] == ["S1"]

    context = llm.calls[0][1]["content"]
    assert "[S1] type=page_metadata" in context and "Meta description: Card terminals" in context
    assert "Acme Terminal is a countertop card reader" in context  # main text of the products page
    assert "| Acme Terminal | 1.5% |" in context  # the table is kept
    assert "Log in" not in context  # navigation is not main text

    assert [(l.platform.value, l.url) for l in draft.links] == [
        ("website", "https://acme.example"),
        ("facebook", "https://www.facebook.com/acmepay"),
        ("x", "https://x.com/acmepay"),
        ("linkedin", "https://www.linkedin.com/company/acmepay"),
    ]
    assert not any("/blog" in c or "/careers" in c or "/login" in c or "/privacy" in c for c in calls)
    assert any("could not be read" in w for w in draft.warnings)  # /pricing answered 404


def test_the_page_cap_includes_the_homepage(monkeypatch):
    monkeypatch.setattr(settings, "PROFILE_IMPORT_MAX_PAGES", 3)
    site, calls = make_site(ACME_PAGES)
    draft = run("https://acme.example", site)
    pages = [s for s in draft.sources if s.type.value == "web_page"]
    assert len(pages) <= 3
    fetched = [c for c in calls if c not in ("/robots.txt", "/sitemap.xml")]
    assert len(fetched) <= 3


def test_pages_robots_disallows_are_skipped_with_a_warning_and_a_blocked_site_fails():
    site, calls = make_site(ACME_PAGES, robots="User-agent: *\nDisallow: /about\n")
    draft = run("acme.example", site)
    assert "/about" not in calls
    assert any("robots.txt does not allow" in w for w in draft.warnings)

    site, _ = make_site(ACME_PAGES, robots="User-agent: *\nDisallow: /\n")
    with pytest.raises(SiteUnreachable, match="robots.txt"):
        run("acme.example", site)


def test_an_unreachable_homepage_is_an_error():
    site, _ = make_site({"/": (500, "boom", "text/html")})
    with pytest.raises(SiteUnreachable):
        run("acme.example", site)
    site, _ = make_site({"/": (200, b"%PDF-1.4", "application/pdf")})
    with pytest.raises(SiteUnreachable, match="web page"):
        run("acme.example", site)


def test_a_private_or_unresolvable_start_address_is_reported_as_such_not_as_robots():
    from edrak.company_profile.url_import.errors import UrlBlocked

    async def private(host, port):
        return ["10.0.0.9"]

    async def nothing(host, port):
        return []

    for resolver, error, text in ((private, UrlBlocked, "private"), (nothing, SiteUnreachable, "could not be found")):
        fetcher = SiteFetcher(transport=httpx.MockTransport(lambda r: pytest.fail("no request may be sent")), resolver=resolver)
        with pytest.raises(error, match=text):
            run("acme.example", fetcher)


def test_a_redirect_off_the_site_is_not_read():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "acme.example" and request.url.path == "/":
            return httpx.Response(200, html=fx("en_home.html"))
        if request.url.path == "/about":
            return httpx.Response(302, headers={"location": "https://evil.example/about"})
        return httpx.Response(404)

    async def public(h, p):
        return ["93.184.216.34"]

    site = SiteFetcher(transport=httpx.MockTransport(handler), resolver=public)
    draft = run("acme.example", site)
    assert not any("evil.example" in s.ref for s in draft.sources)
    assert any("could not be read" in w for w in draft.warnings)


# ------------------------------------------------------------------ sitemaps


def test_sitemap_urls_are_candidates_and_an_index_is_read_one_level_deep():
    pages = {
        "/": fx("en_home.html").replace("<a href=\"/about\">About us</a>", ""),
        "/sitemap.xml": fx("sitemap_index.xml"),
        "/sitemap-pages.xml": fx("sitemap_pages.xml"),
        "/who-we-are": fx("about.html"),
        "/solutions": fx("products.html"),
    }
    site, calls = make_site(pages, robots="User-agent: *\nSitemap: https://acme.example/sitemap.xml\n")
    draft = run("acme.example", site)
    refs = {s.ref for s in draft.sources}
    assert "https://acme.example/who-we-are" in refs and "https://acme.example/solutions" in refs
    assert not any(c.startswith("/careers/") or c.startswith("/blog/") or c == "/terms" for c in calls)
    assert "/sitemap.xml" in calls and calls.count("/sitemap.xml") == 1  # listed twice, read once


# ------------------------------------------------------------------ bilingual and Arabic


def test_an_arabic_site_is_read_in_arabic():
    pages = {"/": fx("ar_home.html"), "/ar/services": fx("ar_home.html")}
    site, _ = make_site(pages)
    llm = FakeLLM({"name": "فوري تك", "aliases": [], "notes": "Fawry Tech provides payment solutions."}, {"name": ["S1"], "notes": ["S2"]})
    draft = run("fawry.example", site, llm=llm)
    context = llm.calls[0][1]["content"]
    assert "حلول الدفع الإلكتروني" in context
    assert draft.profile.name == "فوري تك"
    assert next(s for s in draft.sources if s.type.value == "web_page").ref == "https://fawry.example/"


def test_a_bilingual_site_adds_its_arabic_about_page():
    pages = {
        "/": fx("bilingual_home.html"),
        "/ar/": fx("bilingual_ar_home.html"),
        "/about": fx("about.html"),
        "/services": fx("products.html"),
        "/ar/about-us": fx("ar_home.html"),
        "/ar/services": fx("ar_home.html"),
    }
    site, calls = make_site(pages)
    draft = run("nileledger.example", site)
    refs = [s.ref for s in draft.sources]
    assert "https://nileledger.example/ar/about-us" in refs
    assert "https://nileledger.example/ar/services" not in refs
    assert calls.count("/ar/") == 1


# ------------------------------------------------------------------ JavaScript fallback


RICH = fx("about.html").replace(
    "</main>",
    "<p>Rendered content about payments reaches merchants in Cairo, Alexandria and Giza.</p>"
    "<p>Settlement arrives the next business day, and every sale appears in the daily report.</p>"
    "<p>Support staff answer calls in Arabic and English from eight in the morning until midnight.</p></main>",
)


def spa_pages():
    return {"/": fx("spa_home.html")}


def test_a_thin_homepage_is_rendered_when_a_browser_is_available():
    rendered_calls = []

    async def renderer(urls):
        rendered_calls.append(list(urls))
        return {"https://acme.example/": RICH.replace("</body>", '<nav><a href="/products">Products</a></nav></body>')}

    pages = {"/": fx("spa_home.html"), "/products": fx("spa_home.html")}
    site, calls = make_site(pages)
    llm = FakeLLM()
    draft = run("acme.example", site, renderer=renderer, llm=llm)

    assert rendered_calls[0] == ["https://acme.example/"]  # the homepage first
    assert "/products" in calls  # a link that only exists after rendering was followed
    assert ["https://acme.example/products"] in rendered_calls  # and the selected pages were rendered too
    assert "Rendered content about payments" in llm.calls[0][1]["content"]
    assert NEEDS_JS_WARNING not in draft.warnings


def test_a_thin_homepage_without_a_browser_warns_and_carries_on():
    site, _ = make_site(spa_pages())
    llm = FakeLLM({"name": "Shell App"}, {"name": ["S1"]})
    draft = run("acme.example", site, llm=llm)
    assert NEEDS_JS_WARNING in draft.warnings
    assert draft.sources and draft.sources[0].type.value == "page_metadata"  # the meta tags were still read
    assert draft.profile.name == "Shell App"


def test_a_healthy_homepage_never_starts_a_browser():
    async def renderer(urls):
        raise AssertionError("not needed")

    site, _ = make_site(ACME_PAGES)
    draft = run("acme.example", site, renderer=renderer)
    assert NEEDS_JS_WARNING not in draft.warnings


def test_a_crashing_browser_does_not_fail_the_import():
    async def renderer(urls):
        raise RuntimeError("chromium crashed")

    site, _ = make_site(spa_pages())
    draft = run("acme.example", site, renderer=renderer, llm=FakeLLM({"name": "Shell App"}, {"name": ["S1"]}))
    assert NEEDS_JS_WARNING in draft.warnings


def test_the_browser_route_guard_aborts_private_and_media_requests():
    class Req:
        def __init__(self, url, kind="document"):
            self.url, self.resource_type = url, kind

    class Route:
        def __init__(self, request):
            self.request, self.result = request, None

        async def abort(self):
            self.result = "abort"

        async def continue_(self):
            self.result = "continue"

    async def resolver(host, port):
        return ["10.0.0.4"] if host == "intranet.example" else ["93.184.216.34"]

    async def go():
        guard = make_route_guard(resolver)
        out = {}
        for name, req in {
            "ok": Req("https://acme.example/app.js", "script"),
            "private-name": Req("https://intranet.example/x", "xhr"),
            "private-ip": Req("http://169.254.169.254/latest", "xhr"),
            "image": Req("https://acme.example/logo.png", "image"),
            "data": Req("data:text/plain,hi", "other"),
        }.items():
            route = Route(req)
            await guard(route)
            out[name] = route.result
        return out

    assert asyncio.run(go()) == {"ok": "continue", "private-name": "abort", "private-ip": "abort", "image": "abort", "data": "continue"}


def test_metadata_text_lists_the_tags_and_the_organization():
    text = metadata_text(parse_page(fx("jsonld_home.html"), "https://delta.example/"))
    assert "Page title: Delta Freight" in text
    assert "Organization (JSON-LD):" in text and "  name: Delta Freight" in text
    assert "area served: Egypt; Libya" in text and "same as: https://www.facebook.com/deltafreight" in text
