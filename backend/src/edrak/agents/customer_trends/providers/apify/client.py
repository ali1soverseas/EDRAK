"""Apify REST client: run an actor synchronously and read its dataset items."""

from typing import Any

import httpx

from edrak.agents.customer_trends.providers.base import (
    ProviderBadResponse,
    ProviderError,
    ProviderUnavailable,
)
from edrak.agents.customer_trends.providers.http import RateLimiter, request_json

NAME = "apify"
BASE_URL = "https://api.apify.com/v2"
MAX_SYNC_TIMEOUT_S = 300
_TIMEOUT_MARGIN_S = 30


class RunNotFinished(ProviderUnavailable):
    """The actor ran out of time or failed. Running it again would cost again, so no retry."""

    retryable = False


def _error_type(response: httpx.Response) -> tuple[str, str]:
    try:
        error = response.json()["error"]
        return str(error.get("type", "")), str(error.get("message", ""))
    except (ValueError, KeyError, TypeError, AttributeError):
        return "", ""


def map_error(response: httpx.Response) -> ProviderError | None:
    """Apify reports a run that timed out or crashed as 408 or as a 400 `run-failed`."""
    status = response.status_code
    kind, message = _error_type(response)
    if status == 408:
        return RunNotFinished(f"{NAME}: actor run did not finish within the time limit")
    if status == 400 and kind in {"run-failed", "actor-run-failed", "run-timeout-exceeded"}:
        reason = "timed out" if "TIMED-OUT" in message or "timeout" in kind else "failed"
        return RunNotFinished(f"{NAME}: actor run {reason}")
    if status == 400 and kind == "invalid-input":
        return ProviderBadResponse(f"{NAME}: actor input rejected: {message[:200]}")
    return None


def actor_path(actor_id: str) -> str:
    """Apify addresses actors as `username~name` in URLs."""
    return actor_id.replace("/", "~")


class ApifyClient:
    def __init__(
        self, client: httpx.AsyncClient, token: str, *, limiter: RateLimiter | None = None
    ) -> None:
        self._client = client
        self._token = token
        self._limiter = limiter

    async def run(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        *,
        limit: int,
        timeout_s: int = MAX_SYNC_TIMEOUT_S,
    ) -> list[dict[str, Any]]:
        """Run `actor_id` with `run_input` and return at most `limit` dataset items."""
        timeout_s = min(timeout_s, MAX_SYNC_TIMEOUT_S)
        data = await request_json(
            self._client,
            "POST",
            f"{BASE_URL}/acts/{actor_path(actor_id)}/run-sync-get-dataset-items",
            provider=NAME,
            params={"limit": limit, "timeout": timeout_s},
            json=run_input,
            headers={"Authorization": f"Bearer {self._token}"},
            limiter=self._limiter,
            error_mapper=map_error,
            request_timeout=timeout_s + _TIMEOUT_MARGIN_S,
        )
        if not isinstance(data, list):
            raise ProviderBadResponse(f"{NAME}: expected a list of dataset items")
        return [item for item in data if isinstance(item, dict)]
