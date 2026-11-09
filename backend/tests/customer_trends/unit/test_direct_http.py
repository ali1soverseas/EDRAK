from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderResult,
    ProviderUnavailable,
)
from edrak.agents.customer_trends.providers.config import load_providers_config
from edrak.agents.customer_trends.providers.direct_http import DirectHttpProvider
from edrak.agents.customer_trends.providers.http import USER_AGENT, make_client
from edrak.agents.customer_trends.schemas.common import SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from tests.customer_trends.factories import NOW, call_params

CONFIG = load_providers_config().providers["direct_http"]
ARABIC = "الدعم الفني بطيء جدا والأسعار مرتفعة لكن الميزات ممتازة"
PAGE = f"""<html lang="en"><head><title>GitLab Duo review</title>
<meta name="author" content="A. Writer">
<meta property="article:published_time" content="2026-09-12">
</head><body><nav>Home About Pricing</nav><article><h1>GitLab Duo review</h1>
<p>{"We tested the assistant on a large monorepo for two weeks. " * 8}</p>
<p>{ARABIC} {ARABIC} {ARABIC}</p></article><footer>Cookie settings</footer></body></html>"""


class Site:
    """A fake site: a handler by path, and a log of what was requested."""

    def __init__(
        self, pages: dict[str, httpx.Response | Callable[[httpx.Request], httpx.Response]]
    ) -> None:
        self.pages = pages
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = self.pages.get(request.url.path)
        if page is None:
            return httpx.Response(404)
        return page(request) if callable(page) else page

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def html(body: str = PAGE, **headers: str) -> httpx.Response:
    return httpx.Response(
        200, text=body, headers={"content-type": "text/html; charset=utf-8", **headers}
    )


async def fetch(
    site: Site, url: str = "https://blog.example.test/post", **params: Any
) -> ProviderResult:
    async with make_client(5, transport=httpx.MockTransport(site)) as client:
        provider = DirectHttpProvider(client, CONFIG, clock=lambda: NOW)
        return await provider.call("fetch_page", call_params(url=url, **params))


def evidence(result: ProviderResult) -> EvidenceItem:
    [item] = result.items
    assert isinstance(item, EvidenceItem)
    return item


async def test_the_main_text_is_extracted_without_page_furniture() -> None:
    site = Site({"/robots.txt": httpx.Response(404), "/post": html()})
    item = evidence(await fetch(site))
    assert item.source_type is SourceType.WEB and item.platform is None
    assert item.snippet_only is False
    assert item.provider == "direct_http"
    assert item.url == "https://blog.example.test/post"
    assert item.text.startswith("GitLab Duo review\n")
    assert "We tested the assistant on a large monorepo" in item.text
    assert ARABIC in item.text
    assert "Cookie settings" not in item.text and "Home About Pricing" not in item.text
    assert item.metadata["title"] == "GitLab Duo review"
    assert item.metadata["truncated"] is False
    assert item.author == "A Writer"
    assert item.published_at == datetime(2026, 9, 12, tzinfo=UTC)
    assert item.collected_at == NOW


async def test_the_request_identifies_the_worker() -> None:
    site = Site({"/robots.txt": httpx.Response(404), "/post": html()})
    await fetch(site)
    assert all(r.headers["user-agent"] == USER_AGENT for r in site.requests)
    assert "text/html" in site.requests[-1].headers["accept"]


async def test_the_text_is_cut_at_max_chars_and_marked_truncated() -> None:
    site = Site({"/robots.txt": httpx.Response(404), "/post": html()})
    item = evidence(await fetch(site, max_chars=300))
    assert len(item.text) <= 300 and item.text.endswith("…")
    assert item.metadata["truncated"] is True


@pytest.mark.parametrize(
    "robots",
    [
        "User-agent: *\nDisallow: /",
        "User-agent: *\nDisallow: /post",
        f"User-agent: {USER_AGENT.split('/')[0]}\nDisallow: /",
    ],
)
async def test_robots_txt_that_disallows_the_page_blocks_the_fetch(robots: str) -> None:
    site = Site({"/robots.txt": httpx.Response(200, text=robots), "/post": html()})
    result = await fetch(site)
    assert result.items == []
    assert result.warnings == ["robots.txt does not allow fetching this page"]
    assert result.meta == {"blocked": "robots"}
    assert site.paths() == ["/robots.txt"]


async def test_robots_txt_that_allows_other_paths_does_not_block() -> None:
    site = Site(
        {
            "/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /private"),
            "/post": html(),
        }
    )
    assert evidence(await fetch(site)).text


@pytest.mark.parametrize("status", [404, 410, 403])
async def test_a_missing_or_forbidden_robots_txt_allows_fetching(status: int) -> None:
    site = Site({"/robots.txt": httpx.Response(status), "/post": html()})
    assert evidence(await fetch(site)).text


