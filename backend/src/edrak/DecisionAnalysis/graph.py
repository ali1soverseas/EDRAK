from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from langchain_core.language_models import BaseChatModel

from .nodes import (
    make_analyze_node,
    make_validate_and_assemble_node,
    prepare_context_node,
)
from .state import DecisionAnalysisState


def build_decision_analysis_graph():
    """
    Build and compile the Decision Analysis graph.

    Flow:
        START
          -> prepare_context
          -> analyze
          -> validate_and_assemble
          -> END
    """

    builder = StateGraph(DecisionAnalysisState)

    builder.add_node(
        "prepare_context",
        prepare_context_node,
    )

    builder.add_node(
        "analyze",
        make_analyze_node(),
    )

    builder.add_node(
        "validate_and_assemble",
        make_validate_and_assemble_node(),
    )

    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", "analyze")
    builder.add_edge("analyze", "validate_and_assemble")
    builder.add_edge("validate_and_assemble", END)

    return builder.compile()