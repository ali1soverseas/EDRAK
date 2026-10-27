"""Traced version of Edrak orchestration/graph.py.

This is based on the graph.py shared in the conversation. Replace the existing
file with this version, or copy the traced_route_after_review wrapper and import
into the existing file. Existing route_after_review logic remains authoritative.
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from ..contracts.worker import WorkerRegistry
from .nodes import (
    cross_signal_node,
    exhausted_node,
    finalize_node,
    make_decision_analysis_node,
    make_dispatch_worker,
    metrics_node,
    plan_node,
    replan_node,
    verification_gate_node,
)
from .review import human_review_node, route_after_review
from .routing import route_from_plan
from .state import OrchestrationState
from .tracing import trace_event


def _route_label(route: Any) -> Any:
    """Make LangGraph route return values readable without changing them."""
    if isinstance(route, (str, int, float, bool)) or route is None:
        return route
    if isinstance(route, (list, tuple)):
        return [_route_label(item) for item in route]
    name = getattr(route, "name", None)
    value = getattr(route, "value", None)
    if name is not None:
        return str(name)
    if value is not None:
        return str(value)
    return repr(route)


def traced_route_after_review(state: OrchestrationState) -> Any:
    """Run the original review router and trace its actual result and context."""
    verification = state.get("verification")
    verification_result = state.get("verification_result")
    review_keys = (
        "human_review", "review", "review_decision", "review_action", "approval",
        "approved", "review_feedback", "review_notes", "replan_count", "replan_tasks",
        "error", "status",
    )
    review_context = {key: state.get(key) for key in review_keys if key in state}
    try:
        route = route_after_review(state)
        trace_event(
            "post_verification_routing",
            state,
            router="route_after_review",
            verification=verification,
            verification_result=verification_result,
            review_context=review_context,
            selected_route=_route_label(route),
            rationale="Actual return value from the existing route_after_review function; routing logic was not changed.",
        )
        return route
    except Exception as exc:
        trace_event(
            "post_verification_routing_error",
            state,
            router="route_after_review",
            verification=verification,
            review_context=review_context,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise


def build_graph(registry: WorkerRegistry, checkpointer: Any = None):
    """Compile the orchestrator with tracing around the post-verification route."""
    graph = StateGraph(OrchestrationState)

    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", make_dispatch_worker(registry))
    graph.add_node("verify", verification_gate_node)
    graph.add_node("metrics", metrics_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("cross_signal", cross_signal_node)
    graph.add_node("decision_analysis", make_decision_analysis_node())
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
    graph.add_edge("verify", "metrics")
    graph.add_edge("metrics", "human_review")
    graph.add_conditional_edges(
        "human_review",
        traced_route_after_review,
        ["dispatch", "cross_signal", "replan", "finalize"],
    )
    graph.add_edge("cross_signal", "decision_analysis")
    graph.add_edge("decision_analysis", "finalize")

    # Replan routes back through the plan router; this preserves existing behavior.
    graph.add_conditional_edges(
        "replan",
        route_from_plan,
        {"dispatch": "dispatch", "finalize_failed": "finalize"},
    )

    graph.add_edge("exhausted", "cross_signal")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)
