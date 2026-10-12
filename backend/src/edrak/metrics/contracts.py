"""Typed metric contracts shared by the planner, the workers and the brief pipeline.

A worker copies `value_text` from a source; code parses `value`. The language model never
writes or computes a number.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import Field

from edrak.contracts.base import ContractModel, NonBlankStr


class MetricScope(str, Enum):
    OFFICIAL = "official"
    THIRD_PARTY = "third_party"
    INTERNAL = "internal"
    PUBLIC_PROXY = "public_proxy_for_internal"


class MetricStatus(str, Enum):
    VERIFIED = "verified"
    THIN = "thin"
    CONFLICT = "conflict"
    UNVERIFIED = "unverified"


class OutcomeStatus(str, Enum):
    FOUND = "found"
    RETRY = "retry"
    GAP = "gap"


class GapReason(str, Enum):
    NOT_FOUND = "not_found"
    NOT_DISCLOSED = "not_publicly_disclosed"
    ONLY_THIRD_PARTY = "only_third_party_source"
    BELOW_MIN_SAMPLE = "below_min_sample"
    CONFLICT = "conflict"


class MetricObservation(ContractModel):
    """One number a worker found, tied to the evidence it came from."""

    metric_id: Optional[str] = None
    entity: NonBlankStr
    value_text: NonBlankStr = Field(description="Copied from the source, e.g. '$19 per user/month'.")
    value: Optional[float] = Field(default=None, description="Parsed from value_text by code.")
    unit: Optional[str] = None
    period: Optional[str] = None
    definition: Optional[str] = None
    evidence_id: NonBlankStr
    source_span: NonBlankStr = Field(description="Exact text of the source that holds the number.")
    scope: MetricScope = MetricScope.THIRD_PARTY
    sample_size: Optional[int] = None
    derived: bool = False
    formula: Optional[str] = None


class MetricRequirement(ContractModel):
    """What the planner asks a worker to find."""

    metric_id: NonBlankStr
    entity: NonBlankStr
    required: bool = True
    definition: Optional[str] = None
    preferred_source_types: list[str] = Field(default_factory=list)
    query_hints: list[str] = Field(default_factory=list)
    check_rule: Optional[str] = None


class RequirementOutcome(ContractModel):
    metric_id: NonBlankStr
    entity: NonBlankStr
    status: OutcomeStatus
    reason: Optional[GapReason] = None
    attempts: int = 1
