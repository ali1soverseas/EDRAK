from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel, NonBlankStr, new_id, utcnow
from .evidence import Evidence, EvidenceRef, EvidenceRelation
from .task import ResearchPlan, WorkerType


class FindingCategory(str, Enum):
    PRODUCT_FEATURE = "product_feature"
    PRICING_PACKAGING = "pricing_packaging"
    POSITIONING = "positioning"
    TARGET_CUSTOMER = "target_customer"
    STRENGTH = "strength"
    GAP = "gap"
    MARKET_SIGNAL = "market_signal"
    CUSTOMER_SENTIMENT = "customer_sentiment"
    RISK = "risk"
    OPPORTUNITY = "opportunity"
    OTHER = "other"


class Finding(ContractModel):
    """One evidence-backed claim.

    Support and contradiction are derived from ``evidence_refs`` rather than
    stored, so they cannot drift away from the actual links.
    """

    finding_id: NonBlankStr = Field(
        default_factory=new_id,
        description="Unique finding identifier.",
    )
    statement: NonBlankStr = Field(description="The claim being made.")
    category: FindingCategory = Field(
        default=FindingCategory.OTHER,
        description="Dimension this claim speaks to.",
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list,
        description="Evidence backing or refuting this claim.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional self-reported confidence.",
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Known caveats attached to this claim.",
    )

    @property
    def evidence_ids(self) -> list[str]:
        return [ref.evidence_id for ref in self.evidence_refs]

    @property
    def is_supported(self) -> bool:
        return any(ref.relation is EvidenceRelation.SUPPORTS for ref in self.evidence_refs)

    @property
    def is_contradicted(self) -> bool:
        return any(ref.relation is EvidenceRelation.CONTRADICTS for ref in self.evidence_refs)


class WorkerStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    NO_EVIDENCE = "no_evidence"
    FAILED = "failed"


class Conflict(ContractModel):
    """A contradiction discovered between findings or between a finding and its evidence."""

    finding_id: NonBlankStr = Field(description="finding_id of the claim in question.")
    contradicting_evidence_ids: list[NonBlankStr] = Field(
        default_factory=list,
        description="Evidence that refutes the claim.",
    )
    description: NonBlankStr = Field(description="What the contradiction is.")


class WorkerResult(ContractModel):
    """The single contract every worker returns for a ResearchTask.

    Referential integrity is enforced here: every evidence reference and every
    conflict must resolve inside this result. Workers are therefore forced to
    carry provenance, and a dangling id fails at the boundary instead of
    surfacing later inside verification or synthesis.
    """

    task_id: NonBlankStr = Field(
        description="task_id of the ResearchTask this answers.",
    )
    worker: WorkerType = Field(description="Which worker produced this result.")
    status: WorkerStatus = Field(description="Outcome of the task.")
    attempt: int = Field(default=1, ge=1, description="Attempt number that produced this result.")

    findings: list[Finding] = Field(default_factory=list, description="Claims produced.")
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence collected.")
    gaps: list[str] = Field(default_factory=list, description="Known information gaps.")
    conflicts: list[Conflict] = Field(default_factory=list, description="Contradictions found.")

    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional overall confidence.",
    )
    started_at: datetime | None = Field(default=None, description="Start time (UTC).")
    completed_at: datetime | None = Field(default=None, description="Completion time (UTC).")
    error: str | None = Field(default=None, description="Failure detail when status is failed.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Worker-specific extras.")

    @model_validator(mode="after")
    def _identifiers_are_unique(self) -> WorkerResult:
        finding_ids = [finding.finding_id for finding in self.findings]
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("duplicate finding_id in findings")

        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("duplicate evidence_id in evidence")
        return self

    @model_validator(mode="after")
    def _references_resolve(self) -> WorkerResult:
        known_evidence = {item.evidence_id for item in self.evidence}
        known_findings = {finding.finding_id for finding in self.findings}

        for finding in self.findings:
            unknown = {ref.evidence_id for ref in finding.evidence_refs} - known_evidence
            if unknown:
                raise ValueError(
                    f"finding {finding.finding_id!r} references unknown evidence_id(s): "
                    f"{sorted(unknown)}"
                )

        for conflict in self.conflicts:
            if conflict.finding_id not in known_findings:
                raise ValueError(
                    f"conflict references unknown finding_id: {conflict.finding_id!r}"
                )
            unknown = set(conflict.contradicting_evidence_ids) - known_evidence
            if unknown:
                raise ValueError(
                    f"conflict on {conflict.finding_id!r} references unknown evidence_id(s): "
                    f"{sorted(unknown)}"
                )
        return self

    def to_outcome(self):  # type: ignore[no-untyped-def]
        from .worker import WorkerOutcome

        return WorkerOutcome(
            task_id=self.task_id,
            worker=self.worker,
            status=self.status,
            attempt=self.attempt,
            error=self.error,
        )


# Ensure WorkerOutcome can reference WorkerStatus after both classes are defined.
from .worker import WorkerOutcome  # noqa: E402, F401

try:
    WorkerOutcome.model_rebuild()
except Exception:
    pass


class RunStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class OrchestrationResult(ContractModel):
    """Terminal output of one orchestrator run.

    Carries worker results through to verification unchanged. The orchestrator
    itself reads only the control fields of each WorkerResult.
    """

    request_id: NonBlankStr = Field(
        description="request_id of the originating BusinessRequest.",
    )
    status: RunStatus = Field(description="Overall run outcome.")
    plan: ResearchPlan | None = Field(default=None, description="Plan that was executed.")
    results: list[WorkerResult] = Field(default_factory=list, description="Worker results collected.")
    error: str | None = Field(default=None, description="Run-level failure detail.")
    completed_at: datetime = Field(
        default_factory=utcnow,
        description="When the run finished (UTC).",
    )