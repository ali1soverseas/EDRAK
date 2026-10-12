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
    plan_node,
    replan_node,
    verification_gate_node,
)
from .review import human_review_node, route_after_review
from .routing import route_from_plan
from .state import OrchestrationState


def build_graph(registry: WorkerRegistry, checkpointer: Any = None):
    """Compile the orchestrator.

    Workers are dispatched in parallel via ``Send``: ``route_from_plan`` emits
    one ``Send`` per task, LangGraph runs them concurrently in a thread pool,
    and ``verify`` runs once after the whole superstep completes.
    A verified run then enters ``cross_signal`` before finalize.

    After verification the run always stops at ``human_review`` (checkpoint 2) until a person
    decides: continue to synthesis, re-plan, or stop. Pausing needs a ``checkpointer``; the run
    then resumes with ``Command(resume=...)`` on the same ``thread_id``.
    """
    graph = StateGraph(OrchestrationState)

    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", make_dispatch_worker(registry))
    graph.add_node("verify", verification_gate_node)
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

    graph.add_edge("verify", "human_review")
    graph.add_conditional_edges(
        "human_review",
        route_after_review,
        ["dispatch", "cross_signal", "replan", "finalize"],
    )
    graph.add_edge("cross_signal", "decision_analysis")
    graph.add_edge("decision_analysis", "finalize")

    # A replan routes back through the plan router because either shape needs a
    # dispatch. A targeted replan keeps the original task ids and narrows
    # replan_tasks to the workers that contradicted, so it re-runs those workers
    # only; the plan itself stays whole so the untouched workers' results remain
    # attributable. The fallback replan mints a fresh plan with fresh task ids
    # and clears the results. Going straight to verify would grade an empty result
    # set and loop until the limits ran out.
    graph.add_conditional_edges(
        "replan",
        route_from_plan,
        {"dispatch": "dispatch", "finalize_failed": "finalize"},
    )

    graph.add_edge("exhausted", "cross_signal")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)