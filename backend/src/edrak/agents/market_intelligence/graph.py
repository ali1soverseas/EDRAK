from langgraph.graph import END, START, StateGraph

from edrak.agents.market_intelligence.nodes import (
    fill_gaps,
    log_block,
    output_node,
    task_executor,
    task_planner,
)
from edrak.agents.market_intelligence.state import MarketAgentState, empty_market_state
from datetime import datetime

from edrak.contracts import (
    Evidence,
    EvidenceRef,
    Finding,
    FindingCategory,
    ResearchTask,
    SourceType,
    WorkerResult,
    WorkerStatus,
    WorkerType,
    utcnow,
)

_STATUS_CONFIDENCE = {
    WorkerStatus.COMPLETED: 0.8,
    WorkerStatus.PARTIAL: 0.55,
    WorkerStatus.NO_EVIDENCE: 0.2,
    WorkerStatus.FAILED: 0.0,
}

_STATUS_ERROR = {
    WorkerStatus.COMPLETED: "none",
    WorkerStatus.PARTIAL: "Some research tasks were skipped.",
    WorkerStatus.NO_EVIDENCE: "No usable market evidence was collected.",
    WorkerStatus.FAILED: "Market research failed to produce findings.",
}


def task_router(state: MarketAgentState) -> str:
    """Route: more tasks -> executor; open gaps -> one search pass; else output."""
    idx = state.get("current_task_idx", 0)
    task_list = state.get("task_list", [])
    remaining = [task for task in task_list[idx:] if task["status"] in ("pending", "running")]
    if remaining:
        label = remaining[0]["description"][:50]
        print(f"\n  [market_intelligence] TASK ROUTER: Next task: {label}")
        return "task_executor"
    gaps = [gap for gap in (state.get("market_gaps") or []) if gap]
    if gaps and not state.get("gap_fill_done"):
        print(f"\n  [market_intelligence] TASK ROUTER: {len(gaps)} gap(s) -- searching again")
        return "fill_gaps"
    print("\n  [market_intelligence] TASK ROUTER: All tasks complete -- proceeding to output_node")
    return "output_node"


def build_graph():
    """
    Sequential graph:
      task_planner
        -> task_executor  (loops via task_router until all tasks processed)
        -> fill_gaps once, when gaps remain, then task_executor again
        -> output_node
    """
    graph = StateGraph(MarketAgentState)

    graph.add_node("task_planner", task_planner)
    graph.add_node("task_executor", task_executor)
    graph.add_node("fill_gaps", fill_gaps)
    graph.add_node("output_node", output_node)

    graph.add_edge(START, "task_planner")
    graph.add_edge("task_planner", "task_executor")
    graph.add_conditional_edges(
        "task_executor",
        task_router,
        {
            "task_executor": "task_executor",
            "fill_gaps": "fill_gaps",
            "output_node": "output_node",
        },
    )
    graph.add_edge("fill_gaps", "task_executor")
    graph.add_edge("output_node", END)

    return graph.compile()


_COMPILED_GRAPH = None


def get_graph():
    """Reuse one compiled graph across tasks in a process."""
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_graph()
    return _COMPILED_GRAPH


def format_task_context(task: ResearchTask) -> str:
    """Flatten the shared request context into the agent's private string field."""
    profile = task.company_profile
    ctx = task.business_context
    lines = [f"Company: {profile.name}"]
    if profile.aliases:
        lines.append(f"Aliases: {', '.join(profile.aliases)}")
    if profile.products:
        lines.append(f"Products: {', '.join(profile.products)}")
    if profile.notes:
        lines.append(profile.notes)
    lines.append(f"Use case: {ctx.use_case.value}")
    if ctx.targets:
        lines.append(f"Targets: {', '.join(ctx.targets)}")
    if ctx.focus_areas:
        lines.append(f"Focus areas: {', '.join(ctx.focus_areas)}")
    lines.append(f"Task focus: {task.focus}")
    if ctx.constraints:
        lines.append(f"Constraints: {', '.join(ctx.constraints)}")
    return "\n".join(lines)


def _evidence_from_private_item(item: dict, quote: str = "") -> Evidence:
    """The fact is this page's own excerpt, never another source's quote."""
    source = item.get("source", "")
    text = (item.get("text") or item.get("excerpt") or "").strip()
    fact = (text or "Source text was not retained.")[:800]
    excerpt = text[:2000] or None
    if item.get("type") == "url":
        return Evidence(
            source_type=SourceType.WEB_PAGE,
            source_url=source or None,
            source_title=source or None,
            extracted_fact=fact,
            excerpt=excerpt,
        )
    return Evidence(
        source_type=SourceType.OTHER,
        extracted_fact=fact,
        excerpt=excerpt,
        metadata={"endpoint": source} if source else {},
    )


def worker_result_from_state(
    task: ResearchTask,
    state: MarketAgentState,
    started_at: datetime | None = None,
) -> WorkerResult:
    started = started_at or utcnow()
    evidence: list[Evidence] = []
    findings: list[Finding] = []

    for item in state.get("market_findings", []):
        claim = item.get("claim", "").strip()
        if not claim or claim.lower().startswith("data retrieved for"):
            continue
        raw_evidence = item.get("evidence", [])
        refs: list[EvidenceRef] = []
        for raw in raw_evidence:
            ev = _evidence_from_private_item(raw)
            evidence.append(ev)
            refs.append(EvidenceRef(evidence_id=ev.evidence_id))
        stored = item.get("confidence")
        # A finding carries the confidence its analysis node computed. The
        # fallback is deliberately low: this path receives private evidence
        # items rather than analysed claims, so a missing value means nothing
        # scored it, and claiming mid-confidence would overstate the finding.
        confidence_value = float(stored) if isinstance(stored, (int, float)) else 0.35
        findings.append(
            Finding(
                statement=claim,
                category=FindingCategory.MARKET_SIGNAL,
                evidence_refs=refs,
                confidence=confidence_value,
                limitations=[f"Derived from research task: {item['task']}"] if item.get("task") else [],
            )
        )

    report = state.get("final_report") or {}
    summary = report.get("task_summary") or {}
    skipped = summary.get("skipped", 0)
    gaps = [gap for gap in (report.get("gaps") or state.get("market_gaps") or []) if gap]
    if not findings and not gaps:
        gaps = ["No market findings were produced."]

    if not findings:
        status = WorkerStatus.NO_EVIDENCE if (skipped or gaps) else WorkerStatus.FAILED
    elif gaps or skipped:
        status = WorkerStatus.PARTIAL
    else:
        status = WorkerStatus.COMPLETED

    if findings:
        confidence = sum(finding.confidence or 0.0 for finding in findings) / len(findings)
    else:
        confidence = _STATUS_CONFIDENCE[status]

    result = WorkerResult(
        task_id=task.task_id,
        worker=task.worker,
        status=status,
        attempt=task.attempt,
        findings=findings,
        evidence=evidence,
        gaps=gaps,
        confidence=round(confidence, 2),
        started_at=started,
        completed_at=utcnow(),
        error=_STATUS_ERROR[status],
    )
    log_block("market_intelligence", "OUTPUT", result.model_dump_json(indent=2))
    return result


def run(task: ResearchTask) -> WorkerResult:
    """Accept ResearchTask, run the market graph, return WorkerResult."""
    started_at = utcnow()
    app = get_graph()
    final_state = app.invoke(
        empty_market_state(
            run_id=task.parent_request_id,
            goal=task.goal,
            business_context=format_task_context(task),
        ),
        config={
            "recursion_limit": 100,
            "configurable": {"thread_id": f"market-{task.task_id}"},
        },
    )
    return worker_result_from_state(task, final_state, started_at=started_at)


class MarketIntelligence:
    """Domain worker satisfying the shared Worker protocol."""

    worker_type = WorkerType.MARKET_INTELLIGENCE

    def run(self, task: ResearchTask) -> WorkerResult:
        return run(task)
