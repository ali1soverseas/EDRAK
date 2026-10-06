"""ApifyProvider: capabilities served by Apify actors, configured in providers.yaml."""

import re
import string
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import Field

from edrak.agents.customer_trends.providers.apify.client import ApifyClient
from edrak.agents.customer_trends.providers.apify.mappers import (
    MAPPERS,
    TREND_MAPPERS,
    MapContext,
)
from edrak.agents.customer_trends.providers.base import (
    CallParams,
    ProviderBadResponse,
    ProviderResult,
    in_window,
)
from edrak.agents.customer_trends.providers.config import ProviderConfig
from edrak.agents.customer_trends.providers.http import RateLimiter
from edrak.agents.customer_trends.providers.keys import KeyRing, with_failover
from edrak.agents.customer_trends.schemas.common import StrictModel
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries

NAME = "apify"
DEFAULT_TIMEOUT_S = 240
_DROP = object()
_STATUS_ID = re.compile(r"/status(?:es)?/(\d+)")
_WINDOWS = (
    (1, "day"),
    (7, "week"),
    (31, "month"),
    (93, "quarter"),
    (186, "half_year"),
    (366, "year"),
)


class ActorSpec(StrictModel):
    """One capability's actor: which one, what to send, how to read the answer."""

    actor: str | None = None
    mapper: str
    input: dict[str, Any] = Field(default_factory=dict)
    values: dict[str, dict[str, str]] = Field(default_factory=dict)
    requires: list[str] = Field(default_factory=list)
    enabled: bool = True
    min_items: int | None = None
    cost_per_item_usd: float = 0.0
    cost_per_run_usd: float = 0.0
    verify: bool = False
    note: str = ""


def date_window(since: date | None, today: date) -> str | None:
    """Smallest named window that reaches back to `since`; `all` when it is further."""
    if since is None:
        return None
    days = (today - since).days
    return next((name for limit, name in _WINDOWS if days <= limit), "all")


def template_values(call: CallParams, today: date, limit: int) -> dict[str, Any]:
    """Everything an actor input template can refer to. Missing values are None."""
    single_language = call.languages[0] if len(call.languages) == 1 else None
    target = call.target
    query = " ".join([call.query, *call.hashtags]).strip()
    status = _STATUS_ID.search(call.post_url or "")
    return {
        "query": query or None,
        "hashtag": re.sub(r"\W+", "", query).lower() or None,
        "language": single_language,
        "geo": call.geo,
        "since": call.since.isoformat() if call.since else None,
        "until": call.until.isoformat() if call.until else None,
        "window": date_window(call.since, today),
        "max_results": limit,
        "max_results_plus_one": limit + 1,
        "url": call.post_url,
        "status_id": status.group(1) if status else None,
        "sort": call.sort,
        "keywords": call.keywords or ([query] if query else None),
        "timeframe": call.timeframe,
        "target": target,
        "target_url": (
            target if target.startswith("http") else f"https://www.amazon.com/dp/{target}"
        )
        if target
        else None,
        "country_lower": call.country.lower() if call.country else None,
        "country_upper": call.country.upper() if call.country else None,
    }


