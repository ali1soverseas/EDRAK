"""submit_findings: check findings against the evidence store, then keep the sound ones.

A finding is a claim plus the evidence behind it. The model writes the claim; this module
checks what code can check: the cited evidence exists, a "high" confidence has the spread it
needs, every number in the claim is one that was computed, and the claim states what the
evidence shows without telling the reader what to decide.
"""

import time
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from edrak.agents.customer_trends.schemas.analysis import MetricResult
from edrak.agents.customer_trends.schemas.common import (
    Confidence,
    ProcessingResponse,
    RunScope,
    StrictModel,
    ToolResponse,
    ToolStatus,
)
from edrak.agents.customer_trends.schemas.findings import Finding, find_numbers
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools.base import ToolContext, ToolSpec, finish_processing
from edrak.agents.customer_trends.utils.text import phrase_pattern, search_key

VERDICT_PHRASES_PATH = Path(__file__).resolve().parent.parent / "config" / "verdict_phrases.yaml"
MAX_FINDINGS = 20
HIGH_MIN_EVIDENCE = 10
HIGH_MIN_SPREAD = 2
RELATIVE_TOLERANCE = 0.01
ROUNDING_HALF = 0.5
METRIC_ID_KEY = "metric_id"
YEAR_RANGE = (1900, 2100)
LISTED_IDS = 5
DOWNGRADE_CAVEAT = (
    "confidence lowered from high: high needs at least 10 evidence items from at least 2 "
    "platforms or source types"
)

DESCRIPTION = """Use it to hand in your findings once the analysis is done. Each is checked, and
the sound ones are stored as the worker's result; you get back which were accepted and why any
were not. Do NOT use it for notes or drafts: whatever is accepted becomes part of the result.

A finding states what the evidence shows: a claim of one or two sentences, its type, a
confidence, the ids of the stored evidence items behind it (from evidence_query) and a metrics
object. Rules:
- every evidence id must be a stored item of this run;
- every number in the claim must appear in `metrics`, copied as written (45% is 45), or in a
  compute_metrics result cited as "metric_id" in `metrics`. Years are not counted as numbers;
- a claim must not recommend or decide ("should enter", "do not launch"): describe, do not
  advise;
- one evidence id means low confidence; high confidence needs at least 10 evidence ids from at
  least 2 platforms or source types, otherwise it is lowered to medium.

A rejected finding can be fixed and sent again with the same id; an accepted one is replaced
when its id is sent again. Send at most 20 findings per call.

Example: {"findings": [{"id": "f1", "type": "pain_point",
"claim": "Slow support is raised in 34.5% of analyzed posts.", "confidence": "medium",
"evidence_ids": ["0123456789abcdef", "fedcba9876543210"], "metrics": {"share_pct": 34.5},
"caveats": ["Reddit and X only"]}]}"""


class SubmitFindingsInput(RunScope):
    findings: list[Finding] = Field(
        min_length=1, max_length=MAX_FINDINGS, description="The findings to check and store."
    )


class RejectedFinding(StrictModel):
    id: str
    reasons: list[str]


class AdjustedFinding(StrictModel):
    id: str
    confidence: Confidence
    reasons: list[str]


class SubmitFindingsResponse(ProcessingResponse):
    accepted: list[str] = Field(default_factory=list)
    rejected: list[RejectedFinding] = Field(default_factory=list)
    adjusted: list[AdjustedFinding] = Field(default_factory=list)


def load_verdict_phrases(path: Path | None = None) -> list[str]:
    data = yaml.safe_load((path or VERDICT_PHRASES_PATH).read_text(encoding="utf-8"))
    return [str(phrase) for phrase in data["phrases"]]


def verdict_phrases_in(claim: str, phrases: Iterable[str]) -> list[str]:
    """The listed phrases the claim contains, ignoring case and Arabic spelling variants."""
    text = search_key(claim)
    return [phrase for phrase in phrases if phrase_pattern(phrase).search(text)]


def _is_year(value: float) -> bool:
    return value.is_integer() and YEAR_RANGE[0] <= value <= YEAR_RANGE[1]


def claim_numbers(claim: str) -> list[float]:
    """The quantities a claim states. A four digit year is a date, not a quantity."""
    return [value for value in find_numbers(claim) if not _is_year(value)]


def _decimals(value: float) -> int:
    if value.is_integer():
        return 0
    exponent = Decimal(repr(value)).as_tuple().exponent
    return max(0, -int(exponent))


def matches(claimed: float, known: float) -> bool:
    """True when `claimed` is `known` to within 1 percent, or within rounding at the number of
    decimals the claim shows (0.5 for a whole number, 0.05 for one decimal, and so on)."""
    rounding = ROUNDING_HALF * 10.0 ** -_decimals(claimed)
    return abs(claimed - known) <= max(rounding, RELATIVE_TOLERANCE * abs(known))


