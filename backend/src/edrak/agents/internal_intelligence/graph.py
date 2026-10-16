"""LangGraph workflow definition for the Internal Intelligence Worker."""

import logging
from typing import Optional
from langgraph.graph import END, START, StateGraph

from edrak.agents.internal_intelligence.nodes import (
    analyze_and_synthesize_node,
    format_worker_result_node,
    plan_queries_node,
    retrieve_evidence_node,
)
from edrak.agents.internal_intelligence.state import InternalAgentState
from edrak.contracts.result import WorkerResult
from edrak.contracts.task import ResearchTask

logger = logging.getLogger(__name__)


def create_internal_intelligence_graph():
    """Builds and compiles the LangGraph StateGraph for Internal Intelligence."""
    builder = StateGraph(InternalAgentState)

    # Register nodes
    builder.add_node("plan_queries", plan_queries_node)
    builder.add_node("retrieve_evidence", retrieve_evidence_node)
    builder.add_node("analyze_synthesize", analyze_and_synthesize_node)
    builder.add_node("format_result", format_worker_result_node)

    # Wire deterministic linear flow
    builder.add_edge(START, "plan_queries")
    builder.add_edge("plan_queries", "retrieve_evidence")
    builder.add_edge("retrieve_evidence", "analyze_synthesize")
    builder.add_edge("analyze_synthesize", "format_result")
    builder.add_edge("format_result", END)

    return builder.compile()


# Singleton compiled graph instance
internal_intelligence_graph = create_internal_intelligence_graph()


def run_internal_intelligence(task: ResearchTask) -> WorkerResult:
    """Executes the Internal Intelligence Worker graph for a given ResearchTask.

    Returns the standardized WorkerResult contract.
    """
    logger.info("Starting Internal Intelligence Worker for task %s", task.task_id)
    initial_state: InternalAgentState = {
        "task": task,
        "queries": [],
        "retrieved_evidence": [],
        "findings": [],
        "limitations_and_gaps": [],
        "summary": "",
        "worker_result": None,
        "error": None,
    }

    final_state = internal_intelligence_graph.invoke(initial_state)
    result = final_state.get("worker_result")

    if not result:
        raise RuntimeError(f"Internal Intelligence Worker failed to produce a valid WorkerResult for task {task.task_id}")

    return result
