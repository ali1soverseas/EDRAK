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

    @model_validator(mode="after")
    def _one_source_cannot_support_and_contradict(self) -> Finding:
        """Reject self-refuting claims.

        Without this a single evidence_id can appear as both SUPPORTS and
        CONTRADICTS, making is_supported and is_contradicted simultaneously
        true and leaving verification unable to decide which to trust.
        """
        relations: dict[str, set[EvidenceRelation]] = {}
        for ref in self.evidence_refs:
            relations.setdefault(ref.evidence_id, set()).add(ref.relation)

        clashing = sorted(
            evidence_id
            for evidence_id, seen in relations.items()
            if EvidenceRelation.SUPPORTS in seen
            and EvidenceRelation.CONTRADICTS in seen
        )
        if clashing:
            raise ValueError(
                f"evidence cannot both support and contradict the same finding: {clashing}"
            )
        return self


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

    @model_validator(mode="after")
    def _status_matches_payload(self) -> WorkerResult:
        """Reject statuses that contradict their own payload."""
        if self.status is WorkerStatus.FAILED and not (self.error or "").strip():
            raise ValueError("status='failed' requires a non-empty error")

        if self.status is WorkerStatus.NO_EVIDENCE and self.findings:
            raise ValueError(
                f"status='no_evidence' cannot carry findings: "
                f"{[f.finding_id for f in self.findings]}"
            )
        return self

    @model_validator(mode="after")
    def _conflicts_do_not_contradict_the_findings_own_support(self) -> WorkerResult:
        """A conflict must not refute evidence its own finding cites as support.

        Otherwise the same evidence_id is asserted on both sides and the conflict
        is decorative: nothing reconciles the two claims.
        """
        findings = {finding.finding_id: finding for finding in self.findings}

        for conflict in self.conflicts:
            finding = findings.get(conflict.finding_id)
            if finding is None:
                continue

            supported = {
                ref.evidence_id
                for ref in finding.evidence_refs
                if ref.relation is EvidenceRelation.SUPPORTS
            }
            clash = sorted(supported & set(conflict.contradicting_evidence_ids))
            if clash:
                raise ValueError(
                    f"conflict on {conflict.finding_id!r} refutes evidence the finding "
                    f"cites as support: {clash}"
                )
        return self

    def to_outcome(self) -> WorkerOutcome:
        return WorkerOutcome(
            task_id=self.task_id,
            worker=self.worker,
            status=self.status,
            attempt=self.attempt,
            error=self.error,
        )


class WorkerOutcome(ContractModel):
    """Control-plane view of a worker result.

    Orchestrator nodes may read only these fields. Findings and evidence must
    never be inspected by the orchestrator; they pass through untouched.
    """

    task_id: NonBlankStr = Field(description="task_id of the ResearchTask this answers.")
    worker: WorkerType = Field(description="Which worker produced this result.")
    status: WorkerStatus = Field(description="Outcome of the task.")
    attempt: int = Field(default=1, ge=1, description="Attempt number that produced this result.")
    error: str | None = Field(default=None, description="Failure detail when status is failed.")


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
    cross_signal: dict[str, Any] | None = Field(
        default=None,
        description="Validated CrossSignalOutput serialized as JSON, if verification succeeded.",
    )
    error: str | None = Field(default=None, description="Run-level failure detail.")
    completed_at: datetime = Field(
        default_factory=utcnow,
        description="When the run finished (UTC).",
    )

    @model_validator(mode="after")
    def _result_task_ids_are_unique(self) -> OrchestrationResult:
        """Duplicate task_id means one attempt silently displaced the other."""
        task_ids = [result.task_id for result in self.results]
        if len(set(task_ids)) != len(task_ids):
            duplicates = sorted({t for t in task_ids if task_ids.count(t) > 1})
            raise ValueError(f"duplicate task_id in results: {duplicates}")
        return self

    @model_validator(mode="after")
    def _results_are_covered_by_the_plan(self) -> OrchestrationResult:
        """A result no planned task produced is unattributable."""
        if self.plan is None:
            return self

        planned = {task.task_id for task in self.plan.tasks}
        orphans = sorted({r.task_id for r in self.results} - planned)
        if orphans:
            raise ValueError(f"results reference task_id(s) absent from the plan: {orphans}")
        return self

    @model_validator(mode="after")
    def _completed_run_has_no_failed_workers(self) -> OrchestrationResult:
        """status='completed' with a FAILED member is self-contradictory."""
        failed = [r.task_id for r in self.results if r.status is WorkerStatus.FAILED]
        if failed and self.status is RunStatus.COMPLETED:
            raise ValueError(
                f"status='completed' but {len(failed)} worker(s) failed: {sorted(failed)}"
            )
        return self