"""Routes a capability to its providers in configured order, with fallback."""

import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError
from structlog.contextvars import bound_contextvars

from edrak.core.llm import provider_key
from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.providers.apify import ApifyProvider
from edrak.agents.customer_trends.providers.base import (
    Provider,
    ProviderBadResponse,
    ProviderError,
    ProviderExhausted,
    ProviderFailure,
    ProviderResult,
    parse_capability,
)
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.providers.cache import DiskCache
from edrak.agents.customer_trends.providers.config import (
    ProvidersConfig,
    load_providers_config,
)
from edrak.agents.customer_trends.providers.direct_http import DirectHttpProvider
from edrak.agents.customer_trends.providers.gdelt import GdeltProvider
from edrak.agents.customer_trends.providers.google_trends_api import (
    GoogleTrendsApiProvider,
)
from edrak.agents.customer_trends.providers.http import make_client
from edrak.agents.customer_trends.providers.serper import SerperProvider
from edrak.agents.customer_trends.providers.socialcrawl import SocialCrawlProvider
from edrak.agents.customer_trends.providers.youtube import YouTubeProvider, YouTubeQuota

if TYPE_CHECKING:
    from edrak.agents.customer_trends.settings import Settings

FIXTURES_RELATIVE = Path("backend") / "tests" / "customer_trends" / "fixtures" / "providers"
CLIENT_TIMEOUT_S = 30.0

log = get_logger(__name__)


def fixture_filename(capability: str) -> str:
    """File name for a capability: `social_search:x` is `social_search.x.json`."""
    return f"{capability.replace(':', '.')}.json"


