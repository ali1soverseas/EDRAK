"""Websites and social profiles printed or linked in a PDF."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlsplit

from edrak.agents.competitor_intelligence.socials import PLATFORM_HOSTS, platform_of
from edrak.company_profile.pdf_import.extract import PageText
from edrak.company_profile.url_import.parse import social_profiles

_SOCIAL_HOSTS = "|".join(re.escape(h) for hosts in PLATFORM_HOSTS.values() for h in hosts)
# A web address with a scheme or "www.", or a social profile written without either.
_URL = re.compile(
    rf"(?:https?://|www\.)[^\s<>\"'‎‏]+|(?<![\w@.])(?:[\w-]+\.)?(?:{_SOCIAL_HOSTS})/[^\s<>\"'‎‏]+",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}»›\"'"


def _clean(raw: str) -> str:
    url = raw.rstrip(_TRAILING)
    return url if url.lower().startswith(("http://", "https://")) else "https://" + url


def urls_in(pages: list[PageText]) -> list[str]:
    """Every web address in the pages: hyperlinks first, then addresses printed in the text."""
    found = [u for p in pages for u in p.links if u.lower().startswith(("http://", "https://"))]
    found += [_clean(m) for p in pages for m in _URL.findall(p.text)]
    return found


def links_from_pages(pages: list[PageText]) -> list[dict[str, str]]:
    """The company's website and social profiles, as the core's extra_links.

    Social profiles use the same detector as the website importer. The website is the host that appears
    most often among the other addresses (the first one seen wins a tie), so a single partner link
    does not outvote the company's own address.
    """
    urls = urls_in(pages)
    links = [{"platform": platform_of(u) or "", "url": u} for u in social_profiles(urls)]
    hosts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    written: dict[str, str] = {}  # the host as first printed, with or without www
    for position, url in enumerate(urls):
        host = (urlsplit(url).hostname or "").lower()
        key = host.removeprefix("www.")
        if not key or "." not in key or platform_of(url):
            continue
        hosts[key] += 1
        first_seen.setdefault(key, position)
        written.setdefault(key, host)
    if hosts:
        best = min(hosts, key=lambda h: (-hosts[h], first_seen[h]))
        links.insert(0, {"platform": "website", "url": f"https://{written[best]}"})
    return links
