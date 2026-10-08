"""State, settings and LLM-facing schemas for the Cross-Signal LangGraph agent."""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from pydantic import Field

from ..contracts.base import ContractModel
from ..contracts.CrossSignal import (
    CrossSignal,
    CrossSignalInput,
    CrossSignalOutput,
    CrossSignalSummary,
    SignalType,
)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CrossSignalSettings:
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_retries: int = 2
    # Fewer verified findings than this -> nothing cross-cutting can exist.
    min_findings: int = 2
    # Hard cap on signals returned (highest confidence kept).
    max_signals: int = 15
    # Every signal must span >= 2 domains, except these types.
    require_cross_domain: bool = True
    single_domain_ok: frozenset[SignalType] = field(
        default_factory=lambda: frozenset({SignalType.GAP})
    )


# --------------------------------------------------------------------------
# LLM-facing structured output schemas
# --------------------------------------------------------------------------
class SignalDetectionResult(ContractModel):
    """What one analysis lens returns."""

    signals: list[CrossSignal] = Field(
        default_factory=list,
        description="Cross-domain signals found through this lens. Empty if none are justified.",
    )


# --------------------------------------------------------------------------
# Graph state
# --------------------------------------------------------------------------
class CrossSignalState(TypedDict, total=False):
    # input
    input: CrossSignalInput

    # prepared by `prepare`
    findings_by_id: dict[str, dict[str, Any]]
    finding_domains: dict[str, str]
    findings_digest: str
    business_digest: str

    # fan-out results (reducer merges parallel lens outputs)
    candidate_signals: Annotated[list[CrossSignal], operator.add]
    warnings: Annotated[list[str], operator.add]

    # after validation / summary
    signals: list[CrossSignal]
    summary: CrossSignalSummary
    stats: dict[str, Any]
    status: str

    # final
    output: CrossSignalOutput


class LensState(TypedDict):
    """Payload sent to each parallel `detect_signals` branch via Send."""

    lens: str
    business_digest: str
    findings_digest: str