class ProviderRegistry:
    def __init__(
        self,
        config: ProvidersConfig,
        *,
        budget: BudgetTracker,
        breaker: CircuitBreaker,
        cache: DiskCache | None = None,
        fixture_mode: bool = False,
        fixtures_dir: Path | None = None,
        providers: Iterable[Provider] = (),
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._budget = budget
        self._breaker = breaker
        self._cache = cache
        self._fixture_mode = fixture_mode
        self._fixtures_dir = fixtures_dir
        self._client = client
        self._providers: dict[str, Provider] = {}
        for provider in providers:
            self.register(provider)

    @classmethod
    def from_config(
        cls,
        settings: "Settings",
        budget: BudgetTracker,
        breaker: CircuitBreaker,
        cache: DiskCache | None = None,
        *,
        config: ProvidersConfig | None = None,
        client: httpx.AsyncClient | None = None,
        fixtures_dir: Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> "ProviderRegistry":
        """Register the providers that can run with the configured keys.

        Providers that need a key are left out without one; the keyless GDELT and direct page
        fetcher and the Google Trends stub are always registered. In fixture mode no provider
        is built at all and no client is opened.
        """
        config = config or load_providers_config()
        fixture_mode = settings.edrak_provider_mode == "fixture"
        registry = cls(
            config,
            budget=budget,
            breaker=breaker,
            cache=cache,
            fixture_mode=fixture_mode,
            fixtures_dir=fixtures_dir or settings.repo_root / FIXTURES_RELATIVE,
            client=client,
        )
        if fixture_mode:
            return registry
        http_client = registry._client = client or make_client(CLIENT_TIMEOUT_S)
        serper_key, _serper_variable = provider_key("SERPER_API_KEY", "customer_trends")
        if serper_key:
            registry.register(
                SerperProvider(
                    http_client,
                    serper_key,
                    config.providers["serper"],
                    clock=clock,
                )
            )
        registry.register(GdeltProvider(http_client, config.providers["gdelt"], clock=clock))
        registry.register(
            DirectHttpProvider(http_client, config.providers["direct_http"], clock=clock)
        )
        apify_tokens = settings.key_list("apify_token", "apify_fallback_tokens")
        if apify_tokens:
            registry.register(
                ApifyProvider(
                    http_client,
                    apify_tokens[0],
                    config.providers["apify"],
                    fallback_tokens=apify_tokens[1:],
                    clock=clock,
                )
            )
        socialcrawl_keys = settings.key_list("socialcrawl_api_key", "socialcrawl_fallback_api_keys")
        if socialcrawl_keys:
            registry.register(
                SocialCrawlProvider(
                    http_client,
                    socialcrawl_keys[0],
                    settings.socialcrawl_base_url,
                    config.providers["socialcrawl"],
                    fallback_keys=socialcrawl_keys[1:],
                    clock=clock,
                )
            )
        trends_key = settings.google_trends_api_key
        registry.register(
            GoogleTrendsApiProvider(
                trends_key.get_secret_value()
                if trends_key and settings.has_key("google_trends_api_key")
                else None
            )
        )
        if settings.youtube_api_key and settings.has_key("youtube_api_key"):
            quota = YouTubeQuota(
                settings.data_dir / "youtube_quota.json",
                settings.youtube_daily_quota,
                settings.youtube_search_daily_cap,
                clock=clock,
            )
            registry.register(
                YouTubeProvider(
                    http_client,
                    settings.youtube_api_key.get_secret_value(),
                    config.providers["youtube_api"],
                    quota,
                    clock=clock,
                )
            )
        return registry

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    @property
    def providers(self) -> dict[str, Provider]:
        return dict(self._providers)

    @property
    def budget(self) -> BudgetTracker:
        return self._budget

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def health(self) -> dict[str, dict[str, Any]]:
        """Each registered provider: its capabilities, whether its breaker is open, its failures in
        a row, and what it knows about its own quota or keys (never a key value)."""
        report: dict[str, dict[str, Any]] = {}
        for name, provider in sorted(self._providers.items()):
            entry: dict[str, Any] = {
                "capabilities": len(provider.capabilities),
                "breaker_open": self._breaker.is_open(name),
                "failures": self._breaker.failures(name),
            }
            own = getattr(provider, "health", None)
            if callable(own):
                entry.update(own())
            report[name] = entry
        return report

    def routing_for(self, capability: str) -> list[str]:
        return list(self._config.routing.get(capability, []))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        """Serve a capability from the first provider that works.

        Raises `BudgetExceeded` before any provider is tried when the run's budget is used up,
        and `ProviderExhausted` when every provider failed or was skipped. Each call counts as
        one tool call on the budget, together with the cost of the provider that served it.
        """
        parse_capability(capability)
        self._budget.check_before_call()
        started = time.perf_counter()
        try:
            if self._fixture_mode:
                result = self._serve_fixture(capability, params)
            else:
                result = await self._route(capability, params)
        except ProviderExhausted as exc:
            self._budget.record()
            log.warning(
                "provider_exhausted",
                capability=capability,
                failures=[f"{f.provider}:{f.error}" for f in exc.failures],
            )
            raise
        self._budget.record(result.cost_estimate)
        log.info(
            "provider_call",
            capability=capability,
            provider=result.provider,
            fallback_used=result.fallback_used,
            count=len(result.items),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return result

    async def _route(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        order = self.routing_for(capability)
        failures: list[ProviderFailure] = []
        for position, name in enumerate(order):
            provider = self._providers.get(name)
            if provider is None:
                failures.append(ProviderFailure(name, "unregistered", "not configured", True))
                continue
            if capability not in provider.capabilities:
                failures.append(ProviderFailure(name, "unsupported", capability, True))
                continue
            if self._breaker.is_open(name):
                failures.append(ProviderFailure(name, "breaker_open", "failed repeatedly", True))
                continue
            cached = self._cache.get(name, capability, params) if self._cache else None
            if cached is not None:
                return self._tag(self._restamp(cached, params), name, position)
            try:
                with bound_contextvars(provider=name):
                    result = await provider.call(capability, params)
            except ProviderError as exc:
                failure = ProviderFailure(name, type(exc).__name__, str(exc))
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                bad = ProviderBadResponse(f"{name}: unexpected {type(exc).__name__}: {exc}")
                failure = ProviderFailure(name, type(bad).__name__, str(bad))
            else:
                self._breaker.record_success(name)
                if self._cache:
                    self._cache.put(name, capability, params, result)
                return self._tag(result, name, position)
            self._breaker.record_failure(name)
            failures.append(failure)
            log.warning(
                "provider_failed", capability=capability, provider=name, error=failure.error
            )
        raise ProviderExhausted(capability, failures)

    @staticmethod
    def _tag(result: ProviderResult, name: str, position: int) -> ProviderResult:
        return result.model_copy(update={"provider": name, "fallback_used": position > 0})

    @staticmethod
    def _restamp(result: ProviderResult, params: dict[str, Any]) -> ProviderResult:
        run_id, task_id = params.get("run_id"), params.get("task_id")
        return result.restamped(run_id, task_id) if run_id and task_id else result

    def _serve_fixture(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        path = (self._fixtures_dir or Path()) / fixture_filename(capability)
        try:
            result = ProviderResult.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise ProviderExhausted(
                capability,
                [ProviderFailure("fixture", type(exc).__name__, f"{path.name}: {exc}")],
            ) from exc
        result = self._restamp(result, params)
        limit = params.get("max_results")
        if isinstance(limit, int) and limit > 0:
            result = result.model_copy(update={"items": result.items[:limit]})
        return result.model_copy(
            update={
                "provider": result.provider or "fixture",
                "fallback_used": False,
                "cost_estimate": 0.0,
            }
        )
