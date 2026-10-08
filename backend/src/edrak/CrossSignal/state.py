from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, TypedDict
import operator

from pydantic import Field

from ..contracts.base import ContractModel
from ..contracts.CrossSignal import (
    CrossSignal,
    CrossSignalInput,
    CrossSignalOutput,
    CrossSignalSummary,
    DecisionReadyContext,
)


# ============================================================================
# SETTINGS
# ============================================================================


@dataclass(frozen=True)
class CrossSignalSettings:
    """
    Runtime configuration for the Cross-Signal stage.

    Cross-Signal is intentionally conservative:
    - only verified findings enter the stage
    - each signal requires at least two distinct findings
    - the LLM identifies relationships
    - deterministic code handles normalization, reference resolution,
      deduplication, and output assembly
    """

    model: str = "gpt-4o-mini"

    temperature: float = 0.2

    max_retries: int = 2

    # Minimum number of verified findings required before analysis.
    min_findings: int = 2

    # Maximum final signals passed to Decision.
    max_signals: int = 6

    # Maximum signals requested from each analytical lens.
    max_signals_per_lens: int = 4

    # Maximum summary items.
    max_summary_items: int = 10

    # Diagnostics.
    max_unresolved_reference_examples: int = 10


# ============================================================================
# LLM OUTPUT CONTRACT
# ============================================================================


class SignalDetectionResult(ContractModel):
    """
    Structured output returned by the LLM for one Cross-Signal lens.

    The LLM is responsible for semantic relationship discovery.

    Deterministic code later verifies that:
    - supporting findings actually exist
    - at least two distinct findings support each signal
    - domains are derived from the actual findings
    - evidence quality is derived from the actual findings
    """

    signals: list[CrossSignal] = Field(
        default_factory=list,
        description=(
            "Meaningful relationships discovered among verified findings. "
            "Return an empty list when no sufficiently strong relationship "
            "is supported."
        ),
    )


# ============================================================================
# GRAPH STATE
# ============================================================================


class CrossSignalState(TypedDict, total=False):
    """
    Internal LangGraph state.

    Important distinction:

    `candidate_signals`
        Raw LLM-generated signals.

    `signals`
        Deterministically normalized, validated, deduplicated signals.

    `findings_by_id`
        Canonical mapping from actual Verification finding IDs.

    `finding_aliases`
        Human-readable F1/F2/... aliases mapped to canonical IDs.
    """

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    input: CrossSignalInput

    # ------------------------------------------------------------------
    # Finding indexes
    # ------------------------------------------------------------------

    findings_by_id: dict[str, Any]

    finding_aliases: dict[str, str]

    finding_domains: dict[str, str]

    # ------------------------------------------------------------------
    # Prompt digests
    # ------------------------------------------------------------------

    findings_digest: str

    business_digest: str

    # ------------------------------------------------------------------
    # Candidate / final signals
    # ------------------------------------------------------------------

    candidate_signals: Annotated[
        list[CrossSignal],
        operator.add,
    ]

    signals: list[CrossSignal]

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    warnings: Annotated[
        list[str],
        operator.add,
    ]

    stats: dict[str, Any]

    # ------------------------------------------------------------------
    # Final objects
    # ------------------------------------------------------------------

    summary: CrossSignalSummary

    decision_ready_context: DecisionReadyContext

    output: CrossSignalOutput

    status: str


# ============================================================================
# LENS STATE
# ============================================================================


class LensState(TypedDict):
    """
    Lightweight state representation for an individual analytical lens.

    Currently useful for future parallelization if the three lenses
    are eventually executed as separate graph branches.
    """

    lens: str
    business_digest: str
    findings_digest: str