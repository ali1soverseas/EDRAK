from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from .task import WorkerType

from .base import (
    ContractModel,
    NonBlankStr,
    new_id,
    utcnow,
)

from .request import BusinessRequest

from .verification import (
    EvidenceQuality,
    FindingCheckStatus,
    FindingVerdict,
    VerificationResult,
    VerificationStatus,
)


# ============================================================================
# CROSS-SIGNAL INPUT
# ============================================================================


class CrossSignalInput(ContractModel):
    """
    Input entering Cross-Signal.

    Only findings that passed Verification should be present here.
    """

    research_run_id: NonBlankStr

    business_request: BusinessRequest

    verified_findings: list[FindingVerdict] = Field(
        default_factory=list
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )

    created_at: datetime = Field(
        default_factory=utcnow
    )


# ============================================================================
# CROSS-SIGNAL INPUT BUILDER
# ============================================================================


def build_cross_signal_input(
    verification_result: VerificationResult,
    request: BusinessRequest,
) -> CrossSignalInput:
    """
    Convert Verification output into the canonical Cross-Signal input.

    Cross-Signal is not allowed to run against an unsuccessful
    verification stage.
    """

    if (
        verification_result.decision.status
        != VerificationStatus.VERIFIED
    ):
        raise ValueError(
            "Cross-Signal cannot run because verification "
            "did not complete successfully."
        )

    if any(
        finding.verification_status != FindingCheckStatus.VERIFIED
        for finding in verification_result.findings
    ):
        raise ValueError(
            "Cross-Signal cannot run because one or more findings "
            "did not pass verification."
        )

    verified_findings = [
        finding
        for finding in verification_result.findings
        if finding.verification_status
        == FindingCheckStatus.VERIFIED
    ]

    return CrossSignalInput(
        research_run_id=verification_result.research_run_id,
        business_request=request,
        verified_findings=verified_findings,
        metadata={
            "source_stage": "verification",
            "total_findings": len(
                verification_result.findings
            ),
            "verified_findings": len(
                verified_findings
            ),
            "verification_completed_at": (
                verification_result.completed_at.isoformat()
            ),
        },
    )


# ============================================================================
# SIGNAL TYPE
# ============================================================================


class SignalType(str, Enum):
    """
    Relationship discovered among verified findings.
    """

    CONVERGENCE = "convergence"

    DEPENDENCY = "dependency"

    TIMING = "timing"

    GAP = "gap"

    MOMENTUM = "momentum"


# ============================================================================
# DECISION RELEVANCE
# ============================================================================


class DecisionRelevance(str, Enum):
    """

    OPPORTUNITY_RELEVANT:
        The signal may represent a business opportunity.

    RISK_RELEVANT:
        The signal may represent a business risk.

    BOTH:
        The signal has both opportunity and risk implications.

    INFORMATIONAL:
        The signal matters for understanding the situation but
        does not clearly map to opportunity/risk.
    """

    OPPORTUNITY_RELEVANT = "opportunity_relevant"

    RISK_RELEVANT = "risk_relevant"

    BOTH = "both"

    INFORMATIONAL = "informational"


# ============================================================================
# URGENCY
# ============================================================================


class Urgency(str, Enum):

    IMMEDIATE = "immediate"

    HIGH = "high"

    MEDIUM = "medium"

    LOW = "low"

    UNKNOWN = "unknown"


# ============================================================================
# SIGNAL EVIDENCE
# ============================================================================


class SignalEvidence(ContractModel):
    """
    Reference to one verified finding supporting a Cross-Signal.

    `finding_id` should normally contain the exact canonical
    Verification finding ID.

    Cross-Signal's normalization layer may resolve F1/F2/... aliases
    if the LLM returns them.
    """

    finding_id: NonBlankStr

    relevance: NonBlankStr


# ============================================================================
# CROSS-SIGNAL
# ============================================================================


