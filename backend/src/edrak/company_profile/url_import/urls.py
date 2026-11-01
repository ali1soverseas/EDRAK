"""URL handling for the website import: normalizing what a person typed, and refusing non-public hosts."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from edrak.agents.customer_trends.tools.urls import non_public_reason
from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked

MAX_URL_CHARS = 2048

# Query parameters that only say where a visitor came from.
_TRACKING_PARAMS = frozenset(
    {"gclid", "dclid", "fbclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid", "_ga", "_gl",
     "_hsenc", "_hsmi", "ref", "ref_src", "spm"}
)
_TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_")
# "name:" at the start with no digit after it is a scheme (mailto:, javascript:), not host:port.
_SCHEME_PREFIX = re.compile(r"^[a-z][a-z0-9+.\-]*:(?!\d)", re.IGNORECASE)

_HOSTNAME = re.compile(r"^[a-z0-9_]([a-z0-9_\-.]*[a-z0-9_])?$")

# Second-level labels that sit under a country code (example.com.eg, example.co.uk).
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "gov", "edu", "ac", "sch", "mil", "or", "ne", "go"})

# Resolves a host to the IP addresses it points at. Tests pass their own.
Resolver = Callable[[str, int], Awaitable[list[str]]]


def _is_tracking(name: str) -> bool:
    key = name.lower()
    return key in _TRACKING_PARAMS or key.startswith(_TRACKING_PREFIXES)


def strip_tracking(url: str) -> str:
    """`url` without tracking parameters and without a fragment."""
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def normalize_url(raw: str) -> str:
    """The URL a person typed as an absolute http(s) URL, or raise UrlBlocked.

    A bare domain gets https. Tracking parameters and the fragment are dropped, the host is
    lowercased (and punycoded when it is not ASCII), and a default port is removed. Any scheme
    other than http and https, and any URL with a username or password in it, is refused.
    """
    text = (raw or "").strip()
    if not text:
        raise UrlBlocked("Enter the company's website address.")
    if len(text) > MAX_URL_CHARS:
        raise UrlBlocked("That web address is too long.")
    if "://" not in text:
        if _SCHEME_PREFIX.match(text):
            raise UrlBlocked("Only http and https web addresses can be imported.")
        text = "https://" + text.lstrip("/")
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise UrlBlocked("Only http and https web addresses can be imported.")
    try:
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise UrlBlocked("That is not a valid web address.") from None
    if not host:
        raise UrlBlocked("That is not a valid web address.")
    if parts.username is not None or parts.password is not None:
        raise UrlBlocked("Web addresses with a username or password cannot be imported.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise UrlBlocked("That is not a valid web address.") from None
    if ":" not in host and not _HOSTNAME.match(host):  # an IPv6 literal has colons and is checked later
        raise UrlBlocked("That is not a valid web address.")

    scheme = parts.scheme.lower()
    shown = f"[{host}]" if ":" in host else host
    if port and not (port == 80 and scheme == "http" or port == 443 and scheme == "https"):
        shown = f"{shown}:{port}"
    return strip_tracking(urlunsplit((scheme, shown, parts.path or "/", parts.query, "")))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def registered_domain(host: str) -> str:
    """The part of `host` a company owns: `shop.example.com` and `www.example.com` both give `example.com`.

    Without a public-suffix list this knows the common `com.eg` / `co.uk` shape and treats other
    hosts as name plus one label. An IP literal is returned as it is.
    """
    host = host.lower().strip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def same_site(a: str, b: str) -> bool:
    """Whether two URLs (or hosts) belong to the same registered domain."""
    return registered_domain(host_of(a) or a) == registered_domain(host_of(b) or b)


def _address_problem(raw: str) -> str | None:
    """Why `raw`, an IP address as text, must not be fetched, or None if it is public."""
    try:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return "it did not resolve to a valid address"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if not address.is_global:
        return "it points at a private, loopback, link-local or reserved address"
    return None


async def _system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def check_address(address: str) -> None:
    """Raise UrlBlocked when `address`, an IP as text, is not a public one."""
    problem = _address_problem(address)
    if problem:
        raise UrlBlocked(f"That address cannot be imported: {problem}.")


async def ensure_public(url: str, resolver: Resolver | None = None) -> None:
    """Raise UrlBlocked unless `url` is http(s) and its host resolves only to public addresses.

    Run it on the first URL and again on every redirect target. A host that does not resolve
    raises SiteUnreachable, since there is nothing to fetch rather than something to refuse.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise UrlBlocked("Only http and https web addresses can be imported.")
    if parts.username is not None or parts.password is not None:
        raise UrlBlocked("Web addresses with a username or password cannot be imported.")
    reason = non_public_reason(host)
    if reason:
        raise UrlBlocked(f"That address cannot be imported: {reason}.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        addresses = await (resolver or _system_resolver)(host, port)
    except (socket.gaierror, UnicodeError, OSError):
        raise SiteUnreachable(f"The website address {host} could not be found.") from None
    if not addresses:
        raise SiteUnreachable(f"The website address {host} could not be found.")
    for address in addresses:
        check_address(address)


def path_of(url: str) -> str:
    return unquote(urlsplit(url).path or "/")
