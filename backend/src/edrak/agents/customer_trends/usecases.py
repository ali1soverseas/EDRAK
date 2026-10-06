"""Typed view of config/use_cases.yaml."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from edrak.agents.customer_trends.schemas.common import Focus, NonEmpty, StrictModel, UseCase

DEFAULT_USE_CASES_PATH = Path(__file__).resolve().parent / "config" / "use_cases.yaml"


class ReviewRule(StrictModel):
    required_with_competitors: bool = False
    severity: Literal["critical", "minor"] = "minor"


class UseCaseConfig(StrictModel):
    emphasis: NonEmpty
    default_focus: list[Focus] = Field(min_length=1)
    min_evidence_total: int = Field(default=30, ge=1)
    min_platforms: int = Field(default=2, ge=1)
    min_platform_items: int = Field(default=20, ge=1)
    min_language_items: int = Field(default=10, ge=1)
    require_trend_series: bool = False
    reviews: ReviewRule = Field(default_factory=ReviewRule)
    query_hints: list[NonEmpty] = Field(default_factory=list)


class UseCases(StrictModel):
    competitive_intelligence: UseCaseConfig
    market_entry: UseCaseConfig
    product_launch: UseCaseConfig

    def for_use_case(self, use_case: UseCase) -> UseCaseConfig:
        config: UseCaseConfig = getattr(self, use_case.value)
        return config


def load_use_cases(path: Path | None = None) -> UseCases:
    text = (path or DEFAULT_USE_CASES_PATH).read_text(encoding="utf-8")
    return UseCases.model_validate(yaml.safe_load(text))
