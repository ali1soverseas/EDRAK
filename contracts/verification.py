from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel, NonBlankStr, utcnow
from .request import BusinessRequest
from .result import WorkerResult
from .task import WorkerType


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    RETRY_REQUIRED = "retry_required"
    REPLAN_REQUIRED = "replan_required"
    CANNOT_COMPLETE = "cannot_complete"


class FindingCheckStatus(str, Enum):
    VERIFIED = "verified"
    INSUFFICIENT = "insufficient"


class EvidenceQuality(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TargetedAction(ContractModel):
    worker: WorkerType = Field(description="Which worker to act on.")
    reason: NonBlankStr = Field(description="What action to take in plain language.")


class VerificationDecision(ContractModel):
    """Compact control-plane result used by the orchestrator for retry/replan."""

    status: VerificationStatus = Field(description="Verification outcome.")
    targeted_actions: list[TargetedAction] = Field(
        default_factory=list,
        description="Concrete actions to take if action is required.",
    )
    summary: NonBlankStr = Field(description="One-line explanation of the decision.")

    @model_validator(mode="after")
    def _actions_match_status(self) -> VerificationDecision:
        status = self.status
        action_count = len(self.targeted_actions)

        if status in (VerificationStatus.RETRY_REQUIRED, VerificationStatus.REPLAN_REQUIRED):
            if action_count < 1:
                raise ValueError(f"{status.value} requires at least one targeted_action")
            return self

        if action_count > 0:
            raise ValueError(
                f"{status.value} requires no targeted_actions, but got {action_count}"
            )
        return self


class VerificationInput(ContractModel):
    """All worker outputs plus the originating request, handed to verification."""

    request: BusinessRequest = Field(
        description="Originating business request. request_id is the research run id.",
    )
    agent_outputs: list[WorkerResult] = Field(
        description="WorkerResult from every research agent in the run.",
    )


class FindingVerdict(ContractModel):
    """Per-finding verification result consumed by synthesis."""

    finding_id: NonBlankStr = Field(description="finding_id from the source WorkerResult.")
    worker: WorkerType = Field(description="Which worker produced the finding.")
    statement: NonBlankStr = Field(description="The claim being verified.")
    verification_status: FindingCheckStatus = Field(description="Per-finding verdict.")
    evidence_quality: EvidenceQuality = Field(description="Highest quality of backing evidence.")
    evidence_ids: list[NonBlankStr] = Field(
        default_factory=list,
        description="Evidence ids inspected for this finding.",
    )
    contradictions: list[str] = Field(
        default_factory=list,
        description="Conflicts found in the backing evidence or across findings.",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="What is still needed to support the claim.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Verifier confidence in this verdict.",
    )


class ControlSummary(ContractModel):
    """Compact retry/replan brief. Does not carry raw documents."""

    missing_information: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    next_research_targets: list[str] = Field(default_factory=list)


class VerificationResult(ContractModel):
    """Full verification stage output.

    ``decision`` is the orchestrator control contract. ``findings`` are the
    verified/insufficient claims passed to synthesis.
    """

    research_run_id: NonBlankStr = Field(
        description="request_id of the originating BusinessRequest.",
    )
    decision: VerificationDecision
    findings: list[FindingVerdict] = Field(default_factory=list)
    control_summary: ControlSummary = Field(default_factory=ControlSummary)
    metadata: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=utcnow)
