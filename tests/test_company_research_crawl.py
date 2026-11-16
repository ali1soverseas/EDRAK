import asyncio

import pytest
from crawl_fixtures import PRIVATE_HOST, Clock, FakeBrowser, Site, acme_pages, crawl, fx, limits

from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked
from edrak.company_research.crawler import FetchMode, StopReason
from edrak.company_research.crawler.crawl import BROWSER_UNAVAILABLE_WARNING, HostThrottle

HOME = "https://acme.example/"


def urls(result):
    return [p.url for p in result.pages]


def paths(result):
    return [p.url.removeprefix("https://acme.example") for p in result.pages]


# ------------------------------------------------------------------ what comes back


def test_a_crawl_returns_pages_with_title_language_text_mode_links_and_pdf_links():
    result = crawl(Site(acme_pages()))
    home = result.pages[0]
    assert home.url == HOME and home.title == "Acme Pay" and home.language == "en" and home.depth == 0
    assert home.fetch_mode is FetchMode.STATIC
    assert "card payment terminals" in home.text
    assert "https://acme.example/about" in [link.url for link in home.links]
    assert "https://facebook.com/acmepay" in [link.url for link in home.links]  # links off the site are reported, not followed
    assert [p.url for p in home.pdf_links] == ["https://acme.example/docs/overview.pdf"]
    assert home.pdf_links[0].text == "Company overview (PDF)" and home.pdf_links[0].found_on == HOME

    investors = next(p for p in result.pages if p.url.endswith("/investors"))
    assert investors.section == "investors" and investors.depth == 1
    assert [p.url for p in investors.pdf_links] == ["https://acme.example/docs/annual-report-2024.pdf"]

    assert result.stats.pages_crawled == len(result.pages) == result.stats.static_pages + result.stats.dynamic_pages
    assert result.start_url == HOME


def test_pdf_links_are_listed_once_and_never_fetched():
    site = Site(acme_pages())
    result = crawl(site)
    found = [p.url for p in result.pdf_links]
    assert len(found) == len(set(found))
    assert {
        "https://acme.example/docs/overview.pdf",
        "https://acme.example/docs/annual-report-2024.pdf",
        "https://acme.example/docs/q1-results.pdf",
        "https://acme.example/docs/sitemap-listed.pdf",
    } <= set(found)
    assert result.stats.pdf_links == len(found)
    assert not [p for p in site.paths if p.endswith(".pdf")]
    assert next(p for p in result.pdf_links if p.url.endswith("sitemap-listed.pdf")).found_on == "sitemap"


def test_the_arabic_page_language_is_detected():
    result = crawl(Site(acme_pages()))
    arabic = next(p for p in result.pages if p.url.endswith("/ar/about"))
    assert arabic.language == "ar"


# ------------------------------------------------------------------ scope


def test_the_crawl_stays_on_the_domain_and_its_subdomains():
    site = Site(acme_pages())
    result = crawl(site)
    assert "https://news.acme.example/" in urls(result)  # a subdomain is the company's own site
    hosts = {host for host, _ in site.requests}
    assert hosts <= {"acme.example", "news.acme.example"}
    assert "facebook.com" not in hosts and "other.example" not in hosts


def test_scope_follows_the_registered_domain_of_the_start_address():
    site = Site({"/": fx("home.html"), "/about": fx("about.html"), "news.acme.example/": fx("news_home.html")})
    result = crawl(site, "https://www.acme.example/")
    assert result.pages[0].url == "https://www.acme.example/"
    assert "https://news.acme.example/" in urls(result)  # a sibling subdomain of the same registered domain
    assert {host for host, _ in site.requests} <= {"www.acme.example", "acme.example", "news.acme.example"}  # acme.example: the sitemap robots.txt names


def test_a_redirect_to_another_domain_is_not_followed():
    site = Site(acme_pages())
    result = crawl(site)
    assert ("other.example", "/about") not in site.requests
    assert result.stats.skipped_off_site == 1
    assert any("redirected away" in w for w in result.warnings)


