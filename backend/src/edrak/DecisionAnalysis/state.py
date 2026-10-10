from __future__ import annotations

from typing import Any, TypedDict

from ..contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
    DecisionAnalysisResult,
    DecisionAnalysisStatus,
    QuestionRecommendation,
)


class DecisionAnalysisState(TypedDict, total=False):
    """LangGraph state for EDRAK Decision Analysis."""

    # Canonical input contract
    input: DecisionAnalysisInput

    # Optional compatibility fields for existing integrations
    request: Any
    cross_signal: Any

    # Intermediate context used by the analysis node
    context: dict[str, Any]

    # Intermediate recommendation pairs
    question_recommendations: list[QuestionRecommendation]

    # Diagnostics and execution status
    warnings: list[str]
    status: DecisionAnalysisStatus

    # Canonical typed final output
    result: DecisionAnalysisResult