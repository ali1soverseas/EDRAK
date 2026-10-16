"""Fetch a public web page and extract its main text, honoring robots.txt."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

from edrak.agents.customer_trends.providers.base import (
    CallParams,
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderResult,
    ProviderUnavailable,
    canonical_url,
    new_evidence,
)
from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.http import USER_AGENT
from edrak.agents.customer_trends.schemas.common import SourceType
from edrak.agents.customer_trends.utils.text import truncate

NAME = "direct_http"
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_TIMEOUT_S = 20.0
MIN_TEXT_CHARS = 30
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_ACCEPT = "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1"


def _disallow_everything() -> RobotFileParser:
    rules = RobotFileParser()
    rules.parse(["User-agent: *", "Disallow: /"])
    return rules


def _empty(warning: str, **meta: Any) -> ProviderResult:
    return ProviderResult(warnings=[warning], meta=meta)


class DirectHttpProvider:
    """The `fetch_page` capability. A page that cannot be read is an empty result with a
    warning, not a failure, so refusals never count against the provider's circuit breaker."""

    name = NAME
    capabilities = {"fetch_page"}

    def __init__(
        self,
        client: httpx.AsyncClient,
        config: ProviderConfig,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._clock = clock
        self._max_bytes = int(config.options.get("max_bytes", DEFAULT_MAX_BYTES))
        self._timeout_s = float(config.options.get("timeout_s", DEFAULT_TIMEOUT_S))
        self._robots: dict[str, RobotFileParser | None] = {}

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        call = CallParams.model_validate(params)
        if capability != "fetch_page" or not call.url:
            raise ProviderBadResponse(f"{NAME}: fetch_page needs url")
        if not await self._allowed(call.url):
            return _empty("robots.txt does not allow fetching this page", blocked="robots")
        fetched = await self._download(call.url)
        if isinstance(fetched, ProviderResult):
            return fetched
        text_body, final_url, plain = fetched
        extracted = (
            (_plain_text(text_body)) if plain else await asyncio.to_thread(self._extract, text_body)
        )
        if extracted is None:
            return _empty("no main text could be extracted from the page", blocked="no_text")
        title, body, details = extracted
        text = truncate(f"{title}\n{body}" if title else body, call.max_chars)
        item = new_evidence(
            call,
            provider=NAME,
            source_type=SourceType.WEB,
            text=text,
            url=final_url,
            key=f"fetch|{canonical_url(final_url)}",
            author=details.get("author"),
            published_at=_published(details.get("date")),
            snippet_only=False,
            metadata={
                "title": title or None,
                "site": details.get("sitename"),
                "chars_extracted": len(body),
                "truncated": len(body) > call.max_chars,
            },
            collected_at=self._clock(),
        )
        return ProviderResult(items=[item], raw_count=1)

    async def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = await self._read_robots(origin)
        rules = self._robots[origin]
        return rules is None or rules.can_fetch(USER_AGENT, url)

    async def _read_robots(self, origin: str) -> RobotFileParser | None:
        """Parsed rules, None when the site has none. An unreachable or broken robots.txt
        counts as disallowing everything, as RFC 9309 asks."""
        try:
            response = await self._client.get(
                f"{origin}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=self._timeout_s
            )
        except httpx.HTTPError:
            return _disallow_everything()
        if 400 <= response.status_code < 500:
            return None
        if response.status_code >= 500:
            return _disallow_everything()
        rules = RobotFileParser()
        rules.parse(response.text.splitlines())
        return rules

    async def _download(self, url: str) -> ProviderResult | tuple[str, str, bool]:
        headers = {"User-Agent": USER_AGENT, "Accept": _ACCEPT}
        try:
            async with self._client.stream(
                "GET", url, headers=headers, timeout=self._timeout_s
            ) as response:
                status = response.status_code
                if status == 429:
                    raise ProviderRateLimited(f"{NAME}: the site is rate limiting (HTTP 429)")
                if status >= 500:
                    raise ProviderUnavailable(f"{NAME}: the site failed (HTTP {status})")
                if status >= 400:
                    return _empty(f"the page answered HTTP {status}", blocked=f"http_{status}")
                kind = (
                    response.headers.get("content-type", "text/html").split(";")[0].strip().lower()
                )
                if kind not in _TEXT_TYPES:
                    return _empty(
                        f"not a text page ({kind or 'unknown type'})", blocked="content_type"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= self._max_bytes:
                        break
                encoding = response.charset_encoding or "utf-8"
                final_url = str(response.url)
        except httpx.TimeoutException:
            raise ProviderUnavailable(f"{NAME}: the page timed out") from None
        except httpx.TransportError as exc:
            raise ProviderUnavailable(f"{NAME}: connection failed ({type(exc).__name__})") from None
        decoded = bytes(body[: self._max_bytes]).decode(encoding, errors="replace")
        return decoded, final_url, kind == "text/plain"

    @staticmethod
    def _extract(html: str) -> tuple[str, str, dict[str, Any]] | None:
        body = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not body or len(body.strip()) < MIN_TEXT_CHARS:
            return None
        meta = trafilatura.extract_metadata(html)
        details = meta.as_dict() if meta is not None else {}
        return str(details.get("title") or ""), body.strip(), details


def _plain_text(text: str) -> tuple[str, str, dict[str, Any]] | None:
    body = text.strip()
    return ("", body, {}) if len(body) >= MIN_TEXT_CHARS else None


def _published(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
