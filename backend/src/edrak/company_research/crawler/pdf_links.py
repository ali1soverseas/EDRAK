"""PDFs that a page shows without a plain link to them: embedded in a frame, or opened in a viewer page.

A viewer page (pdf.js, the Google Docs viewer and the like) carries the address of the file in a
parameter, so the address to download is that parameter, not the viewer.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html

from edrak.company_profile.url_import.urls import strip_tracking

# Query parameters that hold the file a viewer shows.
VIEWER_PARAMS = ("file", "url", "src", "doc", "document", "pdf", "source")
_VIEWER_SEGMENT = re.compile(r"^(?:viewer(?:\.html?)?|gview|pdfjs|pdf\.js|pdf-?viewer|flipbook)$", re.IGNORECASE)
_EMBED_ATTRS = (("iframe", ("src", "data-src")), ("embed", ("src",)), ("object", ("data",)))


def has_pdf_extension(url: str) -> bool:
    """Whether the path of `url` ends in .pdf, or a .pdf name is given in its query."""
    parts = urlsplit(url)
    return unquote(parts.path).lower().endswith(".pdf") or ".pdf" in unquote(parts.query).lower()


def viewer_target(url: str) -> Optional[str]:
    """The address of the file a PDF viewer page at `url` shows, or None when `url` is not a viewer."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [s for s in unquote(parts.path).split("/") if s]
    google = host in ("docs.google.com", "drive.google.com") and bool(segments) and segments[-1] in ("viewer", "gview")
    if not google and not any(_VIEWER_SEGMENT.match(s) for s in segments):
        return None
    query = {k.lower(): v for k, v in parse_qsl(parts.query)}
    for name in VIEWER_PARAMS:
        value = (query.get(name) or "").strip()
        if not value:
            continue
        target = urlsplit(urljoin(url, value))
        if target.scheme in ("http", "https") and target.hostname:
            return strip_tracking(urlunsplit(target._replace(fragment="")))
    return None


def _absolute(base: str, value: str) -> Optional[str]:
    value = (value or "").strip()
    if not value or value.startswith(("#", "data:", "javascript:", "about:")):
        return None
    parts = urlsplit(urljoin(base, value))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return strip_tracking(urlunsplit(parts._replace(fragment="")))


def embedded_pdf_sources(html: str, base_url: str) -> list[tuple[str, str]]:
    """(address, label) of every iframe, embed and object in `html` that shows a PDF.

    One shows a PDF when its type says so, its address names a .pdf file, or its address is a
    viewer page. The address is returned as it is written; a viewer is unwrapped by the caller.
    """
    if not html or not html.strip():
        return []
    try:
        tree = lxml_html.document_fromstring(html.encode("utf-8", "replace"), parser=lxml_html.HTMLParser(encoding="utf-8"))
    except (ValueError, lxml_html.etree.LxmlError):
        return []
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for tag, attributes in _EMBED_ATTRS:
        for node in tree.iter(tag):
            kind = (node.get("type") or "").lower()
            for attribute in attributes:
                url = _absolute(base_url, node.get(attribute) or "")
                if not url or url in seen:
                    continue
                if "pdf" in kind or has_pdf_extension(url) or viewer_target(url):
                    seen.add(url)
                    found.append((url, " ".join((node.get("title") or node.get("aria-label") or "").split())))
    return found
