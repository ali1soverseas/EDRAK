"""Headless-browser rendering for sites whose static HTML has almost no text.

Playwright is optional. When it or its Chromium build is missing, RendererUnavailable is raised and
the import carries on with what it could read without a browser. Every request the browser makes,
including redirects and scripts, goes through the same public-address check as the plain fetcher.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from edrak.agents.competitor_intelligence.socials import USER_AGENT
from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked
from edrak.company_profile.url_import.urls import Resolver, ensure_public
from edrak.core.config import settings

logger = logging.getLogger(__name__)

# Takes URLs, returns the rendered HTML of each that could be rendered.
Renderer = Callable[[list[str]], Awaitable[dict[str, str]]]

_SKIPPED_TYPES = frozenset({"image", "media", "font"})
_LOCAL_SCHEMES = ("data:", "blob:", "about:")


class RendererUnavailable(Exception):
    """Playwright or its Chromium build is not installed."""


def make_route_guard(resolver: Optional[Resolver] = None) -> Callable[[Any], Awaitable[None]]:
    """A Playwright route handler that drops images, fonts and media and any request to a non-public address."""

    async def guard(route: Any) -> None:
        request = route.request
        if request.resource_type in _SKIPPED_TYPES:
            await route.abort()
            return
        if not request.url.lower().startswith(_LOCAL_SCHEMES):
            try:
                await ensure_public(request.url, resolver)
            except (UrlBlocked, SiteUnreachable):
                await route.abort()
                return
        await route.continue_()

    return guard


async def render_pages(urls: list[str]) -> dict[str, str]:
    """Render `urls` in headless Chromium and return the HTML of each page that loaded."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RendererUnavailable("Playwright is not installed.") from exc

    timeout_ms = int(settings.PROFILE_IMPORT_FETCH_TIMEOUT_S * 1000)
    rendered: dict[str, str] = {}
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as exc:  # Playwright raises its own error type when the browser is missing
            raise RendererUnavailable("Chromium is not installed (run: playwright install chromium).") from exc
        try:
            context = await browser.new_context(user_agent=USER_AGENT)
            await context.route("**/*", make_route_guard())
            for url in urls:
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="load", timeout=timeout_ms)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
                    except Exception:  # a page that never goes idle is still read as it stands
                        pass
                    rendered[url] = await page.content()
                except Exception as exc:
                    logger.info("Could not render %s: %s", url, exc)
                finally:
                    await page.close()
        finally:
            await browser.close()
    return rendered
