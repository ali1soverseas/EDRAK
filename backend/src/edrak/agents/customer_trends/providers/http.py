"""Shared HTTP plumbing: client factory, retrying JSON requests and a per-provider limiter."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderError,
    ProviderNotConfigured,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderUnavailable,
)

USER_AGENT = "edrak-customer-trends/0.1"
DEFAULT_ATTEMPTS = 3
MAX_RETRY_AFTER_S = 60.0
_BODY_PREVIEW_CHARS = 200

ErrorMapper = Callable[[httpx.Response], ProviderError | None]


def make_client(
    timeout: float = 30.0,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        transport=transport,
        follow_redirects=True,
    )


class RateLimiter:
    """Keeps at least `min_interval_s` between the starts of consecutive requests."""

    def __init__(
        self,
        min_interval_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._interval = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            wait = self._next_slot - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._next_slot = max(self._clock(), self._next_slot) + self._interval


class _Retryable(Exception):
    def __init__(self, error: ProviderError) -> None:
        super().__init__(str(error))
        self.error = error


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


_BACKOFF = wait_random_exponential(multiplier=0.5, max=8)


def _wait(state: RetryCallState) -> float:
    """Exponential backoff with jitter, or the server's Retry-After when that is longer."""
    delay = _BACKOFF(state)
    error = state.outcome.exception() if state.outcome else None
    if isinstance(error, _Retryable) and isinstance(error.error, ProviderRateLimited):
        advised = error.error.retry_after_s
        if advised:
            return max(delay, min(advised, MAX_RETRY_AFTER_S))
    return delay


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "")
    return float(value) if value.replace(".", "", 1).isdigit() else None


def _body_preview(response: httpx.Response) -> str:
    return " ".join(response.text.split())[:_BODY_PREVIEW_CHARS]


def _interpret(
    response: httpx.Response,
    provider: str,
    error_mapper: ErrorMapper | None,
    empty_ok: bool,
) -> Any:
    mapped = error_mapper(response) if error_mapper else None
    if mapped is not None:
        if isinstance(mapped, ProviderRateLimited | ProviderUnavailable) and mapped.retryable:
            raise _Retryable(mapped)
        raise mapped
    status = response.status_code
    if status in (401, 403):
        raise ProviderNotConfigured(
            f"{provider}: credentials rejected (HTTP {status}); check the API key in .env"
        )
    if status == 402:
        raise ProviderQuotaExceeded(f"{provider}: usage or credit limit reached (HTTP 402)")
    if status == 429:
        raise _Retryable(
            ProviderRateLimited(
                f"{provider}: rate limited (HTTP 429)", retry_after_s=_retry_after(response)
            )
        )
    if status == 408 or status >= 500:
        raise _Retryable(ProviderUnavailable(f"{provider}: server error (HTTP {status})"))
    if status >= 400:
        raise ProviderBadResponse(f"{provider}: HTTP {status}: {_body_preview(response)}")
    if empty_ok and not response.content.strip():
        return {}
    try:
        return response.json()
    except ValueError:
        raise ProviderBadResponse(
            f"{provider}: response is not valid JSON: {_body_preview(response)}"
        ) from None


async def _attempt(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    provider: str,
    limiter: RateLimiter | None,
    error_mapper: ErrorMapper | None,
    empty_ok: bool,
    **request: Any,
) -> Any:
    if limiter is not None:
        await limiter.acquire()
    try:
        response = await client.request(method, url, **request)
    except httpx.TimeoutException:
        raise _Retryable(ProviderUnavailable(f"{provider}: request timed out")) from None
    except httpx.TransportError as exc:
        raise _Retryable(
            ProviderUnavailable(f"{provider}: connection failed ({type(exc).__name__})")
        ) from None
    return _interpret(response, provider, error_mapper, empty_ok)


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    params: dict[str, Any] | None = None,
    json: Any = None,
    headers: dict[str, str] | None = None,
    limiter: RateLimiter | None = None,
    error_mapper: ErrorMapper | None = None,
    empty_ok: bool = False,
    attempts: int = DEFAULT_ATTEMPTS,
    request_timeout: float | None = None,
) -> Any:
    """Send a request and return its parsed JSON.

    Rate limits (429), server errors, timeouts and connection failures are retried with
    exponential backoff and jitter, then reported as `ProviderRateLimited` or
    `ProviderUnavailable`. Rejected credentials (401, 403), usage limits (402), other client
    errors and malformed bodies fail at once. `error_mapper` can claim a response first, for
    providers that signal quota problems inside a 403, and its errors are retried only when
    their `retryable` flag is true. A 429 with `Retry-After` waits at least that long (up to a
    minute). `request_timeout` overrides the client's timeout for slow calls. Error messages never
    include the URL, which may carry credentials.
    """
    try:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(_Retryable),
            stop=stop_after_attempt(attempts),
            wait=_wait,
            sleep=_sleep,
            reraise=True,
        ):
            with attempt:
                return await _attempt(
                    client,
                    method,
                    url,
                    provider,
                    limiter,
                    error_mapper,
                    empty_ok,
                    params=params,
                    json=json,
                    headers=headers,
                    **({"timeout": request_timeout} if request_timeout is not None else {}),
                )
    except _Retryable as exc:
        raise exc.error from None
    raise AssertionError("unreachable: AsyncRetrying always yields an attempt")
