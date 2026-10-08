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
_PLAN_LIMIT_TEXT = "limit exceeded"


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

    async def plan_limit_reached(self, actor_id: str) -> bool:
        """Whether this account's latest run of the actor was refused for a plan limit.

        A free account that used up an actor's monthly runs still gets a run that succeeds, with
        one placeholder row, and the synchronous endpoint does not name the run. The refusal is
        only in that run's log, so it is read when a run returned nothing but placeholders. Any
        failure to look is treated as "no".
        """
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            runs = await self._client.get(
                f"{BASE_URL}/acts/{actor_path(actor_id)}/runs",
                params={"limit": 1, "desc": 1},
                headers=headers,
            )
            runs.raise_for_status()
            latest = runs.json()["data"]["items"][0]["id"]
            log = await self._client.get(f"{BASE_URL}/actor-runs/{latest}/log", headers=headers)
            log.raise_for_status()
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            return False
        return _PLAN_LIMIT_TEXT in log.text.lower()
