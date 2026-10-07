from __future__ import annotations

from edrak.contracts import (
    ControlSummary,
    Evidence,
    EvidenceQuality,
    EvidenceRelation,
    Finding,
    FindingCheckStatus,
    FindingVerdict,
    TargetedAction,
    VerificationDecision,
    VerificationInput,
    VerificationResult,
    VerificationStatus,
    WorkerResult,
    WorkerType,
    utcnow,
)
from edrak.verification.prompts import review_prompt
from edrak.verification.quality import (
    MIN_CONFIDENCE,
    best_quality,
    coverage_gaps,
    dedupe_evidence,
    price_conflicts,
    quantities_missing,
    support_strength,
    vendor_hosts,
)
from edrak.verification.state import VerificationState

_MAX_ACTIONS_PER_WORKER = 5


def _evidence_by_id(result: WorkerResult) -> dict[str, Evidence]:
    return {item.evidence_id: item for item in result.evidence}


def _resolved_evidence(finding: Finding, catalog: dict[str, Evidence]) -> list[Evidence]:
    linked = [catalog[ref.evidence_id] for ref in finding.evidence_refs if ref.evidence_id in catalog]
    return dedupe_evidence(linked)


def _supporting(finding: Finding, evidence: list[Evidence]) -> list[Evidence]:
    supporting_ids = {
        ref.evidence_id
        for ref in finding.evidence_refs
        if ref.relation is EvidenceRelation.SUPPORTS
    }
    return [item for item in evidence if item.evidence_id in supporting_ids]


def _is_complete(finding: Finding, evidence: list[Evidence], strength: str) -> bool:
    supporting = _supporting(finding, evidence)
    if not supporting:
        return False
    return strength == "yes"


def _recorded_contradictions(finding: Finding, result: WorkerResult) -> list[str]:
    notes: list[str] = []
    if finding.is_contradicted:
        notes.append("At least one linked source contradicts the claim.")
    for conflict in result.conflicts:
        if conflict.finding_id == finding.finding_id:
            notes.append(conflict.description)
    return notes


def _source_packet(evidence: list[Evidence]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(evidence[:4], start=1):
        body = (item.excerpt or item.extracted_fact or "").strip()[:1500]
        blocks.append(
            f"[{index}] type={item.source_type.value} "
            f"title={item.source_title or ''} url={item.source_url or ''}\n{body}"
        )
    return "\n\n".join(blocks) or "(no source text saved)"


def _llm_review(statement: str, evidence: list[Evidence]) -> dict | None:
    """Ground the claim in saved source text. None means keep the deterministic result."""
    from edrak.core.llm import get_llm_client

    try:
        data = get_llm_client().chat_structured(
            [{"role": "user", "content": review_prompt(statement, _source_packet(evidence))}],
            temperature=0,
        )
    except Exception as exc:
        print(f"WARNING: verification LLM review failed: {exc}")
        return None

    supported = data.get("supported")
    if not isinstance(supported, bool):
        print("WARNING: verification LLM review returned no supported flag")
        return None

    contradictions = [
        str(item).strip()
        for item in (data.get("contradictions") or [])
        if str(item).strip()
    ]
    invented = [
        str(item).strip()
        for item in (data.get("invented_details") or [])
        if str(item).strip()
    ]
    return {"supported": supported, "contradictions": contradictions, "invented": invented}


def _confidence_too_low(finding: Finding) -> bool:
    return finding.confidence is not None and finding.confidence < MIN_CONFIDENCE


def _should_call_llm(
    finding: Finding,
    quality: EvidenceQuality,
    strength: str,
) -> bool:
    if _confidence_too_low(finding):
        return False
    if quality is EvidenceQuality.LOW:
        return False
    if strength == "yes":
        return False
    if strength == "no":
        return False
    return True


def _missing_information(
    finding: Finding,
    evidence: list[Evidence],
    quality: EvidenceQuality,
    invented: list[str],
    strength: str,
) -> list[str]:
    missing: list[str] = []
    if _confidence_too_low(finding):
        missing.append(f"Worker confidence below {MIN_CONFIDENCE:.0%}.")
    if not evidence:
        missing.append("No evidence was attached to the claim.")
    missing.extend(
        f"Claim quantity {token} is not in the saved source."
        for token in quantities_missing(finding.statement, evidence)
    )
    if invented:
        missing.append("Claim includes details that are not in the saved source: " + "; ".join(invented))
    if quality is EvidenceQuality.LOW and evidence:
        missing.append("Named official, analyst, or news source beyond this URL class.")
    if strength == "no" and evidence and not quantities_missing(finding.statement, evidence):
        missing.append("Saved source does not contain the claim.")
    if strength == "uncertain":
        missing.append("Source overlap is too weak to confirm the claim without a closer read.")
    return list(dict.fromkeys(missing))


def _confidence(
    status: FindingCheckStatus,
    quality: EvidenceQuality,
    contradictions: list[str],
    missing: list[str],
) -> float:
    if status is FindingCheckStatus.VERIFIED:
        score = 0.94 if quality is EvidenceQuality.HIGH else 0.8
        if quality is EvidenceQuality.MEDIUM:
            score = 0.78
    else:
        score = 0.35 if quality is EvidenceQuality.LOW else 0.45
    score -= 0.08 * min(len(contradictions), 3)
    score -= 0.04 * min(len(missing), 4)
    return max(0.05, min(0.99, round(score, 2)))


def _assess_one(
    result: WorkerResult,
    finding: Finding,
    catalog: dict[str, Evidence],
    vendor_domains: tuple[str, ...],
) -> FindingVerdict:
    evidence = _resolved_evidence(finding, catalog)
    supporting = _supporting(finding, evidence) or evidence
    quality = best_quality(supporting, vendor_domains)
    strength = support_strength(finding.statement, supporting)
    recorded = _recorded_contradictions(finding, result)
    prices = price_conflicts(supporting)
    invented: list[str] = []
    contradictions = list(dict.fromkeys(recorded + prices))
    review = None
    if _should_call_llm(finding, quality, strength):
        review = _llm_review(finding.statement, supporting)
    if review:
        if review["invented"]:
            invented = review["invented"]
            strength = "no"
        elif review["supported"]:
            strength = "yes"
        else:
            strength = "no"
        contradictions = list(dict.fromkeys(contradictions + review["contradictions"]))

    complete = _is_complete(finding, evidence, strength)
    missing = _missing_information(finding, evidence, quality, invented, strength)
    if _confidence_too_low(finding):
        complete = False

    verified = (
        complete
        and not contradictions
        and not invented
        and quality is not EvidenceQuality.LOW
        and not _confidence_too_low(finding)
    )
    status = FindingCheckStatus.VERIFIED if verified else FindingCheckStatus.INSUFFICIENT
    if status is FindingCheckStatus.INSUFFICIENT and not missing and not contradictions:
        missing.append("Evidence does not contain enough information to support the claim.")
    if status is FindingCheckStatus.VERIFIED:
        missing = []

    return FindingVerdict(
        finding_id=finding.finding_id,
        worker=result.worker,
        statement=finding.statement,
        verification_status=status,
        evidence_quality=quality,
        evidence_ids=[item.evidence_id for item in evidence],
        contradictions=contradictions,
        missing_information=missing,
        confidence=_confidence(status, quality, contradictions, missing),
    )


def assess_findings(state: VerificationState) -> dict:
    payload: VerificationInput = state["payload"]
    hosts = vendor_hosts(payload.request)
    assessments: list[FindingVerdict] = []
    for result in payload.agent_outputs:
        catalog = _evidence_by_id(result)
        for finding in result.findings:
            assessments.append(_assess_one(result, finding, catalog, hosts))
    return {"assessments": assessments}


def _worker_error(result: WorkerResult) -> str | None:
    error = (result.error or "").strip()
    if not error or error.lower() == "none":
        return None
    return f"{result.worker.value}: {error}"


def decide(state: VerificationState) -> dict:
    payload: VerificationInput = state["payload"]
    assessments: list[FindingVerdict] = list(state.get("assessments") or [])
    present = {result.worker for result in payload.agent_outputs}

    missing = [item for verdict in assessments for item in verdict.missing_information]
    conflicts = [item for verdict in assessments for item in verdict.contradictions]
    failures = [note for result in payload.agent_outputs if (note := _worker_error(result))]
    for result in payload.agent_outputs:
        missing.extend(result.gaps)

    topic_gaps = coverage_gaps(payload.request, payload.agent_outputs)
    for worker, gaps in topic_gaps.items():
        if worker in present:
            missing.extend(gaps)

    insufficient = [v for v in assessments if v.verification_status is FindingCheckStatus.INSUFFICIENT]
    conflicted = [v for v in assessments if v.contradictions]

    actions: list[TargetedAction] = []
    next_targets: list[str] = []
    per_worker_count: dict[WorkerType, int] = {}

    def add_action(worker: WorkerType, reason: str) -> None:
        next_targets.append(reason)
        used = per_worker_count.get(worker, 0)
        if used >= _MAX_ACTIONS_PER_WORKER:
            return
        per_worker_count[worker] = used + 1
        actions.append(TargetedAction(worker=worker, reason=reason))

    for verdict in insufficient:
        target = (
            verdict.missing_information[0]
            if verdict.missing_information
            else (verdict.contradictions[0] if verdict.contradictions else "Collect official evidence for the claim.")
        )
        add_action(verdict.worker, f"{verdict.statement} — {target}")

    for worker, gaps in topic_gaps.items():
        if worker not in present:
            continue
        for gap in gaps:
            add_action(worker, gap)

    for result in payload.agent_outputs:
        for gap in result.gaps:
            add_action(result.worker, gap)

    if not assessments:
        status = VerificationStatus.CANNOT_COMPLETE
        summary = "No findings were available to verify."
        if not failures:
            failures.append("No worker findings were submitted.")
        actions = []
    elif conflicted:
        status = VerificationStatus.REPLAN_REQUIRED
        summary = (
            f"{len(conflicted)} of {len(assessments)} findings have conflicting sources "
            "and need a revised research plan."
        )
        if not actions:
            worker = conflicted[0].worker
            actions = [TargetedAction(worker=worker, reason=conflicted[0].contradictions[0])]
    elif insufficient or any(topic_gaps.get(worker) for worker in present) or any(
        result.gaps for result in payload.agent_outputs
    ):
        status = VerificationStatus.RETRY_REQUIRED
        verified_count = len(assessments) - len(insufficient)
        summary = (
            f"{verified_count} of {len(assessments)} findings are evidence-backed; "
            f"{len(insufficient)} need targeted follow-up."
            if assessments
            else "Findings need targeted follow-up."
        )
        if not actions:
            worker = next(iter(present))
            actions = [TargetedAction(worker=worker, reason=missing[0] if missing else "Retry research for uncovered topics.")]
    else:
        status = VerificationStatus.VERIFIED
        summary = f"All {len(assessments)} findings are evidence-backed with no unresolved conflicts."
        actions = []

    if status in (VerificationStatus.VERIFIED, VerificationStatus.CANNOT_COMPLETE):
        actions = []

    decision = VerificationDecision(status=status, targeted_actions=actions, summary=summary)
    control = ControlSummary(
        missing_information=list(dict.fromkeys(missing)),
        conflicts=list(dict.fromkeys(conflicts)),
        failures=list(dict.fromkeys(failures)),
        next_research_targets=list(dict.fromkeys(next_targets)),
    )
    result = VerificationResult(
        research_run_id=payload.request.request_id,
        decision=decision,
        findings=assessments,
        control_summary=control,
        metadata={
            "workers": [output.worker.value for output in payload.agent_outputs],
            "finding_count": len(assessments),
            "verified_count": sum(
                1 for item in assessments if item.verification_status is FindingCheckStatus.VERIFIED
            ),
            "insufficient_count": len(insufficient),
            "completed_at": utcnow().isoformat(),
        },
    )
    return {
        "control": control.model_dump(),
        "decision_status": status.value,
        "result": result.model_dump(mode="json"),
    }
