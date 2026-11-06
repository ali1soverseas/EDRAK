import asyncio

import httpx
import pytest

from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked
from edrak.company_profile.url_import.fetch import SiteFetcher
from edrak.company_profile.url_import.urls import ensure_public, normalize_url, registered_domain, same_site
from edrak.core.config import settings


def run(coro):
    return asyncio.run(coro)


async def public(host, port):
    return ["93.184.216.34"]


def fetcher(handler, resolver=public):
    return SiteFetcher(transport=httpx.MockTransport(handler), resolver=resolver)


# ------------------------------------------------------------------ normalization


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("example.com", "https://example.com/"),
        ("  Example.COM/About  ", "https://example.com/About"),
        ("http://example.com", "http://example.com/"),
        ("https://example.com:443/x", "https://example.com/x"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        ("example.com:8080", "https://example.com:8080/"),
        ("https://example.com/p?utm_source=a&id=3&fbclid=z&gclid=q#top", "https://example.com/p?id=3"),
        ("https://example.com/?utm_medium=x", "https://example.com/"),
        ("//example.com/a", "https://example.com/a"),
        ("https://مصر.example/", "https://xn--wgbh1c.example/"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "ftp://example.com", "file:///etc/passwd", "javascript:alert(1)", "mailto:a@b.com",
     "https://user:pw@example.com", "https://", "http://exa mple.com", "https://example.com:99999", "a" * 3000],
)
def test_normalize_rejects_bad_input(raw):
    with pytest.raises(UrlBlocked):
        normalize_url(raw)


def test_registered_domain_and_same_site():
    assert registered_domain("www.example.com") == "example.com"
    assert registered_domain("shop.example.com.eg") == "example.com.eg"
    assert registered_domain("example.co.uk") == "example.co.uk"
    assert registered_domain("10.1.2.3") == "10.1.2.3"
    assert same_site("https://en.example.com/a", "https://ar.example.com/b")
    assert not same_site("https://example.com/", "https://example.org/")
    assert not same_site("https://a.com.eg/", "https://b.com.eg/")


# ------------------------------------------------------------------ SSRF


@pytest.mark.parametrize(
    "url",
    ["http://localhost/", "http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/",
     "http://169.254.169.254/latest/meta-data", "http://[::1]/", "http://[fd00::1]/", "http://[::ffff:10.0.0.1]/",
     "http://0.0.0.0/", "http://100.64.0.1/", "http://intranet/", "http://printer.local/", "http://2130706433/"],
)
def test_non_public_hosts_are_blocked_before_any_lookup(url):
    async def never(host, port):
        raise AssertionError("should not resolve")

    with pytest.raises(UrlBlocked):
        run(ensure_public(url, never))


def test_a_public_name_that_resolves_to_a_private_address_is_blocked():
    async def private(host, port):
        return ["93.184.216.34", "10.0.0.7"]  # one bad answer is enough

    with pytest.raises(UrlBlocked, match="private"):
        run(ensure_public("https://sneaky.example/", private))


def test_a_name_that_does_not_resolve_is_unreachable_not_blocked():
    async def nothing(host, port):
        return []

    with pytest.raises(SiteUnreachable):
        run(ensure_public("https://gone.example/", nothing))


def test_a_redirect_to_a_private_ip_is_blocked_and_never_requested():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://10.0.0.5/admin"})

    async def go():
        async with fetcher(handler) as site:
            await site.get("https://example.com/")

    with pytest.raises(UrlBlocked):
        run(go())
    assert seen == ["https://example.com/"]


def test_a_redirect_to_a_name_that_resolves_privately_is_blocked():
    async def resolver(host, port):
        return ["10.1.1.1"] if host == "internal.example" else ["93.184.216.34"]

    def handler(request):
        return httpx.Response(301, headers={"location": "https://internal.example/"})

    async def go():
        async with fetcher(handler, resolver) as site:
            await site.get("https://example.com/")

    with pytest.raises(UrlBlocked):
        run(go())


def test_a_redirect_to_a_non_http_scheme_is_blocked():
    def handler(request):
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    async def go():
        async with fetcher(handler) as site:
            await site.get("https://example.com/")

    with pytest.raises(UrlBlocked):
        run(go())


def test_redirects_are_followed_up_to_the_limit(monkeypatch):
    monkeypatch.setattr(settings, "PROFILE_IMPORT_MAX_REDIRECTS", 5)
    hops = []

    def handler(request):
        hops.append(request.url.path)
        n = len(hops)
        if n <= 5:
            return httpx.Response(302, headers={"location": f"/hop{n}"})
        return httpx.Response(200, html="<html>ok</html>")

    async def go():
        async with fetcher(handler) as site:
            return await site.get("https://example.com/")

    assert run(go()).url.endswith("/hop5")  # five redirects are allowed

    hops.clear()

    def endless(request):
        hops.append(request.url.path)
        return httpx.Response(302, headers={"location": f"/hop{len(hops)}"})

    async def go_forever():
        async with fetcher(endless) as site:
            await site.get("https://example.com/")

    with pytest.raises(SiteUnreachable, match="redirected too many times"):
        run(go_forever())
    assert len(hops) == 6  # the first request and five redirects, then it stops


def test_a_response_from_a_private_peer_is_refused():
    class Stream:
        def get_extra_info(self, name):
            return ("10.9.9.9", 443) if name == "server_addr" else None

    def handler(request):
        return httpx.Response(200, html="<html>secret</html>", extensions={"network_stream": Stream()})

    async def go():
        async with fetcher(handler) as site:
            await site.get("https://example.com/")

    with pytest.raises(UrlBlocked):
        run(go())


# ------------------------------------------------------------------ fetching limits


def test_only_html_is_accepted_and_the_size_is_capped(monkeypatch):
    monkeypatch.setattr(settings, "PROFILE_IMPORT_MAX_BYTES", 1000)

    def handler(request):
        if request.url.path == "/file.pdf":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        if request.url.path == "/missing":
            return httpx.Response(404, html="nope")
        return httpx.Response(200, content=b"<p>" + b"x" * 5000, headers={"content-type": "text/html; charset=utf-8"})

    async def go():
        async with fetcher(handler) as site:
            big = await site.get("https://example.com/")
            with pytest.raises(SiteUnreachable, match="web page"):
                await site.get("https://example.com/file.pdf")
            with pytest.raises(SiteUnreachable, match="HTTP 404"):
                await site.get("https://example.com/missing")
            return big

    big = run(go())
    assert len(big.body) == 1000 and big.truncated


def test_timeouts_and_connection_errors_become_unreachable():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    for handler in (slow, refused):
        async def go():
            async with fetcher(handler) as site:
                await site.get("https://example.com/")

        with pytest.raises(SiteUnreachable):
            run(go())


def test_charset_comes_from_the_header_or_a_meta_tag():
    text = "مرحبا بكم"

    def handler(request):
        if request.url.path == "/header":
            return httpx.Response(200, content=text.encode("windows-1256"), headers={"content-type": "text/html; charset=windows-1256"})
        body = b'<html><head><meta charset="windows-1256"></head><body>' + text.encode("windows-1256")
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    async def go():
        async with fetcher(handler) as site:
            return (await site.get("https://example.com/header")).text(), (await site.get("https://example.com/meta")).text()

    header_text, meta_text = run(go())
    assert header_text == text and text in meta_text


# ------------------------------------------------------------------ robots.txt


def test_robots_rules_are_honoured_and_sitemaps_listed():
    robots = "User-agent: *\nDisallow: /private\nSitemap: https://example.com/map.xml\n\nUser-agent: EdrakBot\nDisallow: /bot-only\n"

    def handler(request):
        return httpx.Response(200, text=robots, headers={"content-type": "text/plain"})

    async def go():
        async with fetcher(handler) as site:
            return (
                await site.allowed("https://example.com/about"),
                await site.allowed("https://example.com/bot-only/x"),
                await site.allowed("https://example.com/private"),
                await site.sitemaps("https://example.com"),
            )

    about, bot_only, private, maps = run(go())
    assert about and not bot_only
    assert private  # a group naming EdrakBot replaces the * group for this agent
    assert maps == ["https://example.com/map.xml"]


def test_a_missing_robots_file_allows_everything_and_a_broken_one_allows_nothing():
    async def check(status):
        def handler(request):
            return httpx.Response(status, text="")

        async with fetcher(handler) as site:
            return await site.allowed("https://example.com/about")

    assert run(check(404)) is True
    assert run(check(503)) is False

    def unreachable(request):
        raise httpx.ConnectError("down", request=request)

    async def down():
        async with fetcher(unreachable) as site:
            return await site.allowed("https://example.com/about")

    assert run(down()) is False