def render_input(
    template: dict[str, Any], values: dict[str, Any], mappings: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """Fill an actor input template.

    A string that is exactly `{name}` becomes the value itself (so numbers and lists keep their
    type), after the actor-specific `mappings[name]` translation if there is one. A string with
    text around placeholders is formatted. A key, or a list item, whose placeholder has no value
    is left out, which lets the actor use its own default.
    """
    rendered = _render(template, values, mappings)
    return {} if rendered is _DROP else rendered


def _render(template: Any, values: dict[str, Any], mappings: dict[str, dict[str, str]]) -> Any:
    if isinstance(template, dict):
        fields = {k: _render(v, values, mappings) for k, v in template.items()}
        kept_fields = {k: v for k, v in fields.items() if v is not _DROP}
        return kept_fields if kept_fields or not template else _DROP
    if isinstance(template, list):
        items = [_render(v, values, mappings) for v in template]
        kept = [v for v in items if v is not _DROP]
        return kept if kept or not template else _DROP
    if not isinstance(template, str):
        return template
    names = [name for _, name, _, _ in string.Formatter().parse(template) if name]
    if not names:
        return template
    resolved = {name: _lookup(name, values, mappings) for name in names}
    if any(value is None for value in resolved.values()):
        return _DROP
    if template == "{" + names[0] + "}":
        return resolved[names[0]]
    return template.format_map(resolved)


def _lookup(name: str, values: dict[str, Any], mappings: dict[str, dict[str, str]]) -> Any:
    if name not in values:
        raise ValueError(f"unknown input placeholder {{{name}}}")
    value = values[name]
    if name in mappings:
        return mappings[name].get(str(value))
    return value


class ApifyProvider:
    name = NAME

    def __init__(
        self,
        client: httpx.AsyncClient,
        token: str,
        config: ProviderConfig,
        *,
        fallback_tokens: Sequence[str] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http = client
        self._limiter = RateLimiter(config.min_interval_s)
        self._keys = KeyRing([token, *fallback_tokens])
        self._clock = clock
        self._timeout_s = int(config.options.get("timeout_s", DEFAULT_TIMEOUT_S))
        self._specs = {
            capability: ActorSpec.model_validate(spec)
            for capability, spec in config.options.get("actors", {}).items()
        }
        for capability, spec in self._specs.items():
            if spec.mapper not in MAPPERS and spec.mapper not in TREND_MAPPERS:
                raise ValueError(f"{capability}: unknown mapper {spec.mapper!r}")
        self.capabilities = {c for c, spec in self._specs.items() if spec.enabled and spec.actor}

    def health(self) -> dict[str, Any]:
        return {"key_in_use": f"{self._keys.position} of {self._keys.size}"}

    def spec_for(self, capability: str) -> ActorSpec:
        return self._specs[capability]

    async def call(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        """Serve a capability, moving to a fallback token if the current one is out of credit."""
        return await with_failover(self._keys, NAME, lambda: self._serve(capability, params))

    async def _serve(self, capability: str, params: dict[str, Any]) -> ProviderResult:
        spec = self._specs.get(capability)
        if spec is None or not spec.enabled or not spec.actor:
            raise ProviderBadResponse(f"{NAME}: unsupported capability {capability!r}")
        call = CallParams.model_validate(params)
        now = self._clock()
        limit = max(call.wanted, spec.min_items or 0)
        values = template_values(call, now.date(), limit)
        missing = [name for name in spec.requires if values.get(name) is None]
        if missing:
            raise ProviderBadResponse(f"{NAME}: {capability} needs {', '.join(missing)}")
        run_input = render_input(spec.input, values, spec.values)
        apify = ApifyClient(self._http, self._keys.current, limiter=self._limiter)
        raw = await apify.run(spec.actor, run_input, limit=limit, timeout_s=self._timeout_s)

        ctx = MapContext(call, now)
        cost = spec.cost_per_run_usd + spec.cost_per_item_usd * len(raw)
        if spec.mapper in TREND_MAPPERS:
            series: list[TrendSeries] = [
                s for item in raw if (s := TREND_MAPPERS[spec.mapper](item, ctx)) is not None
            ]
            return ProviderResult(items=series, raw_count=len(raw), cost_estimate=cost)
        mapped: list[EvidenceItem] = [
            e for item in raw if (e := MAPPERS[spec.mapper](item, ctx)) is not None
        ]
        kept = [e for e in mapped if in_window(e.published_at, call.since, call.until)]
        result = ProviderResult(items=kept[: call.wanted], raw_count=len(raw), cost_estimate=cost)
        if len(kept) < len(mapped):
            result.meta["outside_date_window"] = len(mapped) - len(kept)
        return result
