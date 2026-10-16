"""Requirement check after each worker: found, retry or gap. Deterministic.

A requirement is `found` only when a typed observation matches it AND the registry's check
rule holds (an official source, a minimum sample). A third-party number never fulfils a metric
that must come from the entity itself.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from edrak.contracts import Evidence, EvidenceRef, Finding, WorkerResult
from edrak.contracts.task import ResearchTask
from edrak.metrics.contracts import (
    GapReason,
    MetricObservation,
    MetricRequirement,
    MetricScope,
    OutcomeStatus,
    RequirementOutcome,
)
from edrak.metrics.registry import get_registry

MAX_ATTEMPTS = 3  # the first run plus at most 2 retries per metric


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def observations_of(result: WorkerResult) -> list[MetricObservation]:
    out: list[MetricObservation] = []
    for raw in result.metadata.get("metrics") or []:
        try:
            out.append(raw if isinstance(raw, MetricObservation) else MetricObservation.model_validate(raw))
        except Exception:  # noqa: BLE001 - a malformed observation is skipped, never trusted
            continue
    return out


def _matches(obs: MetricObservation, req: MetricRequirement) -> bool:
    return obs.metric_id == req.metric_id and _norm(obs.entity) == _norm(req.entity)


def check(task: ResearchTask, result: WorkerResult) -> list[RequirementOutcome]:
    """One outcome per requirement on the task."""
    reg = get_registry()
    observations = observations_of(result)
    worker_said = {(o.get("metric_id"), _norm(o.get("entity", ""))): o for o in (result.metadata.get("requirement_outcomes") or [])
                   if isinstance(o, dict)}
    outcomes: list[RequirementOutcome] = []
    for req in task.requirements:
        defn = reg.get(req.metric_id)
        rule = defn.check if defn else {}
        mine = [o for o in observations if _matches(o, req)]
        attempts = task.attempt
        can_retry = attempts < MAX_ATTEMPTS

        def gap(reason: GapReason, retry: bool) -> RequirementOutcome:
            status = OutcomeStatus.RETRY if (retry and can_retry) else OutcomeStatus.GAP
            return RequirementOutcome(metric_id=req.metric_id, entity=req.entity, status=status,
                                      reason=reason, attempts=attempts)

        if not mine:
            said = worker_said.get((req.metric_id, _norm(req.entity)))
            if said and said.get("reason") == GapReason.NOT_DISCLOSED.value:
                outcomes.append(gap(GapReason.NOT_DISCLOSED, retry=False))
            else:
                outcomes.append(gap(GapReason.NOT_FOUND, retry=True))
            continue
        if rule.get("official_source_required") and not any(
            o.scope in (MetricScope.OFFICIAL, MetricScope.INTERNAL, MetricScope.PUBLIC_PROXY) for o in mine
        ):
            outcomes.append(gap(GapReason.ONLY_THIRD_PARTY, retry=True))
            continue
        coverage = (result.metadata.get("control_summary") or {}).get("coverage") or {}
        platforms = coverage.get("by_platform")
        if rule.get("min_platforms") and isinstance(platforms, dict) and len(platforms) < rule["min_platforms"]:
            outcomes.append(gap(GapReason.BELOW_MIN_SAMPLE, retry=False))
            continue
        need = rule.get("min_sample")
        if need and not any((o.sample_size or 0) >= need for o in mine):
            outcomes.append(gap(GapReason.BELOW_MIN_SAMPLE, retry=False))
            continue
        outcomes.append(RequirementOutcome(metric_id=req.metric_id, entity=req.entity,
                                           status=OutcomeStatus.FOUND, attempts=attempts))
    return outcomes


def needs_retry(outcomes: Iterable[RequirementOutcome]) -> list[RequirementOutcome]:
    return [o for o in outcomes if o.status is OutcomeStatus.RETRY]


def retry_task(task: ResearchTask, retry: list[RequirementOutcome]) -> ResearchTask:
    """The same task, asking only for what is still missing."""
    wanted = {(o.metric_id, _norm(o.entity)) for o in retry}
    reqs = [r for r in task.requirements if (r.metric_id, _norm(r.entity)) in wanted]
    names = "; ".join(f"{r.metric_id} for {r.entity}" for r in reqs[:8])
    return task.model_copy(update={
        "requirements": reqs,
        "attempt": task.attempt + 1,
        "goal": f"{task.goal} Find the still-missing figures: {names}.",
        "focus": f"{task.focus} Prefer the entity's own pages and filings for: {names}.",
    })


def merge_results(first: WorkerResult, again: WorkerResult) -> WorkerResult:
    """Fold a retry's result into the first so no earlier finding or source is lost."""
    if again.status.value == "failed":
        return first
    tag = f"a{again.attempt}-"
    evidence = list(first.evidence) + [e.model_copy(update={"evidence_id": tag + e.evidence_id}) for e in again.evidence]
    findings = list(first.findings)
    for f in again.findings:
        refs = [EvidenceRef(evidence_id=tag + r.evidence_id, relation=r.relation) for r in f.evidence_refs]
        findings.append(f.model_copy(update={"finding_id": tag + f.finding_id, "evidence_refs": refs}))
    meta = dict(first.metadata)
    metrics = list(first.metadata.get("metrics") or [])
    for m in again.metadata.get("metrics") or []:
        m = dict(m if isinstance(m, dict) else m.model_dump(mode="json"))
        m["evidence_id"] = tag + str(m.get("evidence_id", ""))
        metrics.append(m)
    meta["metrics"] = metrics
    for key, value in again.metadata.items():
        meta.setdefault(key, value)
    gaps = list(dict.fromkeys([*first.gaps, *again.gaps]))
    status = again.status if again.findings and not first.findings else first.status
    if first.status.value == "no_evidence" and findings:
        status = again.status
    return first.model_copy(update={
        "evidence": evidence, "findings": findings, "gaps": gaps, "metadata": meta,
        "status": status,
        "conflicts": list(first.conflicts) + [c for c in again.conflicts if False],
    })