def test_a_redirect_to_a_private_address_is_refused_before_it_is_requested():
    site = Site(acme_pages())
    result = crawl(site)
    # /moved sends the crawl to a host on the company's domain that resolves to a private address
    assert ("acme.example", "/moved") in site.requests
    assert not [r for r in site.requests if r[0] == PRIVATE_HOST]
    assert result.stats.failed >= 1
    assert not any("Secret" in p.title for p in result.pages)


def test_a_redirect_hop_to_a_literal_private_address_is_refused():
    pages = {"/": fx("home.html"), "/moved": (302, "", "http://169.254.169.254/latest/meta-data"), "/old-about": (404, "", "text/html")}
    site = Site(pages)
    result = crawl(site)
    assert not [r for r in site.requests if r[0] == "169.254.169.254"]
    assert result.stats.failed >= 1


def test_a_start_address_that_is_not_public_is_refused():
    with pytest.raises(UrlBlocked):
        crawl(Site(acme_pages()), "http://127.0.0.1/")
    with pytest.raises(UrlBlocked):
        crawl(Site(acme_pages()), "http://169.254.169.254/")
    with pytest.raises(UrlBlocked):
        crawl(Site(acme_pages()), "file:///etc/passwd")


def test_a_start_address_that_redirects_to_a_private_host_is_refused():
    site = Site({"/": (302, "", f"https://{PRIVATE_HOST}/")})
    with pytest.raises(UrlBlocked):
        crawl(site)
    assert not [r for r in site.requests if r[0] == PRIVATE_HOST]


def test_a_homepage_that_moves_to_another_domain_sets_the_scope_there():
    pages = {"/": (301, "", "https://acme-group.example/"), "acme-group.example/": fx("home.html"), "acme-group.example/about": fx("about.html")}
    site = Site(pages)
    result = crawl(site)
    assert result.pages[0].url == "https://acme-group.example/"
    assert any("moved to acme-group.example" in w for w in result.warnings)
    assert {host for host, _ in site.requests} <= {"acme.example", "acme-group.example"}


# ------------------------------------------------------------------ robots.txt


def test_pages_robots_txt_disallows_are_not_requested():
    site = Site(acme_pages())
    result = crawl(site)
    assert "/private/plan" not in site.paths
    assert result.stats.skipped_robots == 1
    assert any("robots.txt" in w for w in result.warnings)


def test_a_site_whose_robots_txt_disallows_everything_cannot_be_crawled():
    with pytest.raises(SiteUnreachable, match="robots.txt does not allow"):
        crawl(Site(acme_pages(), robots="User-agent: *\nDisallow: /\n"))


def test_the_sitemaps_robots_txt_lists_are_read():
    pages = acme_pages()
    pages["/maps/pages.xml"] = fx("sitemap.xml").replace("/history", "/history-from-robots-sitemap")
    pages["/history-from-robots-sitemap"] = fx("history.html")
    site = Site(pages, robots="User-agent: *\nSitemap: https://acme.example/maps/pages.xml\n")
    crawl(site)
    assert "/maps/pages.xml" in site.paths
    assert "/sitemap.xml" in site.paths  # the default location is tried as well


def test_sitemap_pages_with_a_section_word_are_queued_and_the_rest_are_not():
    site = Site(acme_pages())
    result = crawl(site)
    assert "/blog/our-first-terminal" not in site.paths and "/blog/office-party-photos" not in site.paths
    assert ("other.example", "/investors") not in site.requests
    assert "https://acme.example/history" in urls(result)


# ------------------------------------------------------------------ what is skipped


def test_login_search_cart_files_job_ads_and_query_variants_are_not_read():
    site = Site(acme_pages())
    result = crawl(site)
    requested = set(site.paths)
    assert not {"/login", "/cart", "/files/brochure.zip", "/careers/senior-engineer", "/privacy"} & requested
    assert not [p for p in requested if p.startswith("/search")]
    assert "/products?sort=asc" not in requested
    assert "/careers" in requested  # the careers index is read
    assert result.stats.skipped_filtered > 0


def test_a_page_with_a_password_field_is_dropped_even_when_its_address_looks_fine():
    result = crawl(Site(acme_pages()))
    assert not any(p.url.endswith("/members") for p in result.pages)


def test_query_string_copies_of_a_page_are_read_once():
    site = Site(acme_pages())
    result = crawl(site)
    assert sum(1 for p in site.paths if p.split("?")[0] == "/about") == 1
    assert sum(1 for p in paths(result) if p.split("?")[0] == "/about") == 1


def test_a_page_that_is_not_html_is_not_read():
    pages = acme_pages()
    pages["/about"] = (200, "%PDF-1.4", "application/pdf")
    result = crawl(Site(pages))
    assert "/about" not in paths(result)
    assert result.stats.failed >= 1


# ------------------------------------------------------------------ English and Arabic


def test_the_english_page_is_kept_with_the_arabic_about_page_only():
    site = Site(acme_pages())
    result = crawl(site)
    found = paths(result)
    assert "/team" in found and "/about" in found
    assert "/ar/about" in found  # the Arabic about page
    assert "/ar/team" not in found and "/ar/team" not in site.paths
    assert result.stats.skipped_language >= 1


def test_an_arabic_page_queued_before_its_english_copy_is_dropped_when_the_copy_turns_up():
    def page(links):
        return '<html lang="en"><body><nav>' + links + "</nav><main><p>" + "word " * 120 + "</p></main></body></html>"

    pages = {
        "/": page('<a href="/other">Other</a><a href="/ar/misc">عربي</a>'),
        "/other": page('<a href="/misc">Misc</a>'),
        "/misc": page(""),
        "/ar/misc": page(""),
    }
    site = Site(pages)
    # one page at a time: /ar/misc is queued, then /other is read and shows that /misc exists
    result = crawl(site, limits=limits(concurrency=1))
    assert "/misc" in paths(result)
    assert "/ar/misc" not in paths(result) and "/ar/misc" not in site.paths
    assert result.stats.skipped_language == 1


def test_a_site_in_arabic_only_keeps_its_pages():
    pages = {
        "/": '<html lang="ar"><body><nav><a href="/ar/team">فريق العمل</a><a href="/ar/history">تاريخنا</a></nav><main><p>' + "كلمة " * 120 + "</p></main></body></html>",
        "/ar/team": fx("ar_team.html"),
        "/ar/history": fx("ar_team.html"),
    }
    result = crawl(Site(pages))
    assert {"/ar/team", "/ar/history"} <= set(paths(result))
    assert result.stats.skipped_language == 0


# ------------------------------------------------------------------ priority


def test_pages_with_section_words_are_read_before_pages_without_them():
    links = "".join(f'<a href="/misc/page-{i}">Misc {i}</a>' for i in range(10))
    home = '<html lang="en"><body><main><p>' + "word " * 120 + "</p>" + links + '</main><footer><a href="/investors">x</a></footer></body></html>'
    pages = {"/": home, "/investors": fx("investors.html"), "/investors/reports": fx("reports.html"), "/team": fx("team.html")}
    pages.update({f"/misc/page-{i}": fx("history.html") for i in range(10)})
    pages["/sitemap.xml"] = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://acme.example/team</loc></url></urlset>'
    result = crawl(Site(pages), limits=limits(max_pages=4, concurrency=1))
    assert paths(result)[0] == "/"
    assert set(paths(result)[1:]) == {"/investors", "/team", "/investors/reports"}  # the scored pages, not the misc ones


def test_a_nearer_page_is_read_before_a_farther_one_with_the_same_score():
    site = Site(acme_pages())
    result = crawl(site, limits=limits(concurrency=1))
    order = {p: i for i, p in enumerate(paths(result))}
    assert order["/team"] < order["/team/leadership"] < order["/team/leadership/board"]


# ------------------------------------------------------------------ limits


def test_the_page_limit_stops_the_crawl_and_says_how_many_were_left():
    result = crawl(Site(acme_pages()), limits=limits(max_pages=3))
    assert len(result.pages) == 3 and result.stats.pages_crawled == 3
    assert result.stats.stopped is StopReason.MAX_PAGES
    assert any("stopped at 3 pages" in w for w in result.warnings)


def test_a_crawl_that_runs_out_of_pages_to_read_is_complete():
    result = crawl(Site({"/": fx("home.html"), "/about": fx("about.html")}))
    assert result.stats.stopped is StopReason.COMPLETE and not any("stopped at" in w for w in result.warnings)


def test_the_depth_limit_stops_link_following():
    shallow = crawl(Site(acme_pages()), limits=limits(max_depth=1))
    assert max(p.depth for p in shallow.pages) == 1
    assert "/team/leadership" not in paths(shallow)
    assert shallow.stats.skipped_depth > 0

    default = crawl(Site(acme_pages()), limits=limits(max_depth=3))
    assert "/team/leadership/board" in paths(default)  # three links from the homepage
    assert "/team/leadership/board/archive/2019" not in paths(default)  # four


def test_depth_zero_reads_only_the_homepage():
    result = crawl(Site(acme_pages()), limits=limits(max_depth=0))
    assert paths(result) == ["/"]


def test_the_time_budget_ends_the_crawl_between_batches():
    clock = Clock()
    site = Site(acme_pages(), on_request=lambda r: setattr(clock, "now", clock.now + 60) if r.url.path not in ("/robots.txt", "/sitemap.xml") else None)
    result = crawl(site, clock=clock, sleep=clock.sleep, limits=limits(time_budget_s=100))
    assert result.stats.stopped is StopReason.TIME_BUDGET
    assert 1 < len(result.pages) < 20
    assert any("time budget" in w for w in result.warnings)
    assert result.stats.elapsed_s >= 100


def test_the_time_budget_also_bounds_the_browser():
    browser = FakeBrowser({HOME: fx("resources_rendered.html")}, delay=30)
    site = Site({"/": fx("resources_spa.html"), "/sitemap.xml": fx("sitemap.xml")})
    result = crawl(site, browser=browser, limits=limits(dynamic=True, time_budget_s=0.3))
    assert browser.rendered == [HOME]
    assert result.stats.elapsed_s < 5  # the thirty-second render was cut short
    assert result.pages[0].fetch_mode is FetchMode.STATIC  # the static reading is kept


def test_the_response_size_cap_cuts_a_page_and_says_so():
    result = crawl(Site(acme_pages()), fetcher_args={"max_bytes": 600})
    assert result.stats.truncated > 0
    assert any("size limit" in w for w in result.warnings)


def test_requests_to_one_host_are_spaced_by_the_configured_delay():
    clock = Clock()
    site = Site(acme_pages())
    result = crawl(site, clock=clock, sleep=clock.sleep, limits=limits(host_delay_s=1.0, max_pages=6))
    assert len(result.pages) == 6
    requests = sum(1 for host, path in site.requests if host == "acme.example" and path != "/robots.txt")
    assert requests > 6
    assert clock.now - 1000.0 >= requests - 1 - 1e-6  # one second between each request to the host
    assert all(s > 0 for s in clock.sleeps)


def test_the_throttle_queues_requests_to_the_same_host_and_not_across_hosts():
    clock = Clock()
    throttle = HostThrottle(2.0, clock, clock.sleep)

    async def go():
        await asyncio.gather(throttle.wait("a.example"), throttle.wait("a.example"), throttle.wait("a.example"))
        spent = clock.now
        await throttle.wait("b.example")
        return spent

    spent = asyncio.run(go())
    assert spent == 1000.0 + 4.0 and len(clock.sleeps) == 2  # the first request goes at once, the others at two-second spacing
    assert clock.now == spent  # another host is not held back


def test_a_zero_delay_never_sleeps():
    clock = Clock()
    asyncio.run(HostThrottle(0, clock, clock.sleep).wait("a.example"))
    assert clock.sleeps == []


def test_the_default_limits_come_from_config(monkeypatch):
    from edrak.company_research.crawler import CrawlLimits
    from edrak.core.config import settings

    defaults = CrawlLimits.from_settings()
    assert (defaults.max_pages, defaults.max_depth, defaults.time_budget_s, defaults.concurrency, defaults.host_delay_s) == (40, 3, 180.0, 3, 1.0)
    monkeypatch.setattr(settings, "COMPANY_RESEARCH_CRAWL_MAX_PAGES", 7)
    monkeypatch.setattr(settings, "COMPANY_RESEARCH_CRAWL_CONCURRENCY", 0)
    changed = CrawlLimits.from_settings()
    assert changed.max_pages == 7 and changed.concurrency == 1  # never below one


# ------------------------------------------------------------------ the browser


SPA = "https://acme.example/resources"


def spa_site(**overrides):
    pages = {"/": fx("home.html"), "/resources": fx("resources_spa.html"), "/sitemap.xml": fx("sitemap.xml")}
    pages.update(overrides)
    return Site(pages)


def test_a_page_with_almost_no_static_text_is_read_in_the_browser():
    browser = FakeBrowser({SPA: fx("resources_rendered.html")}, scrolls=3, clicks=2, expands=4)
    result = crawl(spa_site(), browser=browser, limits=limits(dynamic=True))
    page = next(p for p in result.pages if p.url == SPA)
    assert page.fetch_mode is FetchMode.DYNAMIC
    assert "Resources" in page.text and len(page.text) > 300
    assert "less than 300" in page.render_reason
    assert browser.rendered == [SPA]
    assert (result.stats.browser_pages, result.stats.scrolls, result.stats.clicks, result.stats.expands) == (1, 3, 2, 4)
    assert result.stats.dynamic_pages == 1 and result.stats.static_pages == result.stats.pages_crawled - 1


def test_pdf_and_page_links_that_only_appear_after_the_browser_runs_are_found():
    static_only = crawl(spa_site(), browser=FakeBrowser(unavailable=True), limits=limits(dynamic=True))
    assert not [p for p in static_only.pdf_links if "esg-report" in p.url]

    browser = FakeBrowser({SPA: fx("resources_rendered.html")})
    result = crawl(spa_site(), browser=browser, limits=limits(dynamic=True))
    page = next(p for p in result.pages if p.url == SPA)
    assert {p.url for p in page.pdf_links} == {"https://acme.example/docs/esg-report.pdf", "https://acme.example/docs/factsheet.pdf"}
    assert {p.url for p in result.pdf_links} >= {p.url for p in page.pdf_links}
    assert "https://acme.example/sustainability" in [link.url for link in page.links]


def test_links_found_only_after_the_browser_runs_are_followed():
    pages = {"/": fx("resources_spa.html").replace('<div id="root"></div>', '<div id="root"></div>'), "/sustainability": fx("sustainability.html")}
    browser = FakeBrowser({HOME: fx("resources_rendered.html")})
    result = crawl(Site(pages), browser=browser, limits=limits(dynamic=True))
    assert result.pages[0].fetch_mode is FetchMode.DYNAMIC
    assert "/sustainability" in paths(result)


def test_a_page_with_enough_text_and_no_script_signs_never_opens_the_browser():
    browser = FakeBrowser()
    result = crawl(Site({"/": fx("home.html"), "/about": fx("about.html")}), browser=browser, limits=limits(dynamic=True))
    assert browser.opened == 0 and browser.rendered == []
    assert all(p.fetch_mode is FetchMode.STATIC for p in result.pages)


def test_lazy_loading_in_the_html_opens_the_browser_even_when_the_text_is_long():
    browser = FakeBrowser({HOME: fx("resources_lazy_rendered.html")}, clicks=1)
    result = crawl(Site({"/": fx("resources_lazy.html")}), browser=browser, limits=limits(dynamic=True))
    page = result.pages[0]
    assert browser.rendered == [HOME] and page.render_reason == "lazy loading"
    assert page.fetch_mode is FetchMode.DYNAMIC  # it found a link the static page lacked
    assert {p.url for p in page.pdf_links} == {"https://acme.example/docs/brochure.pdf", "https://acme.example/docs/price-list.pdf"}


def test_a_browser_reading_that_adds_nothing_leaves_the_page_static():
    browser = FakeBrowser({HOME: fx("resources_lazy.html")})
    result = crawl(Site({"/": fx("resources_lazy.html")}), browser=browser, limits=limits(dynamic=True))
    assert browser.rendered == [HOME] and result.pages[0].fetch_mode is FetchMode.STATIC
    assert result.pages[0].render_reason == ""


def test_dynamic_reading_can_be_turned_off():
    browser = FakeBrowser({SPA: fx("resources_rendered.html")})
    result = crawl(spa_site(), browser=browser, limits=limits(dynamic=False))
    assert browser.opened == 0
    assert next(p for p in result.pages if p.url == SPA).fetch_mode is FetchMode.STATIC


def test_the_number_of_pages_opened_in_the_browser_is_capped():
    thin = fx("resources_spa.html")
    links = "".join(f'<a href="/team/p{i}">Team {i}</a>' for i in range(5))
    home = '<html lang="en"><body><nav>' + links + '</nav><div id="root"></div></body></html>'
    pages = {"/": home, **{f"/team/p{i}": thin for i in range(5)}}
    browser = FakeBrowser({u: thin for u in [HOME] + [f"https://acme.example/team/p{i}" for i in range(5)]})
    result = crawl(Site(pages), browser=browser, limits=limits(dynamic=True, max_dynamic_pages=3))
    assert len(browser.rendered) == 3
    assert result.stats.pages_crawled == 6


def test_without_a_browser_the_static_text_is_kept_and_one_warning_is_recorded():
    pages = {"/": fx("resources_spa.html"), "/team": fx("resources_spa.html"), "/investors": fx("resources_spa.html")}
    pages["/"] = pages["/"].replace("<div id=\"root\"></div>", '<div id="root"></div><a href="/team">Team</a><a href="/investors">Investors</a>')
    browser = FakeBrowser(unavailable=True)
    result = crawl(Site(pages), browser=browser, limits=limits(dynamic=True))
    assert result.stats.pages_crawled == 3
    assert result.warnings.count(BROWSER_UNAVAILABLE_WARNING) == 1
    assert all(p.fetch_mode is FetchMode.STATIC for p in result.pages)


def test_the_default_browser_is_playwright_and_its_absence_is_only_a_warning(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    result = crawl(Site({"/": fx("resources_spa.html")}), limits=limits(dynamic=True))
    assert result.pages[0].fetch_mode is FetchMode.STATIC
    assert BROWSER_UNAVAILABLE_WARNING in result.warnings


def test_a_browser_that_fails_on_a_page_leaves_the_static_reading():
    class Failing(FakeBrowser):
        async def render(self, url, in_scope):
            raise RuntimeError("page crashed")

    result = crawl(Site({"/": fx("resources_spa.html")}), browser=Failing(), limits=limits(dynamic=True))
    assert result.pages[0].fetch_mode is FetchMode.STATIC and result.stats.browser_pages == 0


def test_the_browser_is_opened_once_and_closed_after_the_crawl():
    closed = []
    browser = FakeBrowser({SPA: fx("resources_rendered.html"), HOME: fx("resources_rendered.html")})
    inner = browser.__call__

    def factory():
        manager = inner()

        class Wrapped:
            async def __aenter__(self):
                return await manager.__aenter__()

            async def __aexit__(self, *exc):
                closed.append(True)
                return await manager.__aexit__(*exc)

        return Wrapped()

    crawl(spa_site(), browser=factory, limits=limits(dynamic=True))
    assert browser.opened == 1 and closed == [True]


# ------------------------------------------------------------------ the entry point


def test_an_unreachable_homepage_raises():
    with pytest.raises(SiteUnreachable):
        crawl(Site({}))


def test_a_bare_domain_is_accepted():
    result = crawl(Site(acme_pages()), "acme.example")
    assert result.pages[0].url == HOME
