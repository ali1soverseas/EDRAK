"""Model prices and the cost estimate for one call.

Prices live in ``pricing.yaml`` beside this module and are overridable from the
environment (see that file). A model with no price yields an unknown cost rather
than zero, so a report never claims a call was free when it was merely unpriced.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_YAML = Path(__file__).with_name("pricing.yaml")


@dataclass(frozen=True)
class Price:
    """USD per one million tokens."""

    input_per_million: float | None = None
    output_per_million: float | None = None
    cached_input_per_million: float | None = None
    free_tier: bool = False

    @property
    def known(self) -> bool:
        if self.free_tier:
            return True
        return self.input_per_million is not None and self.output_per_million is not None


def model_key(model: str | None) -> str:
    """Environment-variable stem for a model name: gpt-oss:120b -> GPT_OSS_120B."""
    return re.sub(r"[^0-9A-Za-z]+", "_", model or "").strip("_").upper()


def _env_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_bool(name: str) -> bool | None:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return None
    return raw in {"1", "true", "yes", "on"}


def _load_yaml() -> dict[str, Any]:
    try:
        return yaml.safe_load(_YAML.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


@lru_cache(maxsize=1)
def _catalogue() -> dict[tuple[str, str], Price]:
    out: dict[tuple[str, str], Price] = {}
    for provider, block in (_load_yaml().get("providers") or {}).items():
        for model, spec in ((block or {}).get("models") or {}).items():
            spec = spec or {}
            out[(str(provider), str(model))] = Price(
                input_per_million=spec.get("input_per_million"),
                output_per_million=spec.get("output_per_million"),
                cached_input_per_million=spec.get("cached_input_per_million"),
                free_tier=bool(spec.get("free_tier", False)),
            )
    return out


def reload() -> None:
    """Forget the cached catalogue. Tests call this after changing the environment."""
    _catalogue.cache_clear()


def price_for(provider: str | None, model: str | None) -> Price | None:
    """The effective price for a model, environment overrides applied, or None."""
    base = _catalogue().get((str(provider), str(model))) if provider and model else None
    key = model_key(model)

    model_in = _env_float(f"LLM_PRICE_{key}_INPUT_PER_MILLION") if key else None
    model_out = _env_float(f"LLM_PRICE_{key}_OUTPUT_PER_MILLION") if key else None
    model_cached = _env_float(f"LLM_PRICE_{key}_CACHED_INPUT_PER_MILLION") if key else None
    model_free = _env_bool(f"LLM_PRICE_{key}_FREE_TIER") if key else None

    inp = model_in if model_in is not None else _env_float("LLM_PRICE_INPUT_PER_MILLION")
    out = model_out if model_out is not None else _env_float("LLM_PRICE_OUTPUT_PER_MILLION")
    cached = (
        model_cached
        if model_cached is not None
        else _env_float("LLM_PRICE_CACHED_INPUT_PER_MILLION")
    )
    free = model_free

    if base is None and inp is None and out is None and free is None:
        return None

    return Price(
        input_per_million=inp if inp is not None else (base.input_per_million if base else None),
        output_per_million=(
            out if out is not None else (base.output_per_million if base else None)
        ),
        cached_input_per_million=(
            cached
            if cached is not None
            else (base.cached_input_per_million if base else None)
        ),
        free_tier=free if free is not None else (base.free_tier if base else False),
    )


def estimate_cost(
    provider: str | None,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None = None,
) -> tuple[float | None, bool, str | None]:
    """Estimate one call's cost.

    Returns ``(cost_usd, pricing_known, source)``. ``source`` is ``"free_tier"``,
    ``"configured"`` or ``None`` when the cost is unknown.

    ``input_tokens`` already includes any cached tokens, so cached ones are
    removed from the billable input and charged at the cached rate (or the input
    rate when no cached rate is set). Reasoning tokens are not charged again:
    the provider already counts them inside ``output_tokens``.
    """
    price = price_for(provider, model)
    if price is None:
        return None, False, None
    if price.free_tier:
        return 0.0, True, "free_tier"
    if not price.known:
        return None, False, None
    if input_tokens is None and output_tokens is None:
        return None, False, None

    billable_input = max((input_tokens or 0) - (cached_input_tokens or 0), 0)
    cached_rate = price.cached_input_per_million
    if cached_rate is None:
        cached_rate = price.input_per_million
    cost = (
        billable_input * (price.input_per_million or 0.0)
        + (cached_input_tokens or 0) * (cached_rate or 0.0)
        + (output_tokens or 0) * (price.output_per_million or 0.0)
    ) / 1_000_000
    return round(cost, 8), True, "configured"
