from __future__ import annotations

from edrak.contracts import (
    ControlSummary,
    Evidence,
    EvidenceQuality,
    EvidenceRelation,
    Finding,
    FindingCheckStatus,
    FindingVerdict,
    SourceType,
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
from edrak.verification.state import VerificationState

_HIGH_SOURCES = {
    SourceType.OFFICIAL_DOCUMENTATION,
    SourceType.PRICING_PAGE,
    SourceType.RELEASE_NOTES,
    SourceType.ANNOUNCEMENT,
    SourceType.REGULATORY,
    SourceType.MARKET_REPORT,
}
_MEDIUM_SOURCES = {
    SourceType.NEWS_ARTICLE,
    SourceType.INTERNAL_DOCUMENT,
    SourceType.ECONOMIC,
}
_CLAIM_DETAIL_CUES = (
    ("autonom", "Level of autonomy"),
    ("enterprise", "Enterprise availability"),
    ("pric", "Pricing from an official source"),
    ("plan", "Which plan or packaging includes the capability"),
    ("available", "Availability / rollout status"),
    ("task", "Which tasks the capability covers"),
)


def _evidence_by_id(result: WorkerResult) -> dict[str, Evidence]:
    return {item.evidence_id: item for item in result.evidence}


def _quality_for(sources: list[SourceType]) -> EvidenceQuality:
    if not sources:
        return EvidenceQuality.LOW
    if any(source is SourceType.SEARCH_RESULT for source in sources) and all(
        source in (SourceType.SEARCH_RESULT, SourceType.OTHER, SourceType.WEB_PAGE)
        for source in sources
    ):
        return EvidenceQuality.LOW
    if any(source in _HIGH_SOURCES for source in sources):
        return EvidenceQuality.HIGH
    if any(source in _MEDIUM_SOURCES for source in sources):
        return EvidenceQuality.MEDIUM
    return EvidenceQuality.LOW


def _resolved_evidence(finding: Finding, catalog: dict[str, Evidence]) -> list[Evidence]:
    return [catalog[ref.evidence_id] for ref in finding.evidence_refs if ref.evidence_id in catalog]


def _is_complete(finding: Finding, evidence: list[Evidence]) -> bool:
    if not evidence:
        return False
    supporting_ids = {
        ref.evidence_id
        for ref in finding.evidence_refs
        if ref.relation is EvidenceRelation.SUPPORTS
    }
    supporting = [item for item in evidence if item.evidence_id in supporting_ids]
    if not supporting:
        return False
    return any(len(item.extracted_fact.strip()) >= 40 for item in supporting)


def _recorded_contradictions(finding: Finding, result: WorkerResult) -> list[str]:
    notes: list[str] = []
    if finding.is_contradicted:
        notes.append("At least one linked source contradicts the claim.")
    for conflict in result.conflicts:
        if conflict.finding_id == finding.finding_id:
            notes.append(conflict.description)
    return notes


def _price_contradictions(evidence: list[Evidence]) -> list[str]:
    facts = [item.extracted_fact.lower() for item in evidence]
    if any("$" in fact or "price" in fact or "cost" in fact for fact in facts):
        prices = {fact for fact in facts if "$" in fact or "included" in fact or "free" in fact}
        if len(prices) >= 2:
            return ["Sources report different pricing or packaging for the same claim."]
    return []


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
    """Compare the claim with the saved source text. None means use the rule fallback."""
    from edrak.core.llm import get_llm_client

    try:
        data = get_llm_client().chat_structured(
            [{"role": "user", "content": review_prompt(statement, _source_packet(evidence))}],
            temperature=0,
        )
    except Exception as exc:
        print(f"WARNING: verification LLM review failed: {exc}")
        return None

    quality = {
        "high": EvidenceQuality.HIGH,
        "medium": EvidenceQuality.MEDIUM,
        "low": EvidenceQuality.LOW,
    }.get(str(data.get("evidence_quality", "")).lower().strip())
    if quality is None:
        print("WARNING: verification LLM review returned no evidence_quality")
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
    return {"quality": quality, "contradictions": contradictions, "invented": invented}


def _missing_information(statement: str, evidence: list[Evidence], finding: Finding) -> list[str]:
    combined = " ".join(item.extracted_fact.lower() for item in evidence)
    missing: list[str] = []
    lowered = statement.lower()
    for cue, label in _CLAIM_DETAIL_CUES:
        if cue in lowered and cue not in combined:
            missing.append(label)
    for limitation in finding.limitations:
        if limitation not in missing:
            missing.append(limitation)
    if not evidence:
        missing.append("No evidence was attached to the claim.")
    elif all(item.source_type is SourceType.SEARCH_RESULT for item in evidence):
        missing.append("Official source beyond a search snippet.")
    return missing


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


def assess_findings(state: VerificationState) -> dict:
    payload: VerificationInput = state["payload"]
    assessments: list[FindingVerdict] = []

    for result in payload.agent_outputs:
        catalog = _evidence_by_id(result)
        for finding in result.findings:
            evidence = _resolved_evidence(finding, catalog)
            sources = [item.source_type for item in evidence]
            complete = _is_complete(finding, evidence)
            missing = _missing_information(finding.statement, evidence, finding)
            recorded = _recorded_contradictions(finding, result)
            review = _llm_review(finding.statement, evidence)
            if review:
                quality = review["quality"]
                contradictions = list(dict.fromkeys(recorded + review["contradictions"]))
                if review["invented"]:
                    contradictions.append(
                        "Claim includes details that are not in the saved source: "
                        + "; ".join(review["invented"])
                    )
            else:
                quality = _quality_for(sources)
                contradictions = list(dict.fromkeys(recorded + _price_contradictions(evidence)))

            verified = complete and not contradictions and quality is not EvidenceQuality.LOW
            status = FindingCheckStatus.VERIFIED if verified else FindingCheckStatus.INSUFFICIENT
            if status is FindingCheckStatus.INSUFFICIENT and not missing and not contradictions:
                missing.append("Evidence does not contain enough information to support the claim.")

            assessments.append(
                FindingVerdict(
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
            )

    return {"assessments": assessments}


def decide(state: VerificationState) -> dict:
    payload: VerificationInput = state["payload"]
    assessments: list[FindingVerdict] = list(state.get("assessments") or [])

    missing = [item for verdict in assessments for item in verdict.missing_information]
    conflicts = [item for verdict in assessments for item in verdict.contradictions]
    failures = [
        f"{result.worker.value}: {result.error}"
        for result in payload.agent_outputs
        if result.error and result.error != "none"
    ]
    insufficient = [v for v in assessments if v.verification_status is FindingCheckStatus.INSUFFICIENT]

    actions: list[TargetedAction] = []
    next_targets: list[str] = []
    seen_workers: set[WorkerType] = set()
    for verdict in insufficient:
        target = (
            verdict.missing_information[0]
            if verdict.missing_information
            else "Collect official evidence for the claim."
        )
        reason = f"{verdict.statement} — {target}"
        next_targets.append(reason)
        if verdict.worker not in seen_workers:
            seen_workers.add(verdict.worker)
            actions.append(TargetedAction(worker=verdict.worker, reason=reason))

    if not assessments:
        status = VerificationStatus.CANNOT_COMPLETE
        summary = "No findings were available to verify."
        if not failures:
            failures.append("No worker findings were submitted.")
        actions = []
    elif conflicts:
        status = VerificationStatus.REPLAN_REQUIRED
        summary = "Verification found conflicting evidence that must be resolved."
        if not actions:
            worker = assessments[0].worker
            actions = [TargetedAction(worker=worker, reason=conflicts[0])]
    elif insufficient:
        status = VerificationStatus.RETRY_REQUIRED
        summary = "Some findings lack sufficient evidence and need targeted follow-up."
    else:
        status = VerificationStatus.VERIFIED
        summary = "All findings are evidence-backed with no unresolved conflicts."
        actions = []

    # VERIFIED / CANNOT_COMPLETE cannot carry targeted_actions.
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
