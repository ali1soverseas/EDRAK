
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from .nodes import (
    make_analyze_node,
    make_draft_options_node,
    make_validate_and_assemble_node,
    prepare_context_node,
)
from .state import DecisionAnalysisState


def build_decision_analysis_graph(model: BaseChatModel | None = None):
    builder = StateGraph(DecisionAnalysisState)

    builder.add_node("prepare_context", prepare_context_node)
    builder.add_node("analyze", make_analyze_node(model))
    builder.add_node("draft_options", make_draft_options_node())
    builder.add_node("validate_and_assemble", make_validate_and_assemble_node())

    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", "analyze")
    builder.add_edge("analyze", "draft_options")
    builder.add_edge("draft_options", "validate_and_assemble")
    builder.add_edge("validate_and_assemble", END)

    return builder.compile()