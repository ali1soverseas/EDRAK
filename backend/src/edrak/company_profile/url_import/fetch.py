"""Fetching pages of one company website, safely and politely.

Every request, including each redirect hop, is checked against the non-public address rules before
it is sent. Redirects are followed here, not by the HTTP client, so that is possible. Responses are
capped in size and limited to the content types the caller asks for, and robots.txt is honoured.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from edrak.agents.competitor_intelligence.socials import USER_AGENT
from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked
from edrak.company_profile.url_import.urls import Resolver, check_address, ensure_public
from edrak.core.config import settings

# The product token robots.txt rules are matched against.
ROBOTS_AGENT = "EdrakBot"
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
SITEMAP_TYPES = frozenset({"application/xml", "text/xml", "text/plain", "application/xhtml+xml", "text/html"})
ANY_TYPE: frozenset[str] = frozenset()  # an empty set means no restriction

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)


@dataclass
class Fetched:
    url: str  # where the body came from, after redirects
    status: int
    content_type: str
    body: bytes
    truncated: bool = False

    def text(self) -> str:
        """The body as text, using the charset the header or a meta tag names, else UTF-8."""
        return decode_body(self.body, self.content_type)


def decode_body(body: bytes, content_type: str = "") -> str:
    charset = None
    header = re.search(r"charset\s*=\s*([A-Za-z0-9_\-]+)", content_type or "", re.IGNORECASE)
    if header:
        charset = header.group(1)
    else:
        meta = _META_CHARSET.search(body[:4096])
        if meta:
            charset = meta.group(1).decode("ascii", "ignore")
    for encoding in (charset, "utf-8"):
        if not encoding:
            continue
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


class SiteFetcher:
    """Async fetcher with the limits from config. Use as `async with SiteFetcher() as fetcher`."""

    def __init__(
        self,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        resolver: Optional[Resolver] = None,
    ) -> None:
        self._transport = transport
        self._resolver = resolver
        self._client: Optional[httpx.AsyncClient] = None
        self._slots = asyncio.Semaphore(max(1, settings.PROFILE_IMPORT_CONCURRENCY))
        self._robots: dict[str, Optional[RobotFileParser]] = {}

    async def __aenter__(self) -> "SiteFetcher":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.PROFILE_IMPORT_FETCH_TIMEOUT_S),
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en,ar;q=0.8"},
            transport=self._transport,
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def check(self, url: str) -> None:
        """Raise UrlBlocked if `url` is not public, or SiteUnreachable if its host does not resolve.

        Called before anything else, so a refused address is reported as such and not as a
        robots.txt that could not be read.
        """
        await ensure_public(url, self._resolver)

    async def get(
        self, url: str, *, kinds: frozenset[str] = HTML_TYPES, raise_for_status: bool = True
    ) -> Fetched:
        """GET `url`, following at most PROFILE_IMPORT_MAX_REDIRECTS redirects.

        Raises UrlBlocked when the URL or a redirect target is not public, and SiteUnreachable
        when the site cannot be read. `kinds` are the content types accepted (none given = any).
        """
        assert self._client is not None, "use SiteFetcher as an async context manager"
        current = url
        for _ in range(settings.PROFILE_IMPORT_MAX_REDIRECTS + 1):
            await ensure_public(current, self._resolver)
            async with self._slots:
                response = await self._once(current, kinds, raise_for_status)
            if isinstance(response, str):  # a redirect target
                current = response
                continue
            return response
        raise SiteUnreachable("The website redirected too many times.")

    async def _once(self, url: str, kinds: frozenset[str], raise_for_status: bool) -> Fetched | str:
        assert self._client is not None
        try:
            async with self._client.stream("GET", url) as response:
                _check_peer(response)
                status = response.status_code
                if status in _REDIRECTS:
                    target = response.headers.get("location")
                    if not target:
                        raise SiteUnreachable("The website sent a redirect with no destination.")
                    return urljoin(url, target)
                kind = response.headers.get("content-type", "text/html").split(";")[0].strip().lower()
                if raise_for_status and status >= 400:
                    raise SiteUnreachable(f"The website answered with an error (HTTP {status}).")
                if status < 400 and kinds and kind not in kinds:
                    raise SiteUnreachable(f"The address did not return a web page ({kind or 'unknown type'}).")
                body = bytearray()
                limit = settings.PROFILE_IMPORT_MAX_BYTES
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        break
                truncated = len(body) > limit
                return Fetched(
                    url=str(response.url),
                    status=status,
                    content_type=response.headers.get("content-type", ""),
                    body=bytes(body[:limit]),
                    truncated=truncated,
                )
        except httpx.TimeoutException:
            raise SiteUnreachable("The website took too long to answer.") from None
        except httpx.UnsupportedProtocol:
            raise UrlBlocked("Only http and https web addresses can be imported.") from None
        except httpx.HTTPError:
            raise SiteUnreachable("The website could not be reached.") from None

    # ------------------------------------------------------------------ robots.txt

    async def _rules(self, origin: str) -> Optional[RobotFileParser]:
        """Parsed rules; None when the site has none. A robots.txt that cannot be read, or that
        fails with a server error, counts as disallowing everything, as RFC 9309 asks."""
        if origin not in self._robots:
            self._robots[origin] = await self._read_robots(origin)
        return self._robots[origin]

    async def _read_robots(self, origin: str) -> Optional[RobotFileParser]:
        try:
            fetched = await self.get(f"{origin}/robots.txt", kinds=ANY_TYPE, raise_for_status=False)
        except (SiteUnreachable, UrlBlocked):
            return _disallow_all()
        if 400 <= fetched.status < 500:
            return None
        if fetched.status >= 500:
            return _disallow_all()
        rules = RobotFileParser()
        rules.parse(fetched.text().splitlines())
        return rules

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        rules = await self._rules(f"{parts.scheme}://{parts.netloc}")
        return rules is None or rules.can_fetch(ROBOTS_AGENT, url)

    async def sitemaps(self, origin: str) -> list[str]:
        """Sitemap URLs the site's robots.txt lists."""
        rules = await self._rules(origin)
        return list(rules.site_maps() or []) if rules is not None else []


def _disallow_all() -> RobotFileParser:
    rules = RobotFileParser()
    rules.parse(["User-agent: *", "Disallow: /"])
    return rules


def _check_peer(response: httpx.Response) -> None:
    """Refuse a response from a non-public address, whatever the name resolved to when checked.

    Covers a name that points somewhere else by the time the connection is made. Not every
    transport reports the peer, so a response without one passes.
    """
    stream = response.extensions.get("network_stream")
    peer = stream.get_extra_info("server_addr") if stream is not None else None
    if peer:
        check_address(str(peer[0]))
