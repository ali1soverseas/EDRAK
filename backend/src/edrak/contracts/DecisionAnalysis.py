from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from .base import (
    ContractModel,
    NonBlankStr,
    new_id,
    utcnow,
)
from .request import BusinessRequest
from .CrossSignal import CrossSignalOutput


# ============================================================================
# ENUMS
# ============================================================================


class DecisionAnalysisStatus(str, Enum):
    COMPLETED = "completed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class PriorityBand(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNDETERMINED = "undetermined"


class AssessmentLevel(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    UNKNOWN = "unknown"


class DecisionItemType(str, Enum):
    OPPORTUNITY = "opportunity"
    RISK = "risk"
    ACTION = "action"


class ActionTimeHorizon(str, Enum):
    IMMEDIATE = "immediate"
    SHORT_TERM = "short_term"
    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"
    UNDETERMINED = "undetermined"


# ============================================================================
# INPUT
# ============================================================================


class DecisionAnalysisInput(ContractModel):
    """
    Canonical input for Decision Analysis.

    The agent consumes Cross-Signal output and the original business request.
    Cross-Signal remains responsible for discovering relationships between
    verified findings.
    """

    research_run_id: NonBlankStr
    business_request: BusinessRequest
    cross_signal: CrossSignalOutput

    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


# ============================================================================
# OPPORTUNITY ASSESSMENT
# ============================================================================


class OpportunityAssessment(ContractModel):
    opportunity_id: NonBlankStr = Field(default_factory=new_id)

    title: NonBlankStr
    description: NonBlankStr
    business_rationale: NonBlankStr

    potential_impact: NonBlankStr
    strategic_fit: AssessmentLevel
    feasibility: AssessmentLevel
    urgency: AssessmentLevel

    supporting_signal_ids: list[NonBlankStr] = Field(
        default_factory=list,
        min_length=1,
    )

    assumptions: list[NonBlankStr] = Field(default_factory=list)
    evidence_gaps: list[NonBlankStr] = Field(default_factory=list)

    confidence: float = Field(ge=0.0, le=1.0)


# ============================================================================
# RISK ASSESSMENT
# ============================================================================


class RiskAssessment(ContractModel):
    risk_id: NonBlankStr = Field(default_factory=new_id)

    title: NonBlankStr
    description: NonBlankStr
    business_rationale: NonBlankStr

    potential_consequences: list[NonBlankStr] = Field(
        default_factory=list
    )

    likelihood: AssessmentLevel
    impact: AssessmentLevel
    urgency: AssessmentLevel

    supporting_signal_ids: list[NonBlankStr] = Field(
        default_factory=list,
        min_length=1,
    )

    dependencies: list[NonBlankStr] = Field(default_factory=list)
    mitigation_considerations: list[NonBlankStr] = Field(
        default_factory=list
    )
    evidence_gaps: list[NonBlankStr] = Field(default_factory=list)

    confidence: float = Field(ge=0.0, le=1.0)


# ============================================================================
# PRIORITIZATION
# ============================================================================


class PriorityAssessment(ContractModel):
    """
    Relative prioritization with an explicit rationale.

    Priority is not the same as evidence confidence:
    a high-impact issue can have limited evidence, and vice versa.
    """

    item_type: DecisionItemType
    item_id: NonBlankStr

    priority: PriorityBand

    strategic_fit: AssessmentLevel
    impact: AssessmentLevel
    urgency: AssessmentLevel
    feasibility: AssessmentLevel

    rationale: NonBlankStr
    key_uncertainties: list[NonBlankStr] = Field(
        default_factory=list
    )


# ============================================================================
# RECOMMENDED ACTIONS
# ============================================================================


class RecommendedAction(ContractModel):
    action_id: NonBlankStr = Field(default_factory=new_id)

    title: NonBlankStr
    description: NonBlankStr
    rationale: NonBlankStr

    intended_outcome: NonBlankStr

    priority: PriorityBand
    time_horizon: ActionTimeHorizon

    supporting_signal_ids: list[NonBlankStr] = Field(
        default_factory=list
    )

    related_opportunity_ids: list[NonBlankStr] = Field(
        default_factory=list
    )
    related_risk_ids: list[NonBlankStr] = Field(
        default_factory=list
    )

    prerequisites: list[NonBlankStr] = Field(default_factory=list)
    success_indicators: list[NonBlankStr] = Field(
        default_factory=list
    )

    confidence: float = Field(ge=0.0, le=1.0)

    requires_human_approval: bool = True


# ============================================================================
# FINAL RESULT
# ============================================================================


class DecisionAnalysisResult(ContractModel):
    """
    Structured decision-support output for EDRAK.

    This output informs human decisions; it does not execute actions or make
    autonomous business decisions.
    """

    analysis_id: NonBlankStr = Field(default_factory=new_id)
    research_run_id: NonBlankStr

    status: DecisionAnalysisStatus

    business_goal: NonBlankStr
    executive_summary: NonBlankStr

    opportunities: list[OpportunityAssessment] = Field(
        default_factory=list
    )
    risks: list[RiskAssessment] = Field(default_factory=list)

    priorities: list[PriorityAssessment] = Field(
        default_factory=list
    )
    recommended_actions: list[RecommendedAction] = Field(
        default_factory=list
    )

    decision_questions: list[NonBlankStr] = Field(
        default_factory=list
    )
    additional_information_needed: list[NonBlankStr] = Field(
        default_factory=list
    )

    limitations: list[NonBlankStr] = Field(default_factory=list)
    warnings: list[NonBlankStr] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=utcnow)