def _flatten(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        yield float(value)
    elif isinstance(value, dict):
        for inner in value.values():
            yield from _flatten(inner)
    elif isinstance(value, list | tuple):
        for inner in value:
            yield from _flatten(inner)


def cited_metric_ids(metrics: dict[str, float | int | str]) -> list[str]:
    return [
        value
        for key, value in metrics.items()
        if isinstance(value, str) and (key == METRIC_ID_KEY or key.endswith(f"_{METRIC_ID_KEY}"))
    ]


def own_numbers(metrics: dict[str, float | int | str]) -> list[float]:
    """The numbers a finding lists in its metrics, in numeric and in text values."""
    ids = set(cited_metric_ids(metrics))
    numbers: list[float] = []
    for value in metrics.values():
        if isinstance(value, str):
            if value not in ids:
                numbers.extend(find_numbers(value))
        elif not isinstance(value, bool):
            numbers.append(float(value))
    return numbers


def _listed(ids: list[str]) -> str:
    shown = ", ".join(ids[:LISTED_IDS])
    return shown + (f" (and {len(ids) - LISTED_IDS} more)" if len(ids) > LISTED_IDS else "")


def check_finding(
    finding: Finding,
    store: EvidenceStore,
    run_id: str,
    phrases: list[str],
) -> list[str]:
    """Why the finding cannot be stored; empty when it can."""
    reasons: list[str] = []
    if not finding.evidence_ids:
        reasons.append("evidence_ids is empty: cite the stored items that support the claim")
    else:
        found = store.existing_ids(run_id, finding.evidence_ids)
        missing = [i for i in finding.evidence_ids if i not in found]
        if missing:
            reasons.append(
                f"evidence ids not found in this run: {_listed(missing)}; "
                "use ids returned by evidence_query"
            )
    known = own_numbers(finding.metrics)
    stored: list[MetricResult] = []
    for metric_id in cited_metric_ids(finding.metrics):
        record = store.get_metric(run_id, metric_id)
        if record is None:
            reasons.append(f"metric_id '{metric_id}' does not exist in this run")
        else:
            stored.append(record)
            known.extend(_flatten(record.values))
    unmatched = [n for n in claim_numbers(finding.claim) if not any(matches(n, k) for k in known)]
    if unmatched:
        listed = ", ".join(f"{n:g}" for n in dict.fromkeys(unmatched))
        reasons.append(
            f"numbers in the claim are not in metrics: {listed}; copy each into metrics as "
            "written (45% is 45), or cite a compute_metrics result as metric_id"
        )
    verdicts = verdict_phrases_in(finding.claim, phrases)
    if verdicts:
        reasons.append(
            f"the claim contains verdict language ({', '.join(repr(v) for v in verdicts)}): "
            "state what the evidence shows and leave the decision to the reader"
        )
    return reasons


def confidence_adjustment(finding: Finding, store: EvidenceStore, run_id: str) -> Finding:
    """Lower an unearned `high` to `medium` with a caveat."""
    if finding.confidence is not Confidence.HIGH:
        return finding
    items = store.get_items(run_id, finding.evidence_ids)
    platforms = {item.platform for item in items if item.platform is not None}
    kinds = {item.source_type for item in items}
    spread = max(len(platforms), len(kinds))
    if len(finding.evidence_ids) >= HIGH_MIN_EVIDENCE and spread >= HIGH_MIN_SPREAD:
        return finding
    return finding.model_copy(
        update={
            "confidence": Confidence.MEDIUM,
            "caveats": [*finding.caveats, DOWNGRADE_CAVEAT],
        }
    )


def _submit(ctx: ToolContext, inp: SubmitFindingsInput) -> SubmitFindingsResponse:
    phrases = load_verdict_phrases()
    accepted: list[Finding] = []
    rejected: list[RejectedFinding] = []
    adjusted: list[AdjustedFinding] = []
    seen: set[str] = set()
    for finding in inp.findings:
        reasons = check_finding(finding, ctx.store, ctx.run_id, phrases)
        if finding.id in seen:
            reasons.append("duplicate id in this submission; every finding needs its own id")
        seen.add(finding.id)
        if reasons:
            rejected.append(RejectedFinding(id=finding.id, reasons=reasons))
            continue
        final = confidence_adjustment(finding, ctx.store, ctx.run_id)
        notes = []
        if len(finding.evidence_ids) < HIGH_MIN_SPREAD:
            notes.append("one evidence id only: confidence is low and marked single_source")
        if final.confidence is not finding.confidence:
            notes.append(DOWNGRADE_CAVEAT)
        if notes:
            adjusted.append(
                AdjustedFinding(id=final.id, confidence=final.confidence, reasons=notes)
            )
        accepted.append(final)
    if accepted:
        ctx.store.save_findings(ctx.run_id, accepted)
    return SubmitFindingsResponse(
        status=ToolStatus.PARTIAL if rejected else ToolStatus.OK,
        count=len(accepted),
        accepted=[f.id for f in accepted],
        rejected=rejected,
        adjusted=adjusted,
    )


async def submit_findings(
    ctx: ToolContext, inp: SubmitFindingsInput
) -> ToolResponse | ProcessingResponse:
    started = time.perf_counter()
    return finish_processing(ctx, "submit_findings", inp, _submit(ctx, inp), started)


SPEC = ToolSpec("submit_findings", DESCRIPTION, SubmitFindingsInput, submit_findings)
