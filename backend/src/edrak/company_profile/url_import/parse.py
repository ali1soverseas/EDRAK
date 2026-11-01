"""Read the parts of a page that are not its main text: metadata, structured data, links."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html

from edrak.agents.competitor_intelligence.socials import normalise_profile, platform_of
from edrak.company_profile.url_import.urls import strip_tracking

ORG_TYPES = frozenset({"organization", "corporation", "localbusiness"})
_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "sms:", "data:", "whatsapp:")
_TEXT_CHARS = 300


@dataclass
class Org:
    """An Organization, Corporation or LocalBusiness from the page's JSON-LD."""

    name: str = ""
    alternate_names: list[str] = field(default_factory=list)
    description: str = ""
    same_as: list[str] = field(default_factory=list)
    address: str = ""
    area_served: list[str] = field(default_factory=list)


@dataclass
class Link:
    url: str  # absolute, without a fragment or tracking parameters
    text: str = ""
    in_nav: bool = False  # inside a nav, header or footer element
    lang: Optional[str] = None  # a language hint from the page (hreflang)


@dataclass
class PageInfo:
    title: str = ""
    description: str = ""
    site_name: str = ""
    og_description: str = ""
    lang: str = ""
    orgs: list[Org] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    alternates: dict[str, str] = field(default_factory=dict)  # hreflang -> URL

    @property
    def has_metadata(self) -> bool:
        return bool(self.title or self.description or self.site_name or self.og_description or self.orgs)


def _clean(value: Any, limit: int = _TEXT_CHARS) -> str:
    return " ".join(str(value or "").split())[:limit]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _names(value: Any) -> list[str]:
    """Strings out of a JSON-LD value that may be a string, an object with a name, or a list of either."""
    found: list[str] = []
    for item in _as_list(value):
        text = _clean(item.get("name") if isinstance(item, dict) else item)
        if text and text not in found:
            found.append(text)
    return found


def _address(value: Any) -> str:
    for item in _as_list(value):
        if isinstance(item, str):
            return _clean(item)
        if isinstance(item, dict):
            parts = [item.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")]
            parts = [_clean(p.get("name") if isinstance(p, dict) else p) for p in parts]
            joined = ", ".join(p for p in parts if p)
            if joined:
                return joined
    return ""


def _walk(node: Any) -> Iterable[dict[str, Any]]:
    """Every object in a JSON-LD document, including those inside @graph and lists."""
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        if "@graph" in node:
            yield from _walk(node["@graph"])


def parse_json_ld(blocks: Iterable[str]) -> list[Org]:
    orgs: list[Org] = []
    for block in blocks:
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for obj in _walk(data):
            types = {str(t).lower().rsplit("/", 1)[-1] for t in _as_list(obj.get("@type"))}
            if not types & ORG_TYPES:
                continue
            orgs.append(
                Org(
                    name=_clean(obj.get("name")),
                    alternate_names=_names(obj.get("alternateName")),
                    description=_clean(obj.get("description"), 600),
                    same_as=[u for u in _names(obj.get("sameAs")) if u.startswith(("http://", "https://"))],
                    address=_address(obj.get("address")),
                    area_served=_names(obj.get("areaServed")),
                )
            )
    return orgs


def _meta(tree: Any, key: str) -> str:
    for attr in ("name", "property"):
        values = tree.xpath(f'//meta[translate(@{attr}, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")="{key}"]/@content')
        if values:
            return _clean(values[0], 600)
    return ""


def _absolute(base: str, href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.lower().startswith(_SKIP_SCHEMES):
        return None
    try:
        joined = urljoin(base, href)
        parts = urlsplit(joined)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return strip_tracking(urlunsplit(parts._replace(fragment="")))


def parse_page(html: str, base_url: str) -> PageInfo:
    """Metadata, JSON-LD organizations and links of one HTML page. An unparseable page gives an empty result."""
    info = PageInfo()
    if not html or not html.strip():
        return info
    try:
        parser = lxml_html.HTMLParser(encoding="utf-8")
        tree = lxml_html.document_fromstring(html.encode("utf-8", "replace"), parser=parser)
    except (ValueError, lxml_html.etree.LxmlError):
        return info

    titles = tree.xpath("//head/title")
    info.title = _clean(titles[0].text_content(), 200) if titles else ""
    info.description = _meta(tree, "description")
    info.site_name = _meta(tree, "og:site_name")
    info.og_description = _meta(tree, "og:description")
    info.lang = _clean(tree.get("lang") or "", 20).lower()
    info.orgs = parse_json_ld(tree.xpath('//script[@type="application/ld+json"]/text()'))

    seen: dict[str, Link] = {}
    for anchor in tree.xpath("//a[@href]"):
        url = _absolute(base_url, anchor.get("href"))
        if not url:
            continue
        text = _clean(anchor.text_content()) or _clean(anchor.get("title") or anchor.get("aria-label"))
        in_nav = bool(anchor.xpath("ancestor::nav or ancestor::header or ancestor::footer"))
        lang = _clean(anchor.get("hreflang") or "", 12).lower() or None
        known = seen.get(url)
        if known is None:
            seen[url] = Link(url=url, text=text, in_nav=in_nav, lang=lang)
        else:
            known.in_nav = known.in_nav or in_nav
            known.text = known.text or text
            known.lang = known.lang or lang
    info.links = list(seen.values())

    for node in tree.xpath('//link[@rel="alternate"][@hreflang][@href]'):
        url = _absolute(base_url, node.get("href"))
        code = _clean(node.get("hreflang"), 12).lower()
        if url and code and code not in info.alternates:
            info.alternates[code] = url
    return info


def social_profiles(urls: Iterable[str]) -> list[str]:
    """Official-looking social profile URLs among `urls`, canonical and without repeats, first seen first.

    Recognizes Facebook, Instagram, X or Twitter, LinkedIn, YouTube and TikTok. Share buttons, posts
    and hashtags are not profiles. A LinkedIn link to a person is skipped: a company site links to
    its company page.
    """
    found: list[str] = []
    seen: set[str] = set()
    for url in urls:
        profile = normalise_profile(url)
        if not profile or profile.lower() in seen:
            continue
        if platform_of(profile) == "linkedin" and "/in/" in profile:
            continue
        seen.add(profile.lower())
        found.append(profile)
    return found


_LANG_PREFIX = re.compile(r"^([a-z]{2})(?:[-_][a-z]{2})?$")


def language_hint(code: str) -> Optional[str]:
    """`ar` or `en` from a language code such as `ar-EG` or `en_US`; None for anything else."""
    match = _LANG_PREFIX.match((code or "").strip().lower())
    return match.group(1) if match and match.group(1) in ("ar", "en") else None