async def test_a_broken_robots_txt_disallows_everything() -> None:
    site = Site({"/robots.txt": httpx.Response(503), "/post": html()})
    result = await fetch(site)
    assert result.meta == {"blocked": "robots"} and site.paths() == ["/robots.txt"]


async def test_an_unreachable_robots_txt_disallows_everything() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    result = await fetch(Site({"/robots.txt": refuse, "/post": html()}))
    assert result.meta == {"blocked": "robots"}


async def test_robots_txt_is_read_once_per_site() -> None:
    site = Site({"/robots.txt": httpx.Response(404), "/a": html(), "/b": html()})
    async with make_client(5, transport=httpx.MockTransport(site)) as client:
        provider = DirectHttpProvider(client, CONFIG)
        await provider.call("fetch_page", call_params(url="https://blog.example.test/a"))
        await provider.call("fetch_page", call_params(url="https://blog.example.test/b"))
    assert site.paths().count("/robots.txt") == 1


@pytest.mark.parametrize(
    ("response", "warning", "blocked"),
    [
        (httpx.Response(404), "the page answered HTTP 404", "http_404"),
        (httpx.Response(403), "the page answered HTTP 403", "http_403"),
        (
            httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
            "not a text page (application/pdf)",
            "content_type",
        ),
        (
            html("<html><body><p>hi</p></body></html>"),
            "no main text could be extracted from the page",
            "no_text",
        ),
        (html(""), "no main text could be extracted from the page", "no_text"),
    ],
)
async def test_pages_that_cannot_be_read_are_empty_results_with_a_reason(
    response: httpx.Response, warning: str, blocked: str
) -> None:
    result = await fetch(Site({"/robots.txt": httpx.Response(404), "/post": response}))
    assert result.items == [] and result.warnings == [warning] and result.meta["blocked"] == blocked


async def test_rate_limits_and_server_errors_are_provider_errors() -> None:
    robots = httpx.Response(404)
    with pytest.raises(ProviderRateLimited):
        await fetch(Site({"/robots.txt": robots, "/post": httpx.Response(429)}))
    with pytest.raises(ProviderUnavailable, match="HTTP 502"):
        await fetch(Site({"/robots.txt": robots, "/post": httpx.Response(502)}))


async def test_timeouts_and_connection_errors_are_unavailable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderUnavailable, match="timed out"):
        await fetch(Site({"/robots.txt": httpx.Response(404), "/post": timeout}))

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderUnavailable, match="connection failed"):
        await fetch(Site({"/robots.txt": httpx.Response(404), "/post": refuse}))


async def test_the_download_is_capped_in_size() -> None:
    big = html("<html><body><article><p>" + "word " * 2_000_000 + "</p></article></body></html>")
    async with make_client(
        5, transport=httpx.MockTransport(Site({"/robots.txt": httpx.Response(404), "/post": big}))
    ) as client:
        provider = DirectHttpProvider(client, CONFIG)
        provider._max_bytes = 50_000
        result = await provider.call(
            "fetch_page", call_params(url="https://blog.example.test/post", max_chars=50000)
        )
    assert len(evidence(result).text) < 50_000


async def test_redirects_are_followed_and_the_final_url_is_kept() -> None:
    site = Site(
        {
            "/robots.txt": httpx.Response(404),
            "/old": httpx.Response(301, headers={"location": "https://blog.example.test/post"}),
            "/post": html(),
        }
    )
    item = evidence(await fetch(site, url="https://blog.example.test/old"))
    assert item.url == "https://blog.example.test/post"


async def test_the_page_id_differs_from_a_search_snippet_of_the_same_url() -> None:
    from edrak.agents.customer_trends.providers.base import CallParams, new_evidence

    site = Site({"/robots.txt": httpx.Response(404), "/post": html()})
    page = evidence(await fetch(site))
    snippet = new_evidence(
        CallParams.model_validate(call_params()),
        provider="serper",
        source_type=SourceType.WEB,
        text="GitLab Duo review. A short excerpt.",
        url="https://blog.example.test/post",
        snippet_only=True,
        collected_at=NOW,
    )
    assert page.id != snippet.id


async def test_plain_text_pages_are_accepted() -> None:
    text = "Release notes. " * 40
    site = Site(
        {
            "/robots.txt": httpx.Response(404),
            "/post": httpx.Response(200, text=text, headers={"content-type": "text/plain"}),
        }
    )
    assert "Release notes." in evidence(await fetch(site)).text


async def test_the_capability_needs_a_url() -> None:
    async with make_client(5) as client:
        with pytest.raises(ProviderBadResponse, match="needs url"):
            await DirectHttpProvider(client, CONFIG).call("fetch_page", call_params())
        with pytest.raises(ProviderBadResponse):
            await DirectHttpProvider(client, CONFIG).call(
                "web_search", call_params(url="https://a.test")
            )
