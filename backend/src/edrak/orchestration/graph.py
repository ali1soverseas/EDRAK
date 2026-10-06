from __future__ import annotations

from langgraph.graph import END, StateGraph

from ..contracts.worker import WorkerRegistry
from .nodes import (
    exhausted_node,
    finalize_node,
    make_dispatch_worker,
    plan_node,
    replan_node,
    verification_gate_node,
)
from .routing import decide_after_verification, route_from_plan
from .state import OrchestrationState


def build_graph(registry: WorkerRegistry):
    """Compile the orchestrator.

    Workers are dispatched in parallel via ``Send``: ``route_from_plan`` emits
    one ``Send`` per task, LangGraph runs them concurrently in a thread pool,
    and ``verify`` runs once after the whole superstep completes.
    """
    graph = StateGraph(OrchestrationState)

    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", make_dispatch_worker(registry))
    graph.add_node("verify", verification_gate_node)
    graph.add_node("replan", replan_node)
    graph.add_node("exhausted", exhausted_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("plan")

    graph.add_conditional_edges(
        "plan",
        route_from_plan,
        {"dispatch": "dispatch", "finalize_failed": "finalize"},
    )

    graph.add_edge("dispatch", "verify")

    graph.add_conditional_edges(
        "verify",
        decide_after_verification,
        {
            "dispatch": "dispatch",
            "replan": "replan",
            "exhausted": "exhausted",
            "finalize": "finalize",
        },
    )

    graph.add_edge("replan", "verify")
    graph.add_edge("exhausted", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()