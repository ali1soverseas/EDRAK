from langgraph.graph import END, START, StateGraph

from edrak.agents.market_intelligence.nodes import output_node, task_executor, task_planner
from edrak.agents.market_intelligence.state import MarketAgentState, empty_market_state
from edrak.contracts import Evidence, Finding, ResearchTask, WorkerResult


def task_router(state: MarketAgentState) -> str:
    """Route: more pending tasks -> task_executor; all done -> output_node."""
    idx = state.get("current_task_idx", 0)
    task_list = state.get("task_list", [])
    remaining = [task for task in task_list[idx:] if task["status"] in ("pending", "running")]
    if remaining:
        label = remaining[0]["description"][:50]
        print(f"\n  [TASK ROUTER] Next task: {label}")
        return "task_executor"
    print("\n  [TASK ROUTER] All tasks complete -- proceeding to output_node")
    return "output_node"


def build_graph():
    """
    Sequential graph:
      task_planner
        -> task_executor  (loops via task_router until all tasks processed)
        -> output_node
    """
    graph = StateGraph(MarketAgentState)

    graph.add_node("task_planner", task_planner)
    graph.add_node("task_executor", task_executor)
    graph.add_node("output_node", output_node)

    graph.add_edge(START, "task_planner")
    graph.add_edge("task_planner", "task_executor")
    graph.add_conditional_edges(
        "task_executor",
        task_router,
        {"task_executor": "task_executor", "output_node": "output_node"},
    )
    graph.add_edge("output_node", END)

    return graph.compile()


def worker_result_from_state(task: ResearchTask, state: MarketAgentState) -> WorkerResult:
    findings = [
        Finding(
            claim=item.get("claim", ""),
            evidence=[
                Evidence(type=ev.get("type", "source"), source=ev.get("source", ""))
                for ev in item.get("evidence", [])
            ],
            task=item.get("task"),
        )
        for item in state.get("market_findings", [])
    ]

    report = state.get("final_report") or {}
    summary = report.get("task_summary") or {}
    done = summary.get("done", len(findings))
    skipped = summary.get("skipped", 0)

    if done == 0:
        status = "failed"
    elif skipped:
        status = "partial"
    else:
        status = "success"

    return WorkerResult(
        worker="market",
        task_id=task.task_id,
        run_id=task.run_id,
        status=status,
        findings=findings,
    )


def run(task: ResearchTask) -> WorkerResult:
    """Accept ResearchTask, run the unchanged market graph, return WorkerResult."""
    app = build_graph()
    final_state = app.invoke(
        empty_market_state(
            run_id=task.run_id,
            goal=task.goal,
            business_context=task.business_context,
        )
    )
    return worker_result_from_state(task, final_state)
