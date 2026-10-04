import httpx
import pytest
import respx

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderUnavailable,
)
from edrak.agents.customer_trends.providers.http import RateLimiter, make_client, request_json
from tests.customer_trends.factories import FakeClock

URL = "https://api.example.test/v1/items"


async def fetch(**kwargs: object) -> object:
    async with make_client(5) as client:
        return await request_json(client, "GET", URL, provider="example", **kwargs)  # type: ignore[arg-type]  # test kwargs


async def test_success_returns_parsed_json(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL, params={"q": "x"}).mock(
        return_value=httpx.Response(200, json={"ok": True, "نص": "قيمة"})
    )
    assert await fetch(params={"q": "x"}, headers={"X-Key": "k"}) == {"ok": True, "نص": "قيمة"}
    assert route.calls.last.request.headers["x-key"] == "k"
    assert route.calls.last.request.headers["user-agent"].startswith("edrak-customer-trends")


async def test_post_sends_json_body(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(URL).mock(return_value=httpx.Response(200, json=[1]))
    async with make_client(5) as client:
        assert await request_json(client, "POST", URL, provider="example", json={"q": "قهوة"}) == [
            1
        ]
    assert route.calls.last.request.read() == '{"q":"قهوة"}'.encode()


async def test_rate_limits_are_retried_then_succeed(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(429), httpx.Response(200, json={"ok": 1})]
    )
    assert await fetch() == {"ok": 1}
    assert route.call_count == 3


async def test_rate_limit_after_three_attempts_is_reported_with_retry_after(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(ProviderRateLimited) as caught:
        await fetch()
    assert route.call_count == 3
    assert caught.value.retry_after_s == 7.0
    assert "example" in str(caught.value)


async def test_server_errors_are_retried_then_unavailable(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(503, text="down"))
    with pytest.raises(ProviderUnavailable, match="HTTP 503"):
        await fetch()
    assert route.call_count == 3


async def test_timeouts_and_connection_failures_are_unavailable(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderUnavailable, match="timed out"):
        await fetch()
    assert route.call_count == 3
    respx_mock.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderUnavailable, match="ConnectError"):
        await fetch()


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_credentials_fail_at_once_with_a_clear_message(
    respx_mock: respx.MockRouter, status: int
) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(ProviderNotConfigured, match=f"HTTP {status}.*API key"):
        await fetch()
    assert route.call_count == 1


async def test_payment_required_is_a_quota_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).mock(return_value=httpx.Response(402))
    with pytest.raises(ProviderQuotaExceeded):
        await fetch()


async def test_other_client_errors_are_bad_responses_and_not_retried(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(400, text="bad  query\nplease"))
    with pytest.raises(ProviderBadResponse, match="HTTP 400: bad query please"):
        await fetch()
    assert route.call_count == 1


async def test_malformed_json_is_a_bad_response(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(ProviderBadResponse, match="not valid JSON"):
        await fetch()


async def test_empty_body_is_allowed_only_when_asked(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(URL).mock(return_value=httpx.Response(200, text=""))
    assert await fetch(empty_ok=True) == {}
    with pytest.raises(ProviderBadResponse):
        await fetch()


async def test_error_mapper_claims_a_response_first(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(403, json={"reason": "quota"}))
    with pytest.raises(ProviderQuotaExceeded, match="mapped"):
        await fetch(error_mapper=lambda response: ProviderQuotaExceeded("mapped"))
    assert route.call_count == 1


async def test_error_mapper_that_declines_leaves_the_default_mapping(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(403, json={}))
    with pytest.raises(ProviderNotConfigured):
        await fetch(error_mapper=lambda response: None)
    assert route.call_count == 1


async def test_mapped_rate_limits_are_retried(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(403))
    with pytest.raises(ProviderRateLimited):
        await fetch(error_mapper=lambda response: ProviderRateLimited("busy"))
    assert route.call_count == 3


async def test_error_messages_never_contain_the_url_or_credentials(
    respx_mock: respx.MockRouter,
) -> None:
    secret_url = f"{URL}?key=SECRET-KEY"
    respx_mock.get(secret_url).mock(side_effect=httpx.ConnectError("refused"))
    async with make_client(5) as client:
        with pytest.raises(ProviderUnavailable) as caught:
            await request_json(client, "GET", secret_url, provider="example")
    assert "SECRET-KEY" not in str(caught.value)
    assert "api.example.test" not in str(caught.value)


async def test_rate_limiter_spaces_requests() -> None:
    clock = FakeClock()
    limiter = RateLimiter(5.0, clock=clock, sleep=clock.sleep)
    await limiter.acquire()
    assert clock.sleeps == []
    await limiter.acquire()
    assert clock.sleeps == [5.0]
    clock.now += 2.0
    await limiter.acquire()
    assert clock.sleeps == [5.0, 3.0]
    clock.now += 60
    await limiter.acquire()
    assert clock.sleeps == [5.0, 3.0]


async def test_rate_limiter_is_applied_before_every_attempt(respx_mock: respx.MockRouter) -> None:
    clock = FakeClock()
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    respx_mock.get(URL).mock(side_effect=[httpx.Response(500), httpx.Response(200, json={})])
    await fetch(limiter=limiter)
    assert clock.sleeps == [1.0]
