"""LangGraph wiring: nodes, edges and the compiled `app`.

    identify_requirements      2. preliminary research requirements
    hybrid_retrieval           3. Neo4j (earlier findings) + ChromaDB (company documents)
    assess_coverage            4. covered / insufficient / stale / unverified / missing; reuse what is known
    generate_queries           5. queries only for what is not covered
    search -> synthesize -> ground -> check_requirements   6. web research, looped by stage
    final_report -> comparison
"""

from langgraph.graph import END, START, StateGraph

from .nodes import (
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
from .prior import assess_coverage_node, hybrid_retrieval_node
from .state import CompetitorState


def after_queries_router(state: CompetitorState) -> str:
    """Search when there are queries. Otherwise record the requirement checks first if none exist yet:
    when earlier runs covered every requirement, stage 1 has nothing to search, and the reused
    findings still have to be counted as fulfilled before the report is written."""
    if state.get("search_queries"):
        return "search"
    if not state.get("requirement_checks"):
        return "check"
    return "finish"


# ============================================================
# BUILD GRAPH
# ============================================================

graph = StateGraph(CompetitorState)

graph.add_node("identify_requirements", identify_research_requirements_node)

graph.add_node("hybrid_retrieval", hybrid_retrieval_node)

graph.add_node("assess_coverage", assess_coverage_node)

graph.add_node("generate_queries", generate_queries_node)

graph.add_node("search", search_node)

graph.add_node("synthesize", synthesize_node)

# Grounding: findings must be supported by the sources they cite (no second model pass;
# the pipeline's verification stage reviews every worker's findings).
graph.add_node("ground", verify_node)

graph.add_node("check_requirements", check_research_requirements_node)

graph.add_node("prepare_next_stage", prepare_next_stage_node)

graph.add_node("final_report", final_report_node)

graph.add_node("comparison", comparison_node)

graph.add_edge(START, "identify_requirements")

graph.add_edge("identify_requirements", "hybrid_retrieval")

graph.add_edge("hybrid_retrieval", "assess_coverage")

graph.add_edge("assess_coverage", "generate_queries")

# No queries: either everything is covered (check, then finish) or a later stage found nothing to ask.
graph.add_conditional_edges(
    "generate_queries",
    after_queries_router,
    {"search": "search", "check": "check_requirements", "finish": "final_report"},
)

graph.add_edge("search", "synthesize")

graph.add_edge("synthesize", "ground")

graph.add_edge("ground", "check_requirements")

graph.add_conditional_edges(
    "check_requirements",
    research_router,
    {"research_again": "prepare_next_stage", "finish": "final_report"},
)

graph.add_edge("prepare_next_stage", "generate_queries")

graph.add_edge("final_report", "comparison")

graph.add_edge("comparison", END)

app = graph.compile()
