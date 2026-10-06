"""Findings, the control summary and the worker's own result package."""

import re
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
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.schemas.trends import TrendSeries

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
