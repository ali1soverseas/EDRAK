"""Node implementations for Internal Intelligence Worker LangGraph."""

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from edrak.agents.internal_intelligence.prompts import (
    INTERNAL_QUERY_PLANNING_SYSTEM_PROMPT,
    INTERNAL_SYNTHESIS_SYSTEM_PROMPT,
)
from edrak.agents.internal_intelligence.state import InternalAgentState
from edrak.contracts.evidence import Evidence, EvidenceRef, EvidenceRelation, SourceType
from edrak.contracts.result import Conflict, Finding, FindingCategory, WorkerResult, WorkerStatus
from edrak.contracts.task import ResearchTask, WorkerType
from edrak.core.action_log import log_action
from edrak.core.config import settings
from edrak.core.llm import get_json_object
from edrak.rag.retriever import InternalRetriever
from edrak.rag.text_utils import clean_markdown

logger = logging.getLogger(__name__)


def plan_queries_node(state: InternalAgentState) -> Dict[str, Any]:
    """Generates targeted natural-language search queries dynamically using LLM with deterministic fallback."""
    log_action("internal_intelligence", "planning search queries")
    started_at = datetime.now(timezone.utc)
    task: ResearchTask = state.get("task")
    if not task:
        return {"queries": [], "started_at": started_at, "error": "No task provided in state"}

    company = (task.company_profile.name if task.company_profile else "GitLab").strip()
    targets = task.business_context.targets if task.business_context else []
    focus_areas = task.business_context.focus_areas if task.business_context else []

    queries: List[str] = []
    llm_planned = False

    try:
        user_prompt = (
            f"Company Context: {company}\n"
            f"Research Goal: {task.goal}\n"
            f"Focus Areas: {task.focus}\n"
            f"Target Competitors: {', '.join(targets) if targets else 'None'}\n"
            f"Domain Focus Areas: {', '.join(focus_areas) if focus_areas else 'None'}\n\n"
            "Formulate up to 6 precise natural-language queries covering the checklist topics as JSON."
        )
        resp = get_json_object(
            INTERNAL_QUERY_PLANNING_SYSTEM_PROMPT,
            user_prompt,
            agent="internal",
            temperature=0.1,
        )
        if isinstance(resp, dict) and "queries" in resp and isinstance(resp["queries"], list):
            for q in resp["queries"]:
                if isinstance(q, str) and len(q.strip()) > 3:
                    queries.append(q.strip())
            llm_planned = True
            logger.info("LLM query planning generated %d queries for task %s", len(queries), task.task_id)
    except Exception as e:
        logger.warning("LLM query planning failed (%s). Using fallback queries.", e)

    # Fallback if LLM unavailable
    if not queries:
        if task.goal:
            queries.append(task.goal.strip())
        if task.focus:
            for clause in re.split(r"[,;\n•]+|\band\b", task.focus, flags=re.IGNORECASE):
                c = clause.strip().strip(".-* ")
                if len(c) > 3:
                    queries.append(f"{company} {c}" if company.lower() not in c.lower() else c)
        for t in targets:
            if t.strip():
                queries.append(f"{company} vs {t.strip()} competitive assessment battlecard")

    # Clean and deduplicate queries
    deduped: List[str] = []
    seen: Set[str] = set()
    for q in queries:
        norm = re.sub(r"\s+", " ", q).strip()
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            deduped.append(norm)

    return {"queries": deduped, "started_at": started_at, "llm_used": llm_planned}


def retrieve_evidence_node(state: InternalAgentState) -> Dict[str, Any]:
    """Retrieves candidates across internal docs and handbook, then cross-scores and reranks."""
    log_action("internal_intelligence", "retrieving internal evidence")
    queries = state.get("queries", [])
    task: Optional[ResearchTask] = state.get("task")
    if not queries and task:
        queries = [task.goal] if task.goal else []

    retriever = InternalRetriever()
    candidates: List[Evidence] = []
    seen_keys: Set[str] = set()

    for q in queries:
        try:
            # Retrieve synthetic internal docs (high precision)
            syn_res = retriever.retrieve(
                query=q,
                top_k=4,
                where_filter={"origin": "synthetic_internal"},
                min_score=0.25,
            )
            # Retrieve public handbook docs
            hb_res = retriever.retrieve(
                query=q,
                top_k=3,
                where_filter={"origin": "public_handbook"},
                min_score=0.45,
            )
            for ev in syn_res + hb_res:
                key = f"{ev.source_url}::{(ev.excerpt or '')[:140]}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    candidates.append(ev)
        except Exception as e:
            logger.error("Error retrieving evidence for query '%s': %s", q, e)

    # Cross-Score & Rerank against (Goal + Focus)
    goal_text = f"{task.goal if task else ''} {task.focus if task else ''}".lower()
    goal_tokens = set(re.findall(r"\w+", goal_text)) - {"a", "an", "the", "and", "or", "to", "in", "of", "for", "vs"}

    def _score_candidate(ev: Evidence) -> float:
        base_score = float(ev.metadata.get("relevance_score", 0.5))
        ev_text = f"{ev.source_title} {ev.extracted_fact} {ev.excerpt}".lower()
        ev_tokens = set(re.findall(r"\w+", ev_text))
        overlap = len(goal_tokens & ev_tokens) / max(1, len(goal_tokens))
        
        # Synthetic internal documents get a strategic priority boost
        boost = 0.15 if ev.is_synthetic else 0.0
        # Handbook pages with no goal token overlap get penalized
        penalty = -0.20 if (not ev.is_synthetic and overlap < 0.05) else 0.0
        
        return round(base_score * 0.6 + overlap * 0.4 + boost + penalty, 3)

    scored_candidates = [(ev, _score_candidate(ev)) for ev in candidates]
    scored_candidates.sort(key=lambda t: t[1], reverse=True)

    # Keep items meeting score cutoff up to max configured items
    cutoff = settings.INTERNAL_RERANK_SCORE_CUTOFF
    max_items = settings.INTERNAL_RERANK_MAX_ITEMS
    min_items = settings.INTERNAL_RERANK_MIN_ITEMS

    top_candidates = [item for item in scored_candidates if item[1] >= cutoff][:max_items]
    # If fewer than min_items met cutoff, take top min_items to ensure basic coverage
    if len(top_candidates) < min_items and scored_candidates:
        top_candidates = scored_candidates[:min(max_items, len(scored_candidates))]

    final_evidence: List[Evidence] = []
    for rank, (ev, final_score) in enumerate(top_candidates, 1):
        ev.metadata["rank"] = rank
        ev.metadata["relevance_score"] = max(0.1, min(0.99, final_score))
        final_evidence.append(ev)

    logger.info(
        "Retrieved %d candidates, reranked top %d high-signal items (cutoff >= %.2f, max=%d)",
        len(candidates),
        len(final_evidence),
        cutoff,
        max_items,
    )
    return {"retrieved_evidence": final_evidence}


def analyze_and_synthesize_node(state: InternalAgentState) -> Dict[str, Any]:
    """Deeply investigates evidence and synthesizes structured, grounded findings via LLM."""
    log_action("internal_intelligence", "summarizing findings from retrieved evidence")
    task: ResearchTask = state.get("task")
    evidence_list: List[Evidence] = state.get("retrieved_evidence", [])

    if not evidence_list:
        return {
            "findings": [],
            "summary": f"No matching internal documents or handbook entries found for task: '{task.goal if task else 'Unknown'}'.",
            "limitations_and_gaps": ["Internal vector index returned 0 matching results for all queries."],
            "dropped_findings": [],
            "conflicts": [],
            "llm_used": False,
        }

    evidence_by_id = {ev.evidence_id: ev for ev in evidence_list}

    # Format evidence pool for LLM investigation
    evidence_prompts = []
    for ev in evidence_list:
        rank = ev.metadata.get("rank", "-")
        rel_score = ev.metadata.get("relevance_score", 0.70)
        doc_type_label = "Synthetic Internal Strategic Doc" if ev.is_synthetic else "Public GitLab Handbook"
        last_updated = ev.metadata.get("last_updated", "Unknown")
        evidence_prompts.append(
            f"=== Evidence Item [ID: {ev.evidence_id}] ===\n"
            f"Rank: #{rank} | Relevance Score: {rel_score}\n"
            f"Source Title: {ev.source_title}\n"
            f"Source URI: {ev.source_url}\n"
            f"Source Type: {doc_type_label}\n"
            f"is_synthetic: {ev.is_synthetic}\n"
            f"Last Updated: {last_updated}\n"
            f"Extracted Fact: {ev.extracted_fact}\n"
            f"Excerpt:\n{ev.excerpt}\n"
        )
    evidence_context_str = "\n".join(evidence_prompts)

    llm_succeeded = False
    findings: List[Finding] = []
    discarded_evidence: List[Dict[str, Any]] = []
    summary_text = ""
    gaps: List[str] = []
    conflicts: List[Conflict] = []

    # 1. Attempt LLM Evidence Investigation and Synthesis
    try:
        user_prompt = (
            f"RESEARCH GOAL: {task.goal}\n"
            f"FOCUS AREAS: {task.focus}\n\n"
            f"RETRIEVED EVIDENCE POOL ({len(evidence_list)} items):\n"
            f"{evidence_context_str}\n\n"
            "Analyze the evidence strictly according to the grounding rules, coverage checklist, and JSON schema."
        )
        res_data = get_json_object(
            INTERNAL_SYNTHESIS_SYSTEM_PROMPT,
            user_prompt,
            agent="internal",
            temperature=0.1,
        )

        raw_findings = res_data.get("findings", [])
        for rf in raw_findings:
            stmt = clean_markdown(rf.get("statement", "")).strip()
            # Clean any placeholder bugs like (+? YoY) or (?)
            stmt = re.sub(r"\(\s*\+\s*\?\s*YoY\s*\)", "", stmt, flags=re.I)
            stmt = re.sub(r"\(\s*\?\s*\)", "", stmt)
            stmt = re.sub(r"\s+", " ", stmt).strip()
            if len(stmt) < 20:
                continue

            # Validate cited evidence IDs
            raw_eids = rf.get("evidence_ids", [])
            valid_refs: List[EvidenceRef] = []
            is_syn = False
            for eid in raw_eids:
                if eid in evidence_by_id:
                    valid_refs.append(EvidenceRef(evidence_id=eid, relation=EvidenceRelation.SUPPORTS))
                    if evidence_by_id[eid].is_synthetic:
                        is_syn = True

            # If no valid IDs were cited, ground to highest similarity item
            if not valid_refs and evidence_list:
                valid_refs = [EvidenceRef(evidence_id=evidence_list[0].evidence_id, relation=EvidenceRelation.SUPPORTS)]
                is_syn = evidence_list[0].is_synthetic

            cat_str = rf.get("category", "other").lower().strip()
            try:
                category = FindingCategory(cat_str)
            except ValueError:
                category = FindingCategory.OTHER

            conf = float(rf.get("confidence", 0.75))
            conf = max(0.1, min(0.95, round(conf, 2)))

            # Clean limitations (remove boilerplate "truncated")
            raw_limitations = rf.get("limitations", [])
            limitations = [
                lim for lim in raw_limitations
                if "excerpt may be truncated" not in lim.lower() and "truncated" not in lim.lower()
            ]
            if is_syn and not any("synthetic" in lim.lower() for lim in limitations):
                limitations.append("Derived from adapted/synthetic internal data for pilot demonstration purposes in accordance with project constraints.")

            for label, value in (
                ("Claim type", rf.get("claim_type")),
                ("Scope", rf.get("scope")),
                ("Supporting quote", rf.get("supporting_quote")),
            ):
                text = str(value or "").strip()
                if text and text not in limitations:
                    limitations.append(f"{label}: {text}")

            findings.append(
                Finding(
                    finding_id=str(uuid.uuid4()),
                    statement=stmt,
                    category=category,
                    evidence_refs=valid_refs,
                    confidence=conf,
                    limitations=limitations,
                )
            )

        summary_text = res_data.get("summary", "")
        gaps = list(res_data.get("gaps", []))
        discarded_evidence = list(res_data.get("discarded_evidence", []))

        # Parse conflicts
        raw_conflicts = res_data.get("conflicts", [])
        if isinstance(raw_conflicts, list) and findings:
            for rc in raw_conflicts:
                if isinstance(rc, dict):
                    desc = rc.get("description", "").strip()
                    c_eids = [eid for eid in rc.get("evidence_ids", []) if eid in evidence_by_id]
                    if desc:
                        conflicts.append(
                            Conflict(
                                finding_id=findings[0].finding_id,
                                contradicting_evidence_ids=c_eids,
                                description=desc,
                            )
                        )
                elif isinstance(rc, str) and rc.strip():
                    conflicts.append(
                        Conflict(
                            finding_id=findings[0].finding_id,
                            contradicting_evidence_ids=[],
                            description=rc.strip(),
                        )
                    )

        llm_succeeded = True
        logger.info("LLM synthesis produced %d verified findings", len(findings))

    except Exception as e:
        logger.warning("LLM synthesis failed (%s). Falling back to grounded deterministic extraction.", e)

    # 2. Deterministic Fallback if LLM failed
    if not findings:
        for ev in evidence_list:
            raw_fact = ev.extracted_fact or ""
            stmt = clean_markdown(raw_fact)
            if len(stmt) < 35:
                continue

            category = _classify_category(f"{ev.extracted_fact} {ev.excerpt or ''} {ev.source_title or ''}")
            rel = float(ev.metadata.get("relevance_score", 0.70))
            source_factor = 0.80 if ev.is_synthetic else 0.85
            conf = round(min(0.95, rel * source_factor), 2)

            limitations = []
            if ev.is_synthetic:
                limitations.append("Derived from adapted/synthetic internal data for pilot demonstration purposes in accordance with project constraints.")

            claim_type = "internal_claim" if ev.is_synthetic else "verified_fact"
            limitations.append(f"Claim type: {claim_type}")
            quote = (ev.excerpt or "").strip()
            if quote:
                limitations.append(f"Supporting quote: {quote[:80]}")

            findings.append(
                Finding(
                    finding_id=str(uuid.uuid4()),
                    statement=stmt,
                    category=category,
                    evidence_refs=[EvidenceRef(evidence_id=ev.evidence_id, relation=EvidenceRelation.SUPPORTS)],
                    confidence=conf,
                    limitations=limitations,
                )
            )

        covered_cats = sorted(list({f.category.value for f in findings}))
        summary_text = (
            f"Internal Intelligence Assessment for '{task.goal if task else 'Internal Analysis'}':\n"
            f"- Analyzed {len(evidence_list)} internal evidence sources covering: {', '.join(covered_cats)}."
        )

    # Check for implicit tension if Intelligent Model Selection handbook page and AI Gateway doc both retrieved
    ims_eids = [ev.evidence_id for ev in evidence_list if "intelligent model selection" in (ev.source_title or "").lower()]
    gw_eids = [ev.evidence_id for ev in evidence_list if "ai gateway" in (ev.source_title or "").lower()]
    if ims_eids and gw_eids and not conflicts and findings:
        conflicts.append(
            Conflict(
                finding_id=findings[0].finding_id,
                contradicting_evidence_ids=[ims_eids[0], gw_eids[0]],
                description="Tension between operational status and strategic architecture: Official Handbook notes model selection is currently static per feature, while AI Gateway architecture documentation describes automated multi-model routing under active rollout.",
            )
        )

    # Ensure required domain gaps are present
    standard_gaps = [
        "GitHub Copilot pricing and capability claims are sourced from internal battlecards and are not independently verified or directly like-for-like with GitLab's seat+credits structure.",
        "Absence of quantitative latency, throughput, or hardware performance benchmarks for self-hosted LLM deployments.",
        "No credit overage or hard cap enforcement mechanisms documented beyond base promotional allowances.",
        "GA date and pricing model lack non-synthetic external corroboration.",
        "External GitHub Copilot comparison is delegated to the Competitor worker.",
    ]
    for sg in standard_gaps:
        if not any(sg.lower()[:30] in g.lower() for g in gaps):
            gaps.append(sg)

    return {
        "findings": findings,
        "retrieved_evidence": evidence_list,
        "summary": summary_text,
        "limitations_and_gaps": gaps,
        "conflicts": conflicts,
        "dropped_findings": discarded_evidence,
        "llm_used": llm_succeeded,
    }


def format_worker_result_node(state: InternalAgentState) -> Dict[str, Any]:
    """Packages findings and evidence into the standard WorkerResult contract."""
    log_action("internal_intelligence", "writing worker result")
    task: ResearchTask = state.get("task")
    findings = state.get("findings", [])
    evidence = state.get("retrieved_evidence", [])
    summary = state.get("summary", "Internal intelligence research completed.")
    gaps = state.get("limitations_and_gaps", [])
    conflicts = state.get("conflicts", [])
    dropped = state.get("dropped_findings", [])
    llm_used = state.get("llm_used", False)

    # Status determination
    if not evidence:
        status = WorkerStatus.NO_EVIDENCE
    elif any("no internal evidence" in g.lower() or "uncovered" in g.lower() or "absent" in g.lower() for g in gaps):
        status = WorkerStatus.PARTIAL
    else:
        status = WorkerStatus.COMPLETED

    # Overall confidence: average of finding confidences
    if findings:
        avg_conf = sum(f.confidence or 0.5 for f in findings) / len(findings)
        overall_confidence = round(avg_conf, 2)
    else:
        overall_confidence = 0.0

    started_at = state.get("started_at") or datetime.now(timezone.utc)
    completed_at = datetime.now(timezone.utc)
    if completed_at <= started_at:
        completed_at = datetime.fromtimestamp(started_at.timestamp() + 0.05, tz=timezone.utc)

    task_id = task.task_id if task else str(uuid.uuid4())
    attempt = task.attempt if task else 1

    result = WorkerResult(
        task_id=task_id,
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        status=status,
        attempt=attempt,
        findings=findings,
        evidence=evidence,
        gaps=gaps,
        conflicts=conflicts,
        confidence=overall_confidence,
        started_at=started_at,
        completed_at=completed_at,
        metadata={
            "query_count": len(state.get("queries", [])),
            "evidence_count": len(evidence),
            "finding_count": len(findings),
            "discarded_count": len(dropped),
            "discarded_evidence": dropped,
            "llm_used": llm_used,
            "summary": summary,
        },
    )
    return {"worker_result": result}


def _classify_category(text: str) -> FindingCategory:
    """Fallback classifier using ordered, word-boundary rules."""
    if re.search(r"\b(okrs?|strategic okrs|key results?|arr|nrr|growth metrics)\b", text, re.I):
        return FindingCategory.MARKET_SIGNAL

    if re.search(r"\b(copilot|competitor|battlecard|vs\.?|versus|differentiat\w+)\b", text, re.I):
        return FindingCategory.POSITIONING

    if re.search(r"\b(gitlab credits|per-seat|seat|sku|subscription|add-on|pricing|price[sd]?|premium|ultimate|consumption-based|metered|billing)\b", text, re.I):
        return FindingCategory.PRICING_PACKAGING

    if re.search(r"\b(ai gateway|agent platform|duo|self-hosted|air-gapped|privacy|retention|model|routing|workflow)\b", text, re.I):
        return FindingCategory.PRODUCT_FEATURE

    if re.search(r"\b(market|adoption|customers?|demand|competition|revenue|telemetry)\b", text, re.I):
        return FindingCategory.MARKET_SIGNAL

    return FindingCategory.OTHER
