from __future__ import annotations

from pydantic import Field

from ..contracts.base import ContractModel, NonBlankStr
from ..contracts.DecisionAnalysis import (
    ActionTimeHorizon,
    AssessmentLevel,
    DecisionItemType,
    OpportunityAssessment,
    PriorityAssessment,
    PriorityBand,
    RecommendedAction,
    RiskAssessment,
)


class DecisionAnalysisDraft(ContractModel):
    """
    LLM-generated analytical draft.

    This is not returned directly to the orchestrator. The validation node
    checks the draft against the supplied Cross-Signal IDs first.
    """

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

    # Model-generated warnings are advisory. The application also generates
    # its own warnings during deterministic validation.
    warnings: list[NonBlankStr] = Field(default_factory=list)