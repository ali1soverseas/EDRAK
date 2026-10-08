"""URL checks shared by the tools that take a URL from the model."""

import ipaddress
from urllib.parse import urlsplit

from edrak.agents.customer_trends.schemas.common import Platform

PLATFORM_DOMAINS: dict[Platform, tuple[str, ...]] = {
    Platform.X: ("x.com", "twitter.com"),
    Platform.REDDIT: ("reddit.com", "redd.it"),
    Platform.TIKTOK: ("tiktok.com",),
    Platform.INSTAGRAM: ("instagram.com",),
    Platform.FACEBOOK: ("facebook.com", "fb.com", "fb.watch"),
    Platform.YOUTUBE: ("youtube.com", "youtu.be"),
}
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def host_of(url: str) -> str | None:
    """The lowercase host of an http(s) URL, or None if it is not one."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    return host if parts.scheme in ("http", "https") and host else None


def on_domain(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith(f".{d}") for d in domains)


def social_platform_of(host: str) -> Platform | None:
    return next((p for p, domains in PLATFORM_DOMAINS.items() if on_domain(host, domains)), None)


def non_public_reason(host: str) -> str | None:
    """Why a host must not be fetched (loopback, private or internal), or None if it may be.

    Only names and literal IP addresses are checked; a public name that resolves to a private
    address is not caught here.
    """
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES) or "." not in host and ":" not in host:
        return "it is a local or internal address"
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    if address.is_global:
        return None
    return "it is a private, loopback or reserved IP address"
