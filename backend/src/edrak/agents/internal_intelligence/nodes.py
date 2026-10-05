"""Node implementations for Internal Intelligence Worker LangGraph."""

import logging
import re
from typing import Any, Dict, List, Optional
import uuid

from edrak.agents.internal_intelligence.prompts import (
    INTERNAL_QUERY_PLANNING_SYSTEM_PROMPT,
    INTERNAL_SYNTHESIS_SYSTEM_PROMPT,
)
from edrak.agents.internal_intelligence.state import InternalAgentState
from edrak.contracts.evidence import Evidence, EvidenceRef, EvidenceRelation
from edrak.contracts.result import Finding, FindingCategory, WorkerResult, WorkerStatus
from edrak.contracts.task import ResearchTask, WorkerType
from edrak.mcp.client import InternalDataToolClient

logger = logging.getLogger(__name__)


def plan_queries_node(state: InternalAgentState) -> Dict[str, Any]:
    """Generates focused internal retrieval queries based on the assigned ResearchTask."""
    task: ResearchTask = state.get("task")
    if not task:
        return {"queries": [], "error": "No task provided in state"}

    queries: List[str] = []

    # Priority 1: Focus and goal
    queries.append(f"{task.goal} {task.focus}".strip())
    if task.focus:
        queries.append(task.focus.strip())

    # Priority 2: Add domain-specific targeted queries for internal knowledge
    goal_focus_lower = (task.goal + " " + task.focus).lower()
    if "duo" in goal_focus_lower or "ai" in goal_focus_lower or "copilot" in goal_focus_lower:
        queries.append("GitLab Duo architecture AI gateway Code Suggestions Chat")
        queries.append("GitLab Duo pricing add-on Pro Enterprise margins")
        queries.append("GitLab Duo internal OKRs roadmap adoption metrics")
    elif "pricing" in goal_focus_lower or "revenue" in goal_focus_lower:
        queries.append("GitLab pricing tiers Premium Ultimate add-on gross margins")
    elif "strategy" in goal_focus_lower or "handbook" in goal_focus_lower:
        queries.append("GitLab handbook values CREDIT DevSecOps platform strategy")

    # Clean and deduplicate queries
    deduped_queries = []
    seen = set()
    for q in queries:
        clean = q.strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            deduped_queries.append(clean)

    return {"queries": deduped_queries[:4]}


def retrieve_evidence_node(state: InternalAgentState) -> Dict[str, Any]:
    """Queries the internal RAG knowledge base using the planned queries."""
    queries = state.get("queries", [])
    if not queries:
        task = state.get("task")
        if task:
            queries = [task.goal]
        else:
            return {"retrieved_evidence": []}

    tool_client = InternalDataToolClient()
    all_evidence: List[Evidence] = []
    seen_contents = set()

    for q in queries:
        try:
            results = tool_client.search_internal_knowledge(query=q, top_k=3)
            for ev in results:
                # Deduplicate evidence by snippet content hash
                content_key = (ev.excerpt or ev.extracted_fact).strip()[:100]
                if content_key not in seen_contents:
                    seen_contents.add(content_key)
                    all_evidence.append(ev)
        except Exception as e:
            logger.error("Error retrieving evidence for query '%s': %s", q, e)

    return {"retrieved_evidence": all_evidence}


def analyze_and_synthesize_node(state: InternalAgentState) -> Dict[str, Any]:
    """Analyzes the retrieved evidence and extracts structured analytical findings."""
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
                "Internal vector index returned 0 matching results.",
                "Ensure internal handbook and strategy documents are indexed in data/internal.",
            ],
        }

    findings: List[Finding] = []
    limitations: List[str] = [
        "Internal company data is adapted/synthetic for GitLab pilot demonstration purposes in accordance with project constraints.",
    ]

    # Synthesize findings grounded in retrieved evidence items
    for ev in evidence_list:
        category = _categorize_domain_category(ev)
        statement = _extract_core_statement(ev)
        confidence = float(ev.metadata.get("confidence_score", 0.9)) if ev.metadata else 0.9

        finding = Finding(
            statement=statement,
            category=category,
            evidence_refs=[
                EvidenceRef(evidence_id=ev.evidence_id, relation=EvidenceRelation.SUPPORTS)
            ],
            confidence=confidence,
            limitations=[],
        )
        findings.append(finding)

    # Construct synthesized summary
    summary_lines = [
        f"Internal Intelligence Assessment for '{task.goal if task else 'GitLab'}':",
        f"- Analyzed {len(evidence_list)} internal evidence sources across product architecture, commercial tiers, and strategy.",
    ]
    for f in findings[:3]:
        summary_lines.append(f"• [{f.category.value.upper()}] {f.statement}")

    summary_text = "\n".join(summary_lines)

    return {
        "findings": findings,
        "summary": summary_text,
        "limitations_and_gaps": limitations,
    }


def format_worker_result_node(state: InternalAgentState) -> Dict[str, Any]:
    """Packages the findings and evidence into the standard WorkerResult contract."""
    task: ResearchTask = state.get("task")
    findings = state.get("findings", [])
    evidence = state.get("retrieved_evidence", [])
    summary = state.get("summary", "Internal intelligence research completed.")
    limitations = state.get("limitations_and_gaps", [])

    status = WorkerStatus.COMPLETED
    if not evidence:
        status = WorkerStatus.NO_EVIDENCE
    elif not findings:
        status = WorkerStatus.PARTIAL

    task_id = task.task_id if task else str(uuid.uuid4())
    attempt = task.attempt if task else 1

    result = WorkerResult(
        task_id=task_id,
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        status=status,
        attempt=attempt,
        findings=findings,
        evidence=evidence,
        gaps=limitations,
        conflicts=[],
        confidence=0.9 if findings else 0.0,
        metadata={
            "query_count": len(state.get("queries", [])),
            "evidence_count": len(evidence),
            "finding_count": len(findings),
            "summary": summary,
        },
    )

    return {"worker_result": result}


def _categorize_domain_category(ev: Evidence) -> FindingCategory:
    """Categorizes the evidence into standard FindingCategory enum."""
    text = f"{ev.source_title or ''} {ev.source_url or ''} {ev.extracted_fact} {ev.excerpt or ''}".lower()
    if "pricing" in text or "tier" in text or "margin" in text or "cost" in text or "pro" in text or "enterprise" in text:
        return FindingCategory.PRICING_PACKAGING
    if "battlecard" in text or "copilot" in text or "vs" in text or "competitor" in text:
        return FindingCategory.POSITIONING
    if "adoption" in text or "telemetry" in text or "metric" in text:
        return FindingCategory.MARKET_SIGNAL
    if "risk" in text or "roadblock" in text:
        return FindingCategory.RISK
    if "opportunity" in text:
        return FindingCategory.OPPORTUNITY
    if "architecture" in text or "gateway" in text or "privacy" in text or "feature" in text:
        return FindingCategory.PRODUCT_FEATURE
    return FindingCategory.OTHER


def _extract_core_statement(ev: Evidence) -> str:
    """Extracts a succinct, high-quality factual statement from the evidence content."""
    if ev.extracted_fact and len(ev.extracted_fact) > 20:
        return ev.extracted_fact

    content = ev.excerpt or ev.extracted_fact or ""
    lines = [line.strip() for line in content.split("\n") if line.strip() and not line.startswith("#")]
    if not lines:
        return f"Internal documentation excerpt from {ev.source_title or ev.source_url or 'internal source'}."

    for line in lines:
        clean = line.lstrip("-*1234567890. ").strip()
        if len(clean) > 25 and not clean.endswith(":") and not clean.startswith("**"):
            if len(clean) > 220:
                clean = clean[:217] + "..."
            return clean

    first = lines[0].lstrip("-*1234567890. ").strip()
    if len(first) > 220:
        first = first[:217] + "..."
    return first or f"Strategic documentation insight from {ev.source_title}."
