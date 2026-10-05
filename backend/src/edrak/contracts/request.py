from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from .base import ContractModel, NonBlankStr, new_id, utcnow


class UseCase(str, Enum):
    COMPETITIVE_INTELLIGENCE = "competitive_intelligence"
    MARKET_ENTRY_EXPANSION = "market_entry_expansion"
    PRODUCT_LAUNCH = "product_launch"


class TriggerType(str, Enum):
    ON_DEMAND = "on_demand"
    SCHEDULED = "scheduled"


class CompanyProfile(ContractModel):
    """Minimal baseline describing the company under analysis."""

    name: NonBlankStr = Field(
        description="Canonical name of the company under analysis."
    )

    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative names or commonly used company names.",
    )

    products: list[str] = Field(
        default_factory=list,
        description="Known products or offerings relevant to the analysis.",
    )

    notes: str | None = Field(
        default=None,
        description="Optional high-level company baseline notes.",
    )


class BusinessContext(ContractModel):
    """Context that frames how the business goal should be analyzed."""

    use_case: UseCase = Field(
        description="EDRAK MVP use case represented by this request."
    )

    targets: list[str] = Field(
        default_factory=list,
        description="Competitors, markets, products, or other analysis targets.",
    )

    focus_areas: list[str] = Field(
        default_factory=list,
        description="Dimensions the analysis should emphasize.",
    )

    trigger: TriggerType = Field(
        default=TriggerType.ON_DEMAND,
        description="How the analysis was initiated.",
    )

    time_window_days: int | None = Field(
        default=None,
        gt=0,
        description="Optional research recency window in days.",
    )

    constraints: list[str] = Field(
        default_factory=list,
        description="Explicit analysis constraints or requirements.",
    )


class BusinessRequest(ContractModel):
    """High-level business request entering the EDRAK orchestration workflow."""

    request_id: NonBlankStr = Field(
        default_factory=new_id,
        description="Unique request identifier.",
    )

    goal: NonBlankStr = Field(
        description="The main business question or objective EDRAK must address."
    )

    company_profile: CompanyProfile = Field(
        description="Baseline information about the company under analysis."
    )

    business_context: BusinessContext = Field(
        description="Context and framing parameters for the analysis."
    )

    extras: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional request-specific information not part of the core contract.",
    )

    created_at: datetime = Field(
        default_factory=utcnow,
        description="When the request was created in UTC.",
    )