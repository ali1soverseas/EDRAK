"""Typed view of config/providers.yaml."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, model_validator

from edrak.agents.customer_trends.providers.base import parse_capability
from edrak.agents.customer_trends.schemas.common import StrictModel

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "providers.yaml"


class ProviderConfig(StrictModel):
    min_interval_s: float = Field(default=0.0, ge=0)
    max_per_request: int | None = Field(default=None, ge=1)
    cost_per_call_usd: float = Field(default=0.0, ge=0)
    verify: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class ProvidersConfig(StrictModel):
    providers: dict[str, ProviderConfig]
    routing: dict[str, list[str]]

    @model_validator(mode="after")
    def _check_routing(self) -> "ProvidersConfig":
        for capability, names in self.routing.items():
            parse_capability(capability)
            unknown = [name for name in names if name not in self.providers]
            if unknown:
                raise ValueError(f"routing for {capability!r} names unknown providers: {unknown}")
        return self


def load_providers_config(path: Path | None = None) -> ProvidersConfig:
    text = (path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8")
    return ProvidersConfig.model_validate(yaml.safe_load(text))
