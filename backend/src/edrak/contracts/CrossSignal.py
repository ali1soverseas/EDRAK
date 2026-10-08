from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from backend.src.edrak.contracts.task import WorkerType

from .base import ContractModel, NonBlankStr, new_id, utcnow
from .request import BusinessRequest
from .verification import (
    EvidenceQuality,
    FindingCheckStatus,
    FindingVerdict,
    VerificationResult,
    VerificationStatus,
)


class CrossSignalInput(ContractModel):
    """
    Input to the Cross-Signal Agent.

    Contains:
    - the research run
    - the original business request/context
    - only verified findings from Verification
    """

    research_run_id: NonBlankStr = Field(
        description="Identifier of the research run."
    )

    business_request: BusinessRequest = Field(
        description=(
            "Originating business request containing the "
            "business goal, company profile, and business context."
        )
    )

    verified_findings: list[FindingVerdict] = Field(
        default_factory=list,
        description=(
            "Only findings that passed Verification."
        ),
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Traceability metadata from Verification."
    )

    created_at: datetime = Field(
        default_factory=utcnow,
        description="Time this Cross-Signal input was created."
    )


def build_cross_signal_input(
    verification_result: VerificationResult,
    request: BusinessRequest,
) -> CrossSignalInput:
    """
    Convert VerificationResult into the input consumed
    by the Cross-Signal Agent.
    """

    if (
        verification_result.decision.status
        != VerificationStatus.VERIFIED
    ):
        raise ValueError(
            "Cross-Signal cannot run because verification "
            "did not complete successfully."
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


from enum import Enum


class SignalType(str, Enum):
    CONVERGENCE = "convergence"
    DIVERGENCE = "divergence"
    TENSION = "tension"
    DEPENDENCY = "dependency"
    TIMING = "timing"
    GAP = "gap"
    MOMENTUM = "momentum"


class SignalEvidence(ContractModel):
    finding_id: NonBlankStr = Field(
        description="Finding that supports this signal."
    )

    relevance: NonBlankStr = Field(
        description="Why this finding is relevant to the signal."
    )


class CrossSignal(ContractModel):
    signal_id: NonBlankStr = Field(
        default_factory=new_id
    )

    signal_type: SignalType

    title: NonBlankStr

    signal: NonBlankStr = Field(
        description=(
            "The cross-domain relationship identified "
            "from verified findings."
        )
    )

    interpretation: NonBlankStr = Field(
        description=(
            "What the relationship means strategically."
        )
    )

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


class CrossSignalSummary(ContractModel):
    dominant_patterns: list[NonBlankStr] = Field(
        default_factory=list
    )

    major_tensions: list[NonBlankStr] = Field(
        default_factory=list
    )

    important_dependencies: list[NonBlankStr] = Field(
        default_factory=list
    )

    timing_signals: list[NonBlankStr] = Field(
        default_factory=list
    )

    strategic_themes: list[NonBlankStr] = Field(
        default_factory=list
    )


class CrossSignalOutput(ContractModel):
    research_run_id: NonBlankStr

    status: NonBlankStr

    signals: list[CrossSignal] = Field(
        default_factory=list
    )

    summary: CrossSignalSummary

    input_statistics: dict[str, Any] = Field(
        default_factory=dict
    )

    created_at: datetime = Field(
        default_factory=utcnow
    )