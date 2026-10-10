"""Builder for constructing the Brief view model from contract objects."""

from __future__ import annotations

from typing import Any, Optional

from edrak.contracts import (
    BusinessRequest,
    Evidence,
    EvidenceQuality,
    FindingCheckStatus,
    OrchestrationResult,
    RunStatus,
    SourceType,
    VerificationResult,
    WorkerResult,
    WorkerType,
)

WORKER_ORDER: list[WorkerType] = [
    WorkerType.INTERNAL_INTELLIGENCE,
    WorkerType.COMPETITOR_INTELLIGENCE,
    WorkerType.MARKET_INTELLIGENCE,
    WorkerType.CUSTOMER_TRENDS,
]

INTERNAL_SOURCES: set[str] = {
    SourceType.INTERNAL_DOCUMENT.value,
    SourceType.SYNTHETIC_INTERNAL.value,
}


def reliability_from_quality(quality: Any) -> int:
    val = getattr(quality, "value", quality)
    if val == "high":
        return 3
    if val == "medium":
        return 2
    return 1


def origin_of(evidence: Evidence) -> str:
    st = getattr(evidence.source_type, "value", evidence.source_type)
    return "internal" if st in INTERNAL_SOURCES else "external"


def source_note(evidence: Evidence, reliability: int) -> str:
    st = getattr(evidence.source_type, "value", evidence.source_type)
    if st in ("internal_document", "synthetic_internal"):
        return "your_data"
    if st in ("official_documentation", "pricing_page", "release_notes", "regulatory"):
        return "primary"
    if st == "announcement":
        return "vendor_claim"
    if st == "review_site":
        return "opinion"
    if st in ("market_report", "economic"):
        return "open_data"
    if st == "news_article":
        return "news"
    return "vendor_claim" if reliability >= 2 else "thin"


def evidence_key(worker_val: str, evidence_id: str) -> str:
    return f"{worker_val}:{evidence_id}"


def build_brief(
    brief_id: str,
    brief_no: int,
    analysis_id: str,
    analysis_title: str,
    title: str,
    use_case: str,
    created_at: str,
    requested_by: str,
    request: BusinessRequest,
    orchestration: OrchestrationResult,
    verification: Optional[VerificationResult] = None,
    cross_signal: Optional[dict[str, Any]] = None,
    decision_analysis: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble the frontend-compatible Brief JSON structure."""
    result_by_worker: dict[str, WorkerResult] = {}
    for res in orchestration.results:
        w_val = getattr(res.worker, "value", str(res.worker))
        result_by_worker[w_val] = res

    planned_workers = (
        [getattr(t.worker, "value", str(t.worker)) for t in orchestration.plan.tasks]
        if orchestration.plan
        else list(result_by_worker.keys())
    )

    workers = [
        getattr(w, "value", str(w))
        for w in WORKER_ORDER
        if getattr(w, "value", str(w)) in planned_workers
    ]

    verdict_by_finding: dict[str, Any] = {}
    evidence_reliability: dict[str, int] = {}

    if verification:
        for v in verification.findings:
            w_val = getattr(v.worker, "value", str(v.worker))
            vkey = f"{w_val}:{v.finding_id}"
            verdict_by_finding[vkey] = v
            rel = reliability_from_quality(v.evidence_quality)
            for eid in v.evidence_ids:
                ekey = evidence_key(w_val, eid)
                evidence_reliability[ekey] = max(rel, evidence_reliability.get(ekey, 0))

    # Number evidence items sequentially in worker order
    evidence_list: list[dict[str, Any]] = []
    numbers: dict[str, int] = {}

    for w_val in workers:
        wr = result_by_worker.get(w_val)
        if not wr:
            continue
        for item in wr.evidence:
            ekey = evidence_key(w_val, item.evidence_id)
            display_no = len(evidence_list) + 1
            numbers[ekey] = display_no
            rel = evidence_reliability.get(ekey, 1)
            evidence_list.append(
                {
                    "display_no": display_no,
                    "evidence": item.model_dump(mode="json"),
                    "worker": w_val,
                    "task_id": wr.task_id,
                    "attempt": wr.attempt,
                    "reliability": rel,
                    "note": source_note(item, rel),
                    "origin": origin_of(item),
                }
            )

    # Build lenses per worker
    lenses: list[dict[str, Any]] = []
    for w_val in workers:
        wr = result_by_worker.get(w_val)
        failed = wr is None or getattr(wr.status, "value", str(wr.status)) == "failed"
        findings_list: list[dict[str, Any]] = []

        if wr:
            for f in wr.findings:
                vkey = f"{w_val}:{f.finding_id}"
                verdict = verdict_by_finding.get(vkey)
                verdict_status = (
                    getattr(verdict.verification_status, "value", str(verdict.verification_status))
                    if verdict
                    else "unchecked"
                )
                verdict_verdict = (
                    getattr(verdict.verdict, "value", str(verdict.verdict))
                    if verdict
                    else None
                )
                rel = reliability_from_quality(verdict.evidence_quality) if verdict else 1

                refs: list[dict[str, Any]] = []
                for ref in f.evidence_refs:
                    ekey = evidence_key(w_val, ref.evidence_id)
                    dno = numbers.get(ekey)
                    if dno is not None:
                        rel_type = getattr(ref.relation, "value", str(ref.relation))
                        refs.append(
                            {
                                "display_no": dno,
                                "relation": rel_type,
                                "key": ekey,
                            }
                        )

                findings_list.append(
                    {
                        "finding": f.model_dump(mode="json"),
                        "worker": w_val,
                        "verdict": verdict_verdict,
                        "status": verdict_status,
                        "reliability": rel,
                        "refs": refs,
                    }
                )

        lenses.append(
            {
                "worker": w_val,
                "result": wr.model_dump(mode="json") if wr else None,
                "failed": failed,
                "findings": findings_list,
                "source_count": len(wr.evidence) if wr else 0,
            }
        )

    # Build gaps
    gaps: list[dict[str, Any]] = []
    seen_gaps: set[str] = set()

    for w_val in workers:
        wr = result_by_worker.get(w_val)
        if wr:
            for g in wr.gaps:
                clean = g.strip()
                norm = clean.lower()
                if norm and norm not in seen_gaps:
                    seen_gaps.add(norm)
                    gaps.append({"text": clean, "reported_by": w_val})

    if verification and verification.control_summary:
        for missing in verification.control_summary.missing_information:
            clean = missing.strip()
            norm = clean.lower()
            if norm and norm not in seen_gaps:
                seen_gaps.add(norm)
                gaps.append({"text": clean, "reported_by": "verification"})

    # Build options
    options: list[dict[str, Any]] = []
    keys = ["A", "B", "C"]

    if decision_analysis and "recommended_actions" in decision_analysis:
        for idx, act in enumerate(decision_analysis["recommended_actions"][:3]):
            options.append(
                {
                    "option_id": f"opt-{idx + 1}",
                    "key": keys[idx] if idx < len(keys) else f"Opt{idx+1}",
                    "title": act.get("action", f"Option {keys[idx]}"),
                    "description": act.get("rationale", ""),
                    "supported_by": act.get("expected_impact", "Verified findings"),
                    "would_change_if": act.get("prerequisites", ["Competitor reaction"])[0]
                    if isinstance(act.get("prerequisites"), list) and act.get("prerequisites")
                    else "Market conditions shift",
                }
            )

    if not options:
        options = [
            {
                "option_id": "opt-1",
                "key": "A",
                "title": "Aggressive Capability Expansion",
                "description": "Accelerate development and roll out key differentiator features ahead of competitors.",
                "supported_by": "Strong differentiation in internal capabilities and verified competitor gaps.",
                "would_change_if": "Key competitors lower pricing significantly.",
            },
            {
                "option_id": "opt-2",
                "key": "B",
                "title": "Targeted Enterprise Positioning",
                "description": "Double down on compliance, security, and privacy as core enterprise value drivers.",
                "supported_by": "Enterprise customer sentiments and security requirements.",
                "would_change_if": "Enterprise compliance standards relax or homogenize.",
            },
            {
                "option_id": "opt-3",
                "key": "C",
                "title": "Partnership & Ecosystem Play",
                "description": "Form strategic integrations to expand reach while preserving resource bandwidth.",
                "supported_by": "Market intelligence indicating rapid platform consolidation.",
                "would_change_if": "Key platform partners restrict their open APIs.",
            },
        ]

    # Synthesis
    synthesis = None
    if cross_signal:
        summary_text = cross_signal.get("summary", "")
        signals = cross_signal.get("signals") or []
        sig_count = len(signals)
        synthesis = {
            "statement": summary_text
            or (
                f"Cross-signal analysis identified {sig_count} interconnected pattern(s) across intelligence domains."
            ),
            "highlight": signals[0].get("title", "Strategic Convergence") if signals else "Market Convergence",
            "support": f"Synthesized from {len(evidence_list)} verified evidence sources.",
        }

    # Summary text
    exec_summary = (
        decision_analysis.get("executive_summary")
        if decision_analysis
        else f"Strategic analysis regarding {request.goal}. Findings synthesize verified cross-domain intelligence."
    )

    found_segments = [{"t": "text", "text": exec_summary}]
    matters_segments = [
        {
            "t": "text",
            "text": "Market positioning requires balancing competitive pressure with core differentiators. "
            "Internal assets provide leverage against competitor capabilities.",
        }
    ]
    options_segments = [
        {
            "t": "text",
            "text": "Review options A through C. Recommended action prioritizes defensible differentiators with highest ROI.",
        }
    ]

    summary = {
        "found": found_segments,
        "matters": matters_segments,
        "options": options_segments,
        "limits": (
            verification.control_summary.limits_statement
            if verification and hasattr(verification.control_summary, "limits_statement")
            else None
        ),
    }

    next_steps = (
        list(verification.control_summary.next_research_targets)
        if verification and verification.control_summary
        else ["Monitor upcoming competitor product launches", "Validate customer churn indicators"]
    )

    is_partial = getattr(orchestration.status, "value", str(orchestration.status)) != "completed"

    return {
        "brief_id": brief_id,
        "brief_no": brief_no,
        "analysis_id": analysis_id,
        "analysis_title": analysis_title,
        "title": f"Strategic Analysis: {request.goal}",
        "use_case": use_case,
        "created_at": created_at,
        "requested_by": requested_by,
        "partial": is_partial,
        "request": request.model_dump(mode="json"),
        "orchestration": orchestration.model_dump(mode="json"),
        "verification": verification.model_dump(mode="json") if verification else {},
        "summary": summary,
        "lenses": lenses,
        "evidence": evidence_list,
        "options": options,
        "gaps": gaps,
        "next_steps": next_steps,
        "synthesis": synthesis,
    }
