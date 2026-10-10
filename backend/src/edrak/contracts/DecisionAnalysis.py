from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Literal

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
# QUESTION-LEVEL RECOMMENDATIONS
# ============================================================================

class RecommendationDetails(ContractModel):
    """A complete, actionable recommendation for one decision question."""

    decision: NonBlankStr = Field(
        description=(
            "A direct answer stating what the organization should do, "
            "should not do, or should do conditionally."
        )
    )

    rationale: NonBlankStr = Field(
        description=(
            "Evidence-based explanation of the recommendation, including "
            "trade-offs, uncertainties, and relevant limitations."
        )
    )

    actions: list[NonBlankStr] = Field(
        default_factory=list,
        description="Concrete steps to implement or validate the recommendation.",
    )
    opportunities: list[NonBlankStr] = Field(
        default_factory=list,
        description="Potential benefits and positive outcomes when supported.",
    )

    risks: list[NonBlankStr] = Field(
        default_factory=list,
        description="Relevant risks, dependencies, and mitigations.",
    )

    success_criteria: list[NonBlankStr] = Field(
        default_factory=list,
        description="Observable or measurable criteria for evaluating success.",
    )

    supporting_signal_ids: list[NonBlankStr] = Field(
        default_factory=list,
        description="IDs of supporting findings from Cross-Signal.",
    )
    limitations: list[NonBlankStr] = Field(
        default_factory=list,
        description="Known limitations of the recommendation and its supporting evidence.",
    )

    confidence: Literal["low", "medium", "high"] = Field(
        description="Confidence based on the strength and completeness of evidence."
    )


class QuestionRecommendation(ContractModel):
    """Pairs the original decision question with its recommendation."""

    question: NonBlankStr = Field(
        description="The original question supplied by Cross-Signal."
    )

    recommendation: RecommendationDetails


# ============================================================================
# INPUT
# ============================================================================

class DecisionAnalysisInput(ContractModel):
    """
    Canonical input for Decision Analysis.

    The agent consumes the original business request and Cross-Signal output.
    Cross-Signal is responsible for identifying relationships between
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

    Priority is distinct from evidence confidence. A high-impact issue may
    have limited evidence, and strong evidence does not automatically make
    an item high priority.
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
    success_indicators: list[NonBlankStr] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_human_approval: bool = True


# ============================================================================
# FINAL RESULT
# ============================================================================

class DecisionAnalysisResult(ContractModel):
    """
    Structured decision-support output for EDRAK.

    The agent provides recommendations to inform human decisions. It does
    not autonomously execute actions or make final business decisions.
    """

    analysis_id: NonBlankStr = Field(default_factory=new_id)
    research_run_id: NonBlankStr

    status: DecisionAnalysisStatus
    business_goal: NonBlankStr
    executive_summary: NonBlankStr

    # Each question is paired with a complete recommendation.
    question_recommendations: list[QuestionRecommendation] = Field(
        default_factory=list
    )

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
