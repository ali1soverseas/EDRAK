"""Node implementations for Internal Intelligence Worker LangGraph."""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Set
import uuid

from edrak.agents.internal_intelligence.prompts import (
    INTERNAL_QUERY_PLANNING_SYSTEM_PROMPT,
    INTERNAL_SYNTHESIS_SYSTEM_PROMPT,
)
from edrak.agents.internal_intelligence.state import InternalAgentState
from edrak.contracts.evidence import Evidence, EvidenceRef, EvidenceRelation, SourceType
from edrak.contracts.result import Finding, FindingCategory, WorkerResult, WorkerStatus
from edrak.contracts.task import ResearchTask, WorkerType
from edrak.mcp.client import InternalDataToolClient

logger = logging.getLogger(__name__)


def plan_queries_node(state: InternalAgentState) -> Dict[str, Any]:
    """Generates focused, multi-topic internal retrieval queries dynamically from the ResearchTask."""
    task: ResearchTask = state.get("task")
    if not task:
        return {"queries": [], "error": "No task provided in state"}

    company = (task.company_profile.name if task.company_profile else "").strip()
    targets = task.business_context.targets if task.business_context else []
    focus_areas = task.business_context.focus_areas if task.business_context else []

    queries: List[str] = []

    # 1. Include the primary business goal
    if task.goal:
        queries.append(task.goal.strip())

    # 2. Dynamically parse discrete topics from task.focus
    if task.focus:
        # Split by comma, semicolon, or bullet delimiters
        raw_clauses = re.split(r"[,;\n•]+|\band\b", task.focus, flags=re.IGNORECASE)
        for clause in raw_clauses:
            clean_clause = clause.strip().strip(".-* ")
            if len(clean_clause) > 3:
                # Prefix with company name if not already present
                if company and company.lower() not in clean_clause.lower():
                    queries.append(f"{company} {clean_clause}")
                else:
                    queries.append(clean_clause)

    # 3. Dynamically add target competitor comparisons
    for target in targets:
        target_clean = target.strip()
        if target_clean:
            if company:
                queries.append(f"{company} vs {target_clean} competitive positioning architecture")
            else:
                queries.append(f"{target_clean} competitive comparison")

    # 4. Dynamically add domain focus areas
    for fa in focus_areas:
        fa_clean = fa.strip()
        if fa_clean:
            if company and company.lower() not in fa_clean.lower():
                queries.append(f"{company} {fa_clean}")
            else:
                queries.append(fa_clean)

    # Clean and deduplicate queries preserving order
    deduped_queries: List[str] = []
    seen: Set[str] = set()
    for q in queries:
        norm = re.sub(r"\s+", " ", q).strip()
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            deduped_queries.append(norm)

    logger.info("Dynamically planned %d targeted queries for task %s", len(deduped_queries), task.task_id)
    return {"queries": deduped_queries}


def retrieve_evidence_node(state: InternalAgentState) -> Dict[str, Any]:
    """Queries the internal RAG knowledge base using the planned queries with per-query hit tracking."""
    queries = state.get("queries", [])
    task = state.get("task")
    if not queries:
        queries = [task.goal] if task and task.goal else []

    tool_client = InternalDataToolClient()
    all_evidence: List[Evidence] = []
    seen_contents: Set[str] = set()

    for q in queries:
        try:
            results = tool_client.search_internal_knowledge(query=q, top_k=3)
            logger.info("Query '%s' returned %d evidence items", q, len(results))
            for ev in results:
                content_key = (ev.excerpt or ev.extracted_fact or "").strip()[:140]
                if content_key and content_key not in seen_contents:
                    seen_contents.add(content_key)
                    all_evidence.append(ev)
        except Exception as e:
            logger.error("Error retrieving evidence for query '%s': %s", q, e)

    logger.info("Total unique evidence items retrieved across queries: %d", len(all_evidence))
    return {"retrieved_evidence": all_evidence}


def analyze_and_synthesize_node(state: InternalAgentState) -> Dict[str, Any]:
    """Analyzes the retrieved evidence and extracts structured analytical findings with provenance."""
    task: ResearchTask = state.get("task")
    evidence_list: List[Evidence] = state.get("retrieved_evidence", [])

    if not evidence_list:
        return {
            "findings": [],
            "summary": (
                f"No matching internal documents or handbook entries were found for task: "
                f"'{task.goal if task else 'Unknown'}'."
            ),
            "limitations_and_gaps": [
                "Internal vector index returned 0 matching results for all queries.",
                "Ensure internal handbook and strategy documents are indexed in data/internal.",
            ],
        }

    findings: List[Finding] = []
    covered_categories: Set[str] = set()

    # Synthesize findings grounded in retrieved evidence items
    for ev in evidence_list:
        category = _categorize_domain_category(ev)
        statement = _extract_core_statement(ev)
        covered_categories.add(category.value)

        # Realistic confidence calculation:
        # Synthetic data is capped at 0.55 - 0.60, authentic internal data scored up to 0.85
        raw_score = float(ev.metadata.get("confidence_score", 0.75)) if ev.metadata else 0.75
        if ev.is_synthetic:
            confidence = round(max(0.45, min(0.60, 0.40 + (raw_score * 0.20))), 2)
            limitations = [
                "Derived from adapted/synthetic internal data for pilot demonstration purposes in accordance with project constraints."
            ]
        else:
            confidence = round(max(0.65, min(0.85, raw_score)), 2)
            limitations = []

        finding = Finding(
            statement=statement,
            category=category,
            evidence_refs=[
                EvidenceRef(evidence_id=ev.evidence_id, relation=EvidenceRelation.SUPPORTS)
            ],
            confidence=confidence,
            limitations=limitations,
        )
        findings.append(finding)

    # Construct synthesized summary reflecting ONLY the actual topics retrieved
    category_names = sorted(list(covered_categories))
    summary_lines = [
        f"Internal Intelligence Assessment for '{task.goal if task else 'Internal Analysis'}':",
        f"- Analyzed {len(evidence_list)} internal evidence sources covering: {', '.join(category_names)}.",
    ]
    for f in findings[:5]:
        summary_lines.append(f"• [{f.category.value.upper()}] {f.statement}")

    summary_text = "\n".join(summary_lines)

    # Dynamic gaps identification: evaluate against requested focus items
    gaps: List[str] = []
    text_corpus = " ".join(
        [f.statement.lower() for f in findings]
        + [e.extracted_fact.lower() for e in evidence_list]
        + [(e.excerpt or "").lower() for e in evidence_list]
        + [(e.source_title or "").lower() for e in evidence_list]
    )

    requested_topics: List[str] = []
    if task and task.focus:
        raw_clauses = re.split(r"[,;\n•]+|\band\b", task.focus, flags=re.IGNORECASE)
        for c in raw_clauses:
            clean = c.strip().strip(".-* ")
            if len(clean) > 3:
                requested_topics.append(clean)

    if task and task.business_context and task.business_context.focus_areas:
        requested_topics.extend(task.business_context.focus_areas)

    for req_topic in requested_topics:
        # Check if any significant word from the requested topic is in the corpus
        topic_keywords = [w.lower() for w in re.split(r"\s+", req_topic) if len(w) > 3]
        if topic_keywords and not any(kw in text_corpus for kw in topic_keywords):
            gaps.append(f"No internal evidence retrieved for requested focus area: '{req_topic}'.")

    if any(ev.is_synthetic for ev in evidence_list):
        gaps.append("All retrieved private internal data is synthetic/adapted for pilot demonstration.")

    return {
        "findings": findings,
        "summary": summary_text,
        "limitations_and_gaps": gaps,
    }


def format_worker_result_node(state: InternalAgentState) -> Dict[str, Any]:
    """Packages the findings and evidence into the standard WorkerResult contract with coverage-based status."""
    task: ResearchTask = state.get("task")
    findings = state.get("findings", [])
    evidence = state.get("retrieved_evidence", [])
    summary = state.get("summary", "Internal intelligence research completed.")
    gaps = state.get("limitations_and_gaps", [])

    # Derive status strictly from coverage of requested focus areas
    uncovered_focus_gaps = [g for g in gaps if g.startswith("No internal evidence retrieved")]
    if not evidence:
        status = WorkerStatus.NO_EVIDENCE
    elif uncovered_focus_gaps:
        status = WorkerStatus.PARTIAL
    else:
        status = WorkerStatus.COMPLETED

    # Overall confidence is the average of finding confidences, capped at 0.60 if all synthetic
    if findings:
        avg_conf = sum(f.confidence or 0.5 for f in findings) / len(findings)
        if all(e.is_synthetic for e in evidence):
            overall_confidence = round(min(0.60, avg_conf), 2)
        else:
            overall_confidence = round(avg_conf, 2)
    else:
        overall_confidence = 0.0

    task_id = task.task_id if task else str(uuid.uuid4())
    attempt = task.attempt if task else 1
    now_utc = datetime.now(timezone.utc)

    result = WorkerResult(
        task_id=task_id,
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        status=status,
        attempt=attempt,
        findings=findings,
        evidence=evidence,
        gaps=gaps,
        conflicts=[],
        confidence=overall_confidence,
        started_at=now_utc,
        completed_at=now_utc,
        metadata={
            "query_count": len(state.get("queries", [])),
            "evidence_count": len(evidence),
            "finding_count": len(findings),
            "uncovered_focus_count": len(uncovered_focus_gaps),
            "summary": summary,
        },
    )

    return {"worker_result": result}


def _categorize_domain_category(ev: Evidence) -> FindingCategory:
    """Accurately categorizes the evidence into standard FindingCategory enum."""
    text = f"{ev.source_title or ''} {ev.source_url or ''} {ev.extracted_fact} {ev.excerpt or ''}".lower()

    # 1. Competitive positioning & comparisons
    if "vs" in text or "competitor" in text or "battlecard" in text or "copilot" in text or "positioning" in text:
        return FindingCategory.POSITIONING

    # 2. Pricing, packaging & virtual currency
    if any(w in text for w in ["credit", "pricing", "tier", "allowance", "margin", "monetization", "cost", "sku", "$"]):
        return FindingCategory.PRICING_PACKAGING

    # 3. Telemetry, ARR metrics & adoption
    if any(w in text for w in ["okr", "telemetry", "arr", "nrr", "adoption", "consumption", "growth", "revenue"]):
        return FindingCategory.MARKET_SIGNAL

    # 4. Architecture, privacy, AI Gateway & product capabilities
    if any(w in text for w in ["gateway", "privacy", "self-hosted", "air-gap", "agent platform", "orchestration", "vulnerability", "feature", "security", "architecture"]):
        return FindingCategory.PRODUCT_FEATURE

    return FindingCategory.OTHER


def _extract_core_statement(ev: Evidence) -> str:
    """Extracts a succinct, high-quality factual statement from the evidence content."""
    content = ev.excerpt or ev.extracted_fact or ""
    lines = [
        line.strip() for line in content.split("\n")
        if line.strip()
        and not line.strip().startswith("#")
        and not line.strip().startswith("**Document ID")
        and not line.strip().startswith("**Classification")
        and not line.strip().startswith("**Last Updated")
        and not line.strip().startswith("**Author")
        and not line.strip().startswith("---")
        and not line.strip().startswith("|")
    ]
    if not lines:
        if ev.extracted_fact and not ev.extracted_fact.startswith("#") and not ev.extracted_fact.startswith("**Doc"):
            return ev.extracted_fact
        return f"Internal documentation excerpt from {ev.source_title or ev.source_url or 'internal source'}."

    for line in lines:
        clean = line.lstrip("-*1234567890. ").strip()
        if len(clean) > 25 and not clean.endswith(":") and not clean.startswith("**Doc"):
            if len(clean) > 220:
                clean = clean[:217] + "..."
            return clean

    first = lines[0].lstrip("-*1234567890. ").strip()
    if len(first) > 220:
        first = first[:217] + "..."
    return first or f"Strategic documentation insight from {ev.source_title}."

