import pytest

from edrak.agents.customer_trends.schemas.common import Platform
from edrak.agents.customer_trends.tools.urls import (
    host_of,
    non_public_reason,
    on_domain,
    social_platform_of,
)


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://Example.COM/a?b=1", "example.com"),
        (" http://blog.example.test/post ", "blog.example.test"),
        ("ftp://example.com/a", None),
        ("example.com/a", None),
        ("not a url", None),
        ("https://", None),
        ("", None),
    ],
)
def test_host_of(url: str, host: str | None) -> None:
    assert host_of(url) == host


def test_on_domain_matches_subdomains_but_not_lookalikes() -> None:
    assert on_domain("www.reddit.com", ("reddit.com",))
    assert on_domain("reddit.com", ("reddit.com",))
    assert not on_domain("notreddit.com", ("reddit.com",))
    assert not on_domain("reddit.com.evil.test", ("reddit.com",))


@pytest.mark.parametrize(
    ("host", "platform"),
    [
        ("x.com", Platform.X),
        ("mobile.twitter.com", Platform.X),
        ("old.reddit.com", Platform.REDDIT),
        ("vm.tiktok.com", Platform.TIKTOK),
        ("www.instagram.com", Platform.INSTAGRAM),
        ("fb.watch", Platform.FACEBOOK),
        ("youtu.be", Platform.YOUTUBE),
        ("blog.example.test", None),
    ],
)
def test_social_platform_of(host: str, platform: Platform | None) -> None:
    assert social_platform_of(host) is platform


@pytest.mark.parametrize(
    "host",
    [
        "localhost", "app.localhost", "printer.local", "db.internal", "intranet",
        "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "172.16.0.9",
        "0.0.0.0",  # noqa: S104  # a host to refuse, not a bind address
        "::1", "[::1]", "fe80::1",
    ],
)  # fmt: skip
def test_non_public_hosts_are_refused(host: str) -> None:
    assert non_public_reason(host) is not None


@pytest.mark.parametrize(
    "host", ["example.com", "blog.example.test", "8.8.8.8", "2001:4860:4860::8888"]
)
def test_public_hosts_are_allowed(host: str) -> None:
    assert non_public_reason(host) is None
