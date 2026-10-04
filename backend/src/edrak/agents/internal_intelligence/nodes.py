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
from edrak.contracts.evidence import Evidence
from edrak.contracts.result import Finding, WorkerResult, WorkerStatus
from edrak.contracts.task import ResearchTask
from edrak.mcp.client import InternalDataToolClient

logger = logging.getLogger(__name__)


def plan_queries_node(state: InternalAgentState) -> Dict[str, Any]:
    """Generates focused internal retrieval queries based on the assigned ResearchTask."""
    task: ResearchTask = state.get("task")
    if not task:
        return {"queries": [], "error": "No task provided in state"}

    queries: List[str] = []

    # Priority 1: Key questions provided in task
    if task.key_questions:
        queries.extend(task.key_questions[:3])

    # Priority 2: Objective & scope
    queries.append(f"{task.objective} {task.scope}".strip())

    # Priority 3: Add domain-specific targeted queries for internal knowledge
    obj_lower = (task.objective + " " + task.scope).lower()
    if "duo" in obj_lower or "ai" in obj_lower or "copilot" in obj_lower:
        queries.append("GitLab Duo architecture AI gateway Code Suggestions Chat")
        queries.append("GitLab Duo pricing add-on Pro Enterprise margins")
        queries.append("GitLab Duo internal OKRs roadmap adoption metrics")
    elif "pricing" in obj_lower or "revenue" in obj_lower:
        queries.append("GitLab pricing tiers Premium Ultimate add-on gross margins")
    elif "strategy" in obj_lower or "handbook" in obj_lower:
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
            queries = [task.objective]
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
                content_key = ev.content.strip()[:100]
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
        # Graceful handling when no internal evidence is found
        return {
            "findings": [],
            "summary": (
                f"No matching internal documents or handbook entries were found for task: "
                f"'{task.objective if task else 'Unknown'}'."
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
        topic = _categorize_domain_topic(ev)
        statement = _extract_core_statement(ev)

        finding = Finding(
            id=f"find_internal_{uuid.uuid4().hex[:8]}",
            statement=statement,
            domain_topic=topic,
            confidence=ev.confidence_score,
            evidence_ids=[ev.id],
            metadata={
                "source_title": ev.title,
                "source_uri": ev.source_uri,
            },
        )
        findings.append(finding)

    # Construct synthesized summary
    summary_lines = [
        f"Internal Intelligence Assessment for '{task.objective if task else 'GitLab'}':",
        f"- Analyzed {len(evidence_list)} internal evidence sources across product architecture, commercial tiers, and strategy.",
    ]
    for f in findings[:3]:
        summary_lines.append(f"• [{f.domain_topic.upper()}] {f.statement}")

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

    status = WorkerStatus.SUCCESS
    if not evidence:
        status = WorkerStatus.NO_DATA
    elif not findings:
        status = WorkerStatus.PARTIAL

    task_id = task.task_id if task else str(uuid.uuid4())

    result = WorkerResult(
        task_id=task_id,
        worker_role="internal_intelligence",
        status=status,
        summary=summary,
        findings=findings,
        evidence=evidence,
        limitations_and_gaps=limitations,
        metadata={
            "query_count": len(state.get("queries", [])),
            "evidence_count": len(evidence),
            "finding_count": len(findings),
        },
    )

    return {"worker_result": result}


def _categorize_domain_topic(ev: Evidence) -> str:
    """Categorizes the evidence into standard domain topics."""
    title_or_uri = (ev.title + " " + ev.source_uri + " " + ev.content[:200]).lower()
    if "pricing" in title_or_uri or "tier" in title_or_uri or "margin" in title_or_uri:
        return "pricing_and_commercials"
    if "battlecard" in title_or_uri or "copilot" in title_or_uri or "vs" in title_or_uri:
        return "competitive_positioning"
    if "adoption" in title_or_uri or "telemetry" in title_or_uri or "metric" in title_or_uri:
        return "internal_telemetry"
    if "roadmap" in title_or_uri or "okr" in title_or_uri or "roadblock" in title_or_uri:
        return "roadmap_and_okrs"
    if "architecture" in title_or_uri or "gateway" in title_or_uri or "privacy" in title_or_uri:
        return "product_architecture"
    if "values" in title_or_uri or "handbook" in title_or_uri:
        return "handbook_values"
    return "internal_strategy"


def _extract_core_statement(ev: Evidence) -> str:
    """Extracts a succinct, high-quality factual statement from the evidence content."""
    lines = [line.strip() for line in ev.content.split("\n") if line.strip() and not line.startswith("#")]
    if not lines:
        return f"Internal documentation excerpt from {ev.title or ev.source_uri}."

    # Find the first substantive line (not just a bullet header like '1. **Positive Feedback**:')
    for line in lines:
        clean = line.lstrip("-*1234567890. ").strip()
        # If line has more substance than just a short title
        if len(clean) > 25 and not clean.endswith(":") and not clean.startswith("**"):
            if len(clean) > 220:
                clean = clean[:217] + "..."
            return clean

    # Fallback to the first line if all lines are short
    first = lines[0].lstrip("-*1234567890. ").strip()
    if len(first) > 220:
        first = first[:217] + "..."
    return first or f"Strategic documentation insight from {ev.title}."
