import asyncio

import httpx
import pytest

from edrak.company_profile.url_import.errors import SiteUnreachable
from edrak.company_profile.url_import.fetch import SiteFetcher


def fetcher(handler, **kwargs):
    async def public(host, port):
        return ["93.184.216.34"]

    return SiteFetcher(transport=httpx.MockTransport(handler), resolver=public, **kwargs)


def run(coro):
    return asyncio.run(coro)


def test_the_hook_runs_before_the_first_request_and_before_each_redirect_hop():
    sent, hooked = [], []

    def handler(request):
        sent.append(str(request.url))
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        return httpx.Response(200, text="<p>ok</p>", headers={"content-type": "text/html"})

    async def before(url):
        hooked.append((url, list(sent)))

    async def go():
        async with fetcher(handler) as site:
            return await site.get("https://acme.example/a", before_request=before)

    page = run(go())
    assert page.url == "https://acme.example/b"
    assert hooked == [("https://acme.example/a", []), ("https://acme.example/b", ["https://acme.example/a"])]


def test_a_hook_that_raises_stops_the_request_it_guards():
    sent = []

    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(302, headers={"location": "https://other.example/x"}) if request.url.path == "/a" else httpx.Response(200, text="x")

    async def before(url):
        if "other.example" in url:
            raise SiteUnreachable("off site")

    async def go():
        async with fetcher(handler) as site:
            await site.get("https://acme.example/a", before_request=before)

    with pytest.raises(SiteUnreachable, match="off site"):
        run(go())
    assert sent == ["/a"]


def test_the_size_cap_and_concurrency_can_be_set_per_fetcher():
    def handler(request):
        return httpx.Response(200, content=b"x" * 5000, headers={"content-type": "text/html"})

    async def go():
        async with fetcher(handler, max_bytes=1000, concurrency=1) as site:
            return await site.get("https://acme.example/")

    page = run(go())
    assert len(page.body) == 1000 and page.truncated
