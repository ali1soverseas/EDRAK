"""LangGraph wiring: knowledge base first, then search only for what is missing.

    START
      -> identify_requirements   requirements and the evidence each one needs
      -> retrieve_kb             stored passages and verified profile facts per requirement
      -> assess_gaps             covered / partial / out of date / missing, per evidence item
      -> plan_gap_queries        searches for the gaps only
      -> search (if there is anything to find) or straight to synthesize
      -> synthesize -> ground -> check_requirements
      -> prepare_next_stage -> generate_queries -> search ...   (the existing loop, unchanged)
      -> final_report -> END
"""

from langgraph.graph import END, START, StateGraph

from .gap_nodes import (
    after_gap_planning_router,
    assess_gaps_node,
    plan_gap_queries_node,
    retrieve_kb_node,
)
from .nodes import (
    after_queries_router,
    check_research_requirements_node,
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

graph = StateGraph(CompetitorState)

graph.add_node("identify_requirements", identify_research_requirements_node)
graph.add_node("retrieve_kb", retrieve_kb_node)
graph.add_node("assess_gaps", assess_gaps_node)
graph.add_node("plan_gap_queries", plan_gap_queries_node)
graph.add_node("generate_queries", generate_queries_node)
graph.add_node("search", search_node)
graph.add_node("synthesize", synthesize_node)
# Grounding: findings must be supported by the sources they cite, knowledge-base passages included.
graph.add_node("ground", verify_node)
graph.add_node("check_requirements", check_research_requirements_node)
graph.add_node("prepare_next_stage", prepare_next_stage_node)
graph.add_node("final_report", final_report_node)

graph.add_edge(START, "identify_requirements")
graph.add_edge("identify_requirements", "retrieve_kb")
graph.add_edge("retrieve_kb", "assess_gaps")
graph.add_edge("assess_gaps", "plan_gap_queries")

# Nothing missing: the stored evidence goes straight to synthesis, with no search at all.
graph.add_conditional_edges(
    "plan_gap_queries",
    after_gap_planning_router,
    {"search": "search", "synthesize": "synthesize"},
)

graph.add_edge("search", "synthesize")
graph.add_edge("synthesize", "ground")
graph.add_edge("ground", "check_requirements")

graph.add_conditional_edges(
    "check_requirements",
    research_router,
    {"research_again": "prepare_next_stage", "finish": "final_report"},
)

# Later stages keep the existing query generator, driven by what check_requirements still misses.
graph.add_edge("prepare_next_stage", "generate_queries")
graph.add_conditional_edges(
    "generate_queries",
    after_queries_router,
    {"search": "search", "finish": "final_report"},
)

graph.add_edge("final_report", END)

app = graph.compile()
