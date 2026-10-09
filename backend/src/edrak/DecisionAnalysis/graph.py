
from langgraph.graph import END, START, StateGraph

from .state import DecisionAnalysisState
from .nodes import (
    prepare_context_node,
    analyze_node,
    validate_and_assemble_node,
)


def build_decision_analysis_graph():
    builder = StateGraph(DecisionAnalysisState)

    builder.add_node("prepare_context", prepare_context_node)
    builder.add_node("analyze", analyze_node)
    builder.add_node(
        "validate_and_assemble",
        validate_and_assemble_node,
    )

    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", "analyze")
    builder.add_edge("analyze", "validate_and_assemble")
    builder.add_edge("validate_and_assemble", END)

    return builder.compile()