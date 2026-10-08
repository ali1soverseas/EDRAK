from __future__ import annotations

from typing import Any, TypedDict

from ..contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
    DecisionAnalysisResult,
)
from .schemas import DecisionAnalysisDraft


class DecisionAnalysisState(TypedDict, total=False):
    # Input
    input: DecisionAnalysisInput

    # Intermediate state
    prepared_context: dict[str, Any]
    draft: DecisionAnalysisDraft

    # Final state
    result: DecisionAnalysisResult

    # Error handling
    warnings: list[str]
    error: str | None