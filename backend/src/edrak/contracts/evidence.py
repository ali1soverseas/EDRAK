from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel, NonBlankStr, new_id, utcnow


class SourceType(str, Enum):
    WEB_PAGE = "web_page"
    SEARCH_RESULT = "search_result"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    PRICING_PAGE = "pricing_page"
    RELEASE_NOTES = "release_notes"
    ANNOUNCEMENT = "announcement"
    NEWS_ARTICLE = "news_article"
    REVIEW_SITE = "review_site"
    MARKET_REPORT = "market_report"
    REGULATORY = "regulatory"
    ECONOMIC = "economic"
    INTERNAL_DOCUMENT = "internal_document"
    SYNTHETIC_INTERNAL = "synthetic_internal"
    OTHER = "other"


class EvidenceRelation(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXTUALIZES = "contextualizes"


class Evidence(ContractModel):
    """Traceable support for one or more findings.

    Workers are the only producers. Verification and synthesis consume it;
    the orchestrator must treat it as opaque.
    """

    evidence_id: NonBlankStr = Field(
        default_factory=new_id,
        description="Unique evidence identifier.",
    )
    source_type: SourceType = Field(description="Nature of the source.")
    source_title: str | None = Field(default=None, description="Title of the source.")
    source_url: str | None = Field(default=None, description="URL when the source is public.")
    publisher: str | None = Field(default=None, description="Who published the source.")
    extracted_fact: NonBlankStr = Field(description="The specific fact drawn from the source.")
    excerpt: str | None = Field(default=None, description="Supporting passage from the source.")
    retrieved_at: datetime = Field(
        default_factory=utcnow,
        description="When the source was retrieved (UTC).",
    )
    is_synthetic: bool = Field(
        default=False,
        description="True when the content is synthetic or adapted, not authentic.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific extras such as pricing tier or publish date.",
    )

    @model_validator(mode="after")
    def _synthetic_sources_must_be_flagged(self) -> Evidence:
        if self.source_type is SourceType.SYNTHETIC_INTERNAL and not self.is_synthetic:
            raise ValueError(
                "source_type='synthetic_internal' requires is_synthetic=True; "
                "synthetic internal data must never be presented as authentic"
            )
        return self


class EvidenceRef(ContractModel):
    """A finding's link to one piece of evidence, carrying what the link means.

    A bare id list cannot distinguish "this source backs the claim" from "this
    source refutes it", so the relation travels with the reference.
    """

    evidence_id: NonBlankStr = Field(description="evidence_id of the referenced Evidence.")
    relation: EvidenceRelation = Field(
        default=EvidenceRelation.SUPPORTS,
        description="How this evidence relates to the finding.",
    )