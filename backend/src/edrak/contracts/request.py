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


class SocialPlatform(str, Enum):
    WEBSITE = "website"
    X = "x"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"


class SocialAccount(ContractModel):
    """One official account of a company, e.g. its X or Instagram profile."""

    platform: SocialPlatform = Field(description="Where the account lives.")
    url: NonBlankStr = Field(description="Full URL of the account or page.")


class CompetitorRef(ContractModel):
    """A company to compare against, with what is known about where it publishes."""

    name: NonBlankStr = Field(description="The competitor's name.")
    website: str | None = Field(default=None, description="Official website, when known.")
    social_accounts: list[SocialAccount] = Field(
        default_factory=list,
        description="Official social accounts, read from the competitor's own website.",
    )
    entity_id: str | None = Field(default=None, description="Watchlist entity id in the app.")
    discovered_by: str = Field(
        default="user",
        description="'user' when the person named it, 'agent' when Edrak found it.",
    )


class CompanyProfile(ContractModel):
    """Baseline describing the company under analysis (the user's own company)."""

    name: NonBlankStr = Field(
        description="Canonical name of the company under analysis."
    )

    aliases: list[NonBlankStr] = Field(
        default_factory=list,
        description="Alternative names or commonly used company names.",
    )

    products: list[NonBlankStr] = Field(
        default_factory=list,
        description="Known products or offerings relevant to the analysis.",
    )

    notes: str | None = Field(
        default=None,
        description="Optional high-level company baseline notes.",
    )

    website: str | None = Field(default=None, description="The company's official website.")

    social_accounts: list[SocialAccount] = Field(
        default_factory=list,
        description="The company's own social accounts. The Customer worker reads what people say there.",
    )

    workspace_id: str | None = Field(
        default=None,
        description="Workspace whose uploaded documents the Internal worker may search. "
        "When unset the whole knowledge base is searched.",
    )


class BusinessContext(ContractModel):
    """Context that frames how the business goal should be analyzed."""

    use_case: UseCase = Field(
        description="EDRAK MVP use case represented by this request."
    )

    targets: list[NonBlankStr] = Field(
        default_factory=list,
        description="Names of the competitors to compare against. Same names as `competitors`; "
        "kept for workers that read a plain list.",
    )

    competitors: list[CompetitorRef] = Field(
        default_factory=list,
        description="Companies to compare against, the same idea in every use case.",
    )

    markets: list[NonBlankStr] = Field(
        default_factory=list,
        description="Places the analysis is about: countries, regions or cities.",
    )

    offering: str | None = Field(
        default=None,
        description="The product or service being assessed or launched.",
    )

    target_customers: str | None = Field(
        default=None,
        description="Who the offering is for.",
    )

    focus_areas: list[NonBlankStr] = Field(
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

    constraints: list[NonBlankStr] = Field(
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