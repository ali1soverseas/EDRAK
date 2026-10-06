"""Findings, the control summary and the worker's own result package."""

import re
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from edrak.agents.customer_trends.schemas.analysis import ThemeAggregate
from edrak.agents.customer_trends.schemas.common import (
    Confidence,
    FindingType,
    NonEmpty,
    RunId,
    StrictModel,
    UseCase,
    UtcDatetime,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem, to_shared_evidence
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.contracts import (
    EvidenceRef as SharedEvidenceRef,
)
from edrak.contracts import Finding as SharedFinding
from edrak.contracts import FindingCategory, ResearchTask, WorkerResult, WorkerStatus, WorkerType

SINGLE_SOURCE_CAVEAT = "single_source"
MAX_HEADLINE_WORDS = 60
MAX_FINDINGS = 20

_WESTERN = "0123456789"
_ARABIC_INDIC = "".join(chr(code) for code in range(0x0660, 0x066A))
_EXTENDED_ARABIC_INDIC = "".join(chr(code) for code in range(0x06F0, 0x06FA))
_ARABIC_DECIMAL = chr(0x066B)
_ARABIC_THOUSANDS = chr(0x066C)
_MINUS_SIGNS = "-" + chr(0x2212)
_TO_WESTERN = str.maketrans(
    _ARABIC_INDIC + _EXTENDED_ARABIC_INDIC + _ARABIC_DECIMAL + _ARABIC_THOUSANDS + chr(0x2212),
    _WESTERN + _WESTERN + ".,-",
)
_DIGIT = f"{_WESTERN}{_ARABIC_INDIC}{_EXTENDED_ARABIC_INDIC}"
_GROUPED = f"[{_DIGIT}]{{1,3}}(?:[,{_ARABIC_THOUSANDS}][{_DIGIT}]{{3}})+"
_PLAIN = f"[{_DIGIT}]+"
_FRACTION = f"(?:[.{_ARABIC_DECIMAL}][{_DIGIT}]+)?"
_NUMBER = re.compile(f"(?<![A-Za-z{_DIGIT}])(?:{_GROUPED}|{_PLAIN}){_FRACTION}")


def find_numbers(text: str) -> list[float]:
    """Numbers written in `text`, in order.

    Handles Western and Arabic-Indic digits, decimal points and commas as thousands
    separators. A percent sign is not part of the value ("45%" gives 45.0). Digits glued to
    a Latin letter (Q3, B2B, GPT4) are identifiers, not quantities, and are skipped.
    """
    found: list[float] = []
    for match in _NUMBER.finditer(text):
        raw = match.group().translate(_TO_WESTERN).replace(",", "")
        value = float(raw)
        start = match.start()
        if (
            start > 0
            and text[start - 1] in _MINUS_SIGNS
            and (start == 1 or not text[start - 2].isalnum())
        ):
            value = -value
        found.append(value)
    return found


class Finding(StrictModel):
    id: NonEmpty = Field(description="Short id, unique in this submission, for example f1.")
    type: FindingType = Field(description="What kind of finding this is.")
    claim: NonEmpty = Field(
        description=(
            "One or two sentences stating what the evidence shows. Every number in it must also "
            "be in metrics. No recommendations or verdicts."
        )
    )
    confidence: Confidence = Field(
        description=(
            "low, medium or high. High needs at least 10 evidence ids from at least 2 platforms "
            "or source types; a single evidence id is always low."
        )
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Ids of stored evidence items that support the claim, from evidence_query.",
    )
    metrics: dict[str, float | int | str] = Field(
        default_factory=dict,
        description=(
            "Every number quoted in the claim, copied as written, for example "
            '{"share_pct": 34.5}. Add "metric_id" to cite a compute_metrics result.'
        ),
    )
    caveats: list[str] = Field(default_factory=list, description="Limits of this finding.")
    related_gaps: list[str] = Field(
        default_factory=list, description="Coverage gaps that weaken this finding."
    )
    use_case_relevance: list[UseCase] = Field(
        default_factory=list, description="The use cases this finding matters for."
    )

    @model_validator(mode="after")
    def _apply_single_source_rule(self) -> Self:
        """Distinct ids only; fewer than two forces low confidence and a caveat.

        Rules that need the store (ids exist, platform spread, numbers in metrics) live in
        submit_findings.
        """
        self.evidence_ids = list(dict.fromkeys(self.evidence_ids))
        if len(self.evidence_ids) < 2:
            self.confidence = Confidence.LOW
            if SINGLE_SOURCE_CAVEAT not in self.caveats:
                self.caveats = [*self.caveats, SINGLE_SOURCE_CAVEAT]
        return self


class FindingsDraft(StrictModel):
    """The writer model's answer: findings that still have to pass `submit_findings`."""

    findings: list[Finding] = Field(default_factory=list, max_length=MAX_FINDINGS)


class HeadlineDraft(StrictModel):
    headline: NonEmpty


class ControlSummary(StrictModel):
    status: Literal["complete", "partial", "insufficient"]
    headline: str
    overall_confidence: Confidence
    findings_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    coverage: dict[str, Any] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    budget_used: dict[str, float] = Field(default_factory=dict)

    @field_validator("headline")
    @classmethod
    def _limit_headline(cls, headline: str) -> str:
        if len(headline.split()) > MAX_HEADLINE_WORDS:
            raise ValueError(f"headline must be at most {MAX_HEADLINE_WORDS} words")
        return headline


class EvidenceRef(StrictModel):
    store_path: NonEmpty
    run_id: RunId
    count: int = Field(ge=0)
    batch_ids: list[str] = Field(default_factory=list)


class CustomerTrendsResult(StrictModel):
    schema_version: str = "1.0"
    worker: Literal["customer_trends"] = "customer_trends"
    task_id: NonEmpty
    run_id: RunId
    brief: TaskBrief
    findings: list[Finding] = Field(default_factory=list)
    theme_aggregates: list[ThemeAggregate] = Field(default_factory=list)
    trend_series: list[TrendSeries] = Field(default_factory=list)
    evidence_ref: EvidenceRef
    gaps: list[str] = Field(default_factory=list)
    control_summary: ControlSummary
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDatetime


# CustomerTrendsResult to the shared WorkerResult (SPEC 6.0). The shared result is compact: the
# findings, the evidence they cite (a short fact each, not the stored text), the gaps and a
# pointer to this worker's full artifact. Metrics, themes and the control summary ride in
# `metadata`, because the shared finding has no field for them.

_CATEGORIES = {
    FindingType.PAIN_POINT: FindingCategory.CUSTOMER_SENTIMENT,
    FindingType.UNMET_NEED: FindingCategory.GAP,
    FindingType.DEMAND_SIGNAL: FindingCategory.MARKET_SIGNAL,
    FindingType.SENTIMENT: FindingCategory.CUSTOMER_SENTIMENT,
    FindingType.COMPETITOR_GAP: FindingCategory.GAP,
    FindingType.TREND: FindingCategory.MARKET_SIGNAL,
    FindingType.RISK: FindingCategory.RISK,
}
_CONFIDENCE = {Confidence.LOW: 0.3, Confidence.MEDIUM: 0.6, Confidence.HIGH: 0.85}
THEMES_IN_METADATA = 10
FAILED_PREFIX = "the run failed"


def worker_status(result: CustomerTrendsResult) -> tuple[WorkerStatus, str | None]:
    """The shared status of a result, and the error text a failed one needs."""
    summary = result.control_summary
    if result.provenance.get("ending") == "run_failed" and not result.findings:
        error = next((gap for gap in result.gaps if gap.startswith(FAILED_PREFIX)), FAILED_PREFIX)
        return WorkerStatus.FAILED, error
    if summary.status == "complete":
        return WorkerStatus.COMPLETED, None
    if summary.status == "insufficient" and not result.findings and summary.evidence_count == 0:
        return WorkerStatus.NO_EVIDENCE, None
    return WorkerStatus.PARTIAL, None


def to_worker_result(
    result: CustomerTrendsResult,
    evidence: Sequence[EvidenceItem],
    *,
    task: ResearchTask,
    location: str | None = None,
    synthetic: bool = False,
) -> WorkerResult:
    """The compact result for the orchestrator. `evidence` holds the stored items the findings cite;
    a reference to an item that is not given is left out rather than invented."""
    known = {item.id: item for item in evidence}
    findings = [
        SharedFinding(
            finding_id=f"{result.task_id}:{finding.id}",
            statement=finding.claim,
            category=_CATEGORIES[finding.type],
            evidence_refs=[
                SharedEvidenceRef(evidence_id=evidence_id)
                for evidence_id in finding.evidence_ids
                if evidence_id in known
            ],
            confidence=_CONFIDENCE[finding.confidence],
            limitations=[*finding.caveats, *(f"gap: {gap}" for gap in finding.related_gaps)],
        )
        for finding in result.findings
    ]
    cited = dict.fromkeys(ref.evidence_id for finding in findings for ref in finding.evidence_refs)
    summary = result.control_summary
    status, error = worker_status(result)
    return WorkerResult(
        task_id=task.task_id,
        worker=WorkerType.CUSTOMER_TRENDS,
        status=status,
        attempt=task.attempt,
        findings=findings,
        evidence=[
            to_shared_evidence(known[evidence_id], synthetic=synthetic) for evidence_id in cited
        ],
        gaps=list(result.gaps),
        confidence=None
        if status in {WorkerStatus.NO_EVIDENCE, WorkerStatus.FAILED}
        else _CONFIDENCE[summary.overall_confidence],
        started_at=result.created_at - timedelta(seconds=summary.budget_used.get("seconds", 0.0)),
        completed_at=result.created_at,
        error=error,
        metadata={
            "worker_schema_version": result.schema_version,
            "run_id": result.run_id,
            "control_summary": summary.model_dump(mode="json"),
            "artifact": {
                "result_location": location,
                "evidence_store": result.evidence_ref.store_path,
                "evidence_count": result.evidence_ref.count,
                "batch_ids": result.evidence_ref.batch_ids,
            },
            "finding_metrics": {
                f"{result.task_id}:{finding.id}": finding.metrics for finding in result.findings
            },
            "themes": [
                a.model_dump(
                    mode="json",
                    include={"theme_label", "count", "share", "sentiment_mix", "recent_growth"},
                )
                for a in result.theme_aggregates[:THEMES_IN_METADATA]
            ],
        },
    )
