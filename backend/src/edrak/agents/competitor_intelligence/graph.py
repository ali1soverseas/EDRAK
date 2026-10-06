"""LangGraph wiring: nodes, edges and the compiled `app`."""

from langgraph.graph import END, START, StateGraph

from .nodes import (
    after_queries_router,
    check_research_requirements_node,
    comparison_node,
    final_report_node,
    generate_queries_node,
    identify_research_requirements_node,
    prepare_next_stage_node,
    research_router,
    search_node,
    synthesize_node,
    verify_node,
)
from .state import CompetitorState


# ============================================================
# BUILD GRAPH
# ============================================================

graph = StateGraph(CompetitorState)

graph.add_node("identify_requirements", identify_research_requirements_node)

graph.add_node("generate_queries", generate_queries_node)

graph.add_node("search", search_node)

graph.add_node("synthesize", synthesize_node)

graph.add_node("verify", verify_node)

graph.add_node("check_requirements", check_research_requirements_node)

graph.add_node("prepare_next_stage", prepare_next_stage_node)

graph.add_node("final_report", final_report_node)

graph.add_node("comparison", comparison_node)

graph.add_edge(START, "identify_requirements")

graph.add_edge("identify_requirements", "generate_queries")

# If no valid queries survive, stop instead of wasting a stage
graph.add_conditional_edges(
    "generate_queries",
    after_queries_router,
    {"search": "search", "finish": "final_report"},
)

graph.add_edge("search", "synthesize")

graph.add_edge("synthesize", "verify")

graph.add_edge("verify", "check_requirements")

graph.add_conditional_edges(
    "check_requirements",
    research_router,
    {"research_again": "prepare_next_stage", "finish": "final_report"},
)

graph.add_edge("prepare_next_stage", "generate_queries")

graph.add_edge("final_report", "comparison")

graph.add_edge("comparison", END)

app = graph.compile()
