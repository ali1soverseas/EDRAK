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
    evidence_numbers: Optional[dict[tuple[str, int, str], int]] = None,
) -> dict[str, Any]:
    """Assemble the frontend-compatible Brief JSON structure.

    `evidence_numbers` maps (task_id, attempt, evidence_id) to the E-number the database gave the
    evidence, so the brief's chips match the evidence explorer. Without it evidence is numbered here.
    """
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
            stored = (evidence_numbers or {}).get((wr.task_id, wr.attempt, item.evidence_id))
            display_no = stored or (max((e["display_no"] for e in evidence_list), default=0) + 1)
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

    evidence_list.sort(key=lambda e: e["display_no"])

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
                    if verdict and getattr(verdict, "verification_status", None)
                    else "unchecked"
                )
                verdict_verdict = verdict_status if verdict else None
                rel = (
                    reliability_from_quality(verdict.evidence_quality)
                    if verdict and getattr(verdict, "evidence_quality", None)
                    else 1
                )

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

    # Options come only from the decision analysis. Unranked; never invented.
    options: list[dict[str, Any]] = []
    keys = ["A", "B", "C"]
    da = decision_analysis or {}
    for idx, act in enumerate((da.get("recommended_actions") or [])[:3]):
        options.append({
            "option_id": f"opt-{idx + 1}",
            "key": keys[idx],
            "title": act.get("title") or act.get("action") or f"Option {keys[idx]}",
            "description": act.get("description") or act.get("rationale") or "",
            "supported_by": act.get("rationale") or "",
            "would_change_if": act.get("intended_outcome") or "",
            "time_horizon": act.get("time_horizon"),
        })

    def _items(rows: Any, kind: str) -> list[dict[str, Any]]:
        out = []
        for r in rows or []:
            if isinstance(r, dict) and r.get("title"):
                out.append({"kind": kind, "title": r["title"], "description": r.get("description", ""),
                            "rationale": r.get("business_rationale", "")})
        return out

    opportunities = _items(da.get("opportunities"), "opportunity")
    risks = _items(da.get("risks"), "risk")
    # Without a decision analysis, findings the workers labelled as opportunities or risks stand in.
    for lens in lenses:
        for item in lens["findings"]:
            cat = (item["finding"] or {}).get("category")
            entry = {"kind": cat, "title": item["finding"]["statement"], "refs": item["refs"], "worker": lens["worker"]}
            if cat == "opportunity" and not da.get("opportunities"):
                opportunities.append(entry)
            elif cat == "risk" and not da.get("risks"):
                risks.append(entry)

    # Synthesis (cross-signal)
    synthesis = None
    if cross_signal:
        raw_summary = cross_signal.get("summary")
        patterns = themes = impacts = []
        statement = ""
        if isinstance(raw_summary, dict):
            patterns = [str(p) for p in (raw_summary.get("dominant_patterns") or []) if str(p).strip()]
            themes = [str(p) for p in (raw_summary.get("strategic_themes") or []) if str(p).strip()]
            impacts = [str(p) for p in (raw_summary.get("business_impacts") or []) if str(p).strip()]
            statement = " ".join(patterns or themes or impacts)
        elif isinstance(raw_summary, str):
            statement = raw_summary
        signals = cross_signal.get("signals") or []
        if statement or signals:
            synthesis = {
                "statement": statement or f"{len(signals)} pattern(s) connect findings across workers.",
                "highlight": signals[0].get("title", "") if signals and isinstance(signals[0], dict) else "",
                "support": f"From {len(evidence_list)} sources.",
                "patterns": patterns, "themes": themes, "impacts": impacts,
                "signals": signals[:4] if isinstance(signals, list) else [],
            }

    # Card 3: the comparison matrix from the competitor worker, with E-numbers for its evidence.
    comparison = None
    comp = result_by_worker.get("competitor_intelligence")
    matrix = (comp.metadata.get("comparison_matrix") if comp else None) or {}
    if matrix.get("dimensions"):
        dims = []
        for d in matrix["dimensions"]:
            cells = []
            for c in d.get("cells", []):
                refs = [numbers[evidence_key("competitor_intelligence", e)] for e in c.get("evidence_ids", [])
                        if evidence_key("competitor_intelligence", e) in numbers]
                cells.append({**c, "refs": refs})
            dims.append({**d, "cells": cells})
        comparison = {**matrix, "dimensions": dims}

    # Card 3: search-interest series from the customer worker. Real values only.
    trends = []
    cust = result_by_worker.get("customer_trends")
    for series in (cust.metadata.get("trend_series") if cust else None) or []:
        if series.get("points"):
            trends.append(series)

    # Summary: only what the run produced.
    verified_n = sum(1 for v in verdict_by_finding.values()
                     if getattr(v.verification_status, "value", "") == "verified")
    total_n = sum(len(l["findings"]) for l in lenses)
    exec_summary = (da.get("executive_summary") or "").strip()
    if not exec_summary:
        top = [item["finding"]["statement"] for l in lenses for item in l["findings"]
               if item.get("status") == "verified"][:3]
        exec_summary = " ".join(top) or "The workers did not establish verified findings for this question."
    matters = [str(i) for i in (synthesis or {}).get("impacts", [])][:3]
    found_segments = [{"t": "text", "text": exec_summary}]
    matters_segments = [{"t": "text", "text": " ".join(matters)}] if matters else []
    options_segments = (
        [{"t": "text", "text": f"{len(options)} option(s) are laid out with the evidence for each. They are not ranked; the decision stays with you."}]
        if options else [{"t": "text", "text": "No options were drafted for this run. The findings and gaps below are the basis for a decision."}]
    )
    limits = None
    if gaps:
        limits = f"{len(gaps)} information gap(s) remain; they are listed with the comparison."
    summary = {"found": found_segments, "matters": matters_segments, "options": options_segments, "limits": limits}

    next_steps = list(verification.control_summary.next_research_targets) if verification else []
    next_steps += [str(q) for q in (da.get("additional_information_needed") or [])]
    next_steps = list(dict.fromkeys(next_steps))[:8]

    is_partial = getattr(orchestration.status, "value", str(orchestration.status)) != "completed"
    if is_partial:
        evidence_status = "partial"
    elif total_n == 0:
        evidence_status = "insufficient"
    elif verified_n == 0 and verdict_by_finding:
        evidence_status = "insufficient"
    else:
        evidence_status = "verified"

    return {
        "brief_id": brief_id,
        "brief_no": brief_no,
        "analysis_id": analysis_id,
        "analysis_title": analysis_title,
        "title": title or request.goal,
        "question": request.goal,
        "use_case": use_case,
        "created_at": created_at,
        "requested_by": requested_by,
        "partial": is_partial,
        "evidence_status": evidence_status,
        "summary_text": exec_summary,
        "counts": {"findings": total_n, "verified": verified_n, "sources": len(evidence_list), "workers": len(workers)},
        "request": request.model_dump(mode="json"),
        "orchestration": orchestration.model_dump(mode="json"),
        "verification": verification.model_dump(mode="json") if verification else {},
        "summary": summary,
        "lenses": lenses,
        "evidence": evidence_list,
        "options": options,
        "opportunities": opportunities[:6],
        "risks": risks[:6],
        "comparison": comparison,
        "trends": trends,
        "gaps": gaps,
        "next_steps": next_steps,
        "synthesis": synthesis,
        "cross_signal": cross_signal,
        "decision_analysis": decision_analysis,
    }