class CrossSignal(ContractModel):
    """
    A meaningful relationship among at least two verified findings.

    This object describes an observed relationship.

    It does NOT represent a recommendation.

    Recommendations/actions belong to the Decision stage.
    """

    signal_id: NonBlankStr = Field(
        default_factory=new_id
    )

    signal_type: SignalType

    title: NonBlankStr

    signal: NonBlankStr

    interpretation: NonBlankStr

    supporting_findings: list[SignalEvidence] = Field(
        min_length=2
    )

    domains_involved: list[WorkerType] = Field(
        min_length=1
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0
    )

    evidence_quality: EvidenceQuality

    strategic_relevance: NonBlankStr

    implications: list[NonBlankStr] = Field(
        default_factory=list
    )

    # ------------------------------------------------------------------
    # Decision relevance
    # ------------------------------------------------------------------

    decision_relevance: DecisionRelevance

    opportunity_relevance: str | None = Field(
        default=None
    )

    risk_relevance: str | None = Field(
        default=None
    )

    affected_business_areas: list[NonBlankStr] = Field(
        default_factory=list
    )

    decision_areas: list[NonBlankStr] = Field(
        default_factory=list
    )

    decision_question: NonBlankStr | None = Field(
        default=None
    )

    urgency: Urgency = Field(
        default=Urgency.UNKNOWN
    )

    dependencies: list[NonBlankStr] = Field(
        default_factory=list
    )

    evidence_gaps: list[NonBlankStr] = Field(
        default_factory=list
    )


# ============================================================================
# CROSS-SIGNAL SUMMARY
# ============================================================================


class CrossSignalSummary(ContractModel):

    dominant_patterns: list[NonBlankStr] = Field(
        default_factory=list
    )

    important_dependencies: list[NonBlankStr] = Field(
        default_factory=list
    )

    business_impacts: list[NonBlankStr] = Field(
        default_factory=list
    )

    timing_signals: list[NonBlankStr] = Field(
        default_factory=list
    )

    momentum_signals: list[NonBlankStr] = Field(
        default_factory=list
    )

    opportunity_relevant_signals: list[NonBlankStr] = Field(
        default_factory=list
    )

    risk_relevant_signals: list[NonBlankStr] = Field(
        default_factory=list
    )

    evidence_gaps: list[NonBlankStr] = Field(
        default_factory=list
    )

    strategic_themes: list[NonBlankStr] = Field(
        default_factory=list
    )


# ============================================================================
# DECISION-READY CONTEXT
# ============================================================================


class DecisionReadyContext(ContractModel):

    business_goal: NonBlankStr

    decision_areas: list[NonBlankStr] = Field(
        default_factory=list
    )

    key_signals: list[NonBlankStr] = Field(
        default_factory=list
    )

    opportunity_indicators: list[NonBlankStr] = Field(
        default_factory=list
    )

    risk_indicators: list[NonBlankStr] = Field(
        default_factory=list
    )

    dependencies: list[NonBlankStr] = Field(
        default_factory=list
    )

    timing_signals: list[NonBlankStr] = Field(
        default_factory=list
    )

    momentum_signals: list[NonBlankStr] = Field(
        default_factory=list
    )

    evidence_gaps: list[NonBlankStr] = Field(
        default_factory=list
    )

    decision_questions: list[NonBlankStr] = Field(
        default_factory=list
    )


# ============================================================================
# CROSS-SIGNAL OUTPUT
# ============================================================================


class CrossSignalOutput(ContractModel):

    research_run_id: NonBlankStr

    status: NonBlankStr

    signals: list[CrossSignal] = Field(
        default_factory=list
    )

    summary: CrossSignalSummary

    decision_ready_context: DecisionReadyContext

    input_statistics: dict[str, Any] = Field(
        default_factory=dict
    )

    warnings: list[NonBlankStr] = Field(
        default_factory=list
    )

    created_at: datetime = Field(
        default_factory=utcnow
    )