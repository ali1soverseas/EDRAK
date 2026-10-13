"""grid -> harvest -> (hunt, at most twice per figure) -> finish."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from edrak.contracts import BusinessRequest, ResearchTask, WorkerResult

from .nodes import build_grid, finish, harvest, hunt, route_after_check
from .state import MetricsState


def build_graph():
    g = StateGraph(MetricsState)
    g.add_node("grid", build_grid)
    g.add_node("harvest", harvest)
    g.add_node("hunt", hunt)
    g.add_node("finish", finish)
    g.set_entry_point("grid")
    g.add_edge("grid", "harvest")
    g.add_conditional_edges("harvest", route_after_check, {"hunt": "hunt", "finish": "finish"})
    g.add_conditional_edges("hunt", route_after_check, {"hunt": "hunt", "finish": "finish"})
    g.add_edge("finish", END)
    return g.compile()


def run(request: BusinessRequest, tasks: list[ResearchTask], results: list[WorkerResult]) -> tuple[list[WorkerResult], list[str]]:
    """The workers' results with typed metrics, checklist outcomes and any pages the hunt read; and the conflicts."""
    from edrak.metrics.custom import register

    register((getattr(request, "extras", None) or {}).get("custom_metrics") or [])  # figures the planner proposed for this field
    if not any(t.requirements for t in tasks):
        from edrak.briefing.reconcile import reconcile

        return reconcile(results)
    out = build_graph().invoke({"request": request, "tasks": tasks, "results": results}, {"recursion_limit": 20})
    return out["results"], out.get("conflicts") or []
