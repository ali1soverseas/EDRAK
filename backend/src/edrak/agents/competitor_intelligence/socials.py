"""Find a company's official social accounts from its own website.

The only trusted source of "this is their X account" is the company's own site: its header,
footer or contact page links to it. Search results are not trusted for this, because look-alike
and fan accounts rank well. A link is kept when it points at a profile, not at a share button,
a post or a hashtag.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 10.0
USER_AGENT = "Mozilla/5.0 (compatible; EdrakBot/1.0; +https://edrak.app/bot)"
CONTACT_PATHS = ("/contact", "/contact-us", "/about", "/about-us")

PLATFORM_HOSTS: dict[str, tuple[str, ...]] = {
    "x": ("x.com", "twitter.com"),
    "facebook": ("facebook.com", "fb.com"),
    "instagram": ("instagram.com",),
    "linkedin": ("linkedin.com",),
    "youtube": ("youtube.com",),
    "tiktok": ("tiktok.com",),
}

# Paths that are not a profile: share buttons, posts, search, login, policy pages.
NOT_A_PROFILE = re.compile(
    r"^/(intent|share|sharer|sharer\.php|dialog|home|search|hashtag|explore|login|signup|i/|"
    r"watch|embed|results|p/|reel/|reels/|stories/|status/|posts/|privacy|legal|help|about/?$|"
    r"policies|terms|tr\b|plugins|groups/?$)",
    re.IGNORECASE,
)
_HREF = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)


def platform_of(url: str) -> Optional[str]:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.").removeprefix("m.")
    for platform, hosts in PLATFORM_HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return platform
    return None


def normalise_profile(url: str) -> Optional[str]:
    """A canonical profile URL, or None when the link is not a profile."""
    platform = platform_of(url)
    if not platform:
        return None
    parsed = urlparse(url)
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path in ("", "/") or NOT_A_PROFILE.match(path):
        return None
    parts = [p for p in path.split("/") if p]
    if platform == "linkedin":
        if len(parts) < 2 or parts[0] not in ("company", "school", "showcase", "in"):
            return None
        parts = parts[:2]
    elif platform == "youtube":
        if parts[0].startswith("@"):
            parts = parts[:1]
        elif parts[0] in ("channel", "c", "user") and len(parts) >= 2:
            parts = parts[:2]
        else:
            return None
    elif platform == "facebook" and parts[0] == "profile.php":
        return f"https://www.facebook.com/profile.php?{parsed.query}" if "id=" in parsed.query else None
    else:
        parts = parts[:1]
    handle = parts[-1].lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._\-]{2,80}", handle):
        return None
    host = {
        "x": "x.com", "facebook": "www.facebook.com", "instagram": "www.instagram.com",
        "linkedin": "www.linkedin.com", "youtube": "www.youtube.com", "tiktok": "www.tiktok.com",
    }[platform]
    return f"https://{host}/" + "/".join(parts)


def profiles_in_html(html: str, base_url: str) -> dict[str, str]:
    """platform -> profile URL, from the links of one page. The first profile per platform wins."""
    found: dict[str, str] = {}
    for href in _HREF.findall(html or ""):
        absolute = urljoin(base_url, href.strip())
        profile = normalise_profile(absolute)
        if not profile:
            continue
        platform = platform_of(profile)
        if platform and platform not in found:
            found[platform] = profile
    return found


def _fetch(client: httpx.Client, url: str) -> Optional[str]:
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        logger.info("Could not fetch %s: %s", url, exc)
        return None
    if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", "html"):
        return None
    return resp.text[:2_000_000]


def find_official_socials(website: str, *, client: Optional[httpx.Client] = None) -> list[dict[str, Any]]:
    """Official accounts linked from the homepage, then from a contact or about page if needed."""
    if not website:
        return []
    if "://" not in website:
        website = "https://" + website
    own = client or httpx.Client(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        found: dict[str, tuple[str, str]] = {}
        pages = [website] + [urljoin(website, p) for p in CONTACT_PATHS]
        for page in pages:
            html = _fetch(own, page)
            if html is None:
                continue
            for platform, url in profiles_in_html(html, page).items():
                found.setdefault(platform, (url, page))
            if len(found) >= 4:
                break
        return [
            {"platform": platform, "url": url, "verified": True, "found_on_url": page}
            for platform, (url, page) in sorted(found.items())
        ]
    finally:
        if client is None:
            own.close()


def homepage_title(website: str, *, client: Optional[httpx.Client] = None) -> Optional[str]:
    """The <title> of a site, used to confirm a discovered domain belongs to the named company."""
    if "://" not in website:
        website = "https://" + website
    own = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        html = _fetch(own, website)
    finally:
        if client is None:
            own.close()
    if not html:
        return None
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", match.group(1)).strip()[:200] if match else ""


def homepage_text(website: str, *, client: Optional[httpx.Client] = None) -> Optional[str]:
    """Title, meta description and visible text of a homepage (first 8,000 characters), lowercased."""
    if "://" not in website:
        website = "https://" + website
    own = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        html = _fetch(own, website)
    finally:
        if client is None:
            own.close()
    if html is None:
        return None
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    meta = " ".join(re.findall(r'(?i)<meta[^>]+content=["\']([^"\']{10,300})["\']', html))
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", f"{meta} {text}").strip().lower()[:8000]
