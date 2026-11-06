"""The Company Research Agent as a LangGraph graph.

    seed -> crawl -> pdfs -> extract -> gap_check -+-> crawl      (up to 2 extra rounds, on important gaps)
                                                   +-> own_social -> third_party? -> merge_and_save -> finish

`seed` and `crawl` go straight to `finish` when there is no website to read. `third_party` runs only
when the config flag is on.
"""

from __future__ import annotations

from typing import Optional

from langgraph.graph import END, StateGraph

from edrak.company_research.agent.nodes import Nodes, safe
from edrak.company_research.agent.state import ResearchDeps, ResearchState

RECURSION_LIMIT = 60  # far above the longest path: seed, 3 rounds of 4 steps, and the tail


def route_after_seed(state: ResearchState) -> str:
    return "finish" if state.get("error") else "crawl"


def route_after_crawl(state: ResearchState) -> str:
    return "finish" if state.get("error") else "pdfs"


def route_after_gaps(state: ResearchState) -> str:
    return "crawl" if state.get("targets") else "own_social"


def make_route_after_social(deps: ResearchDeps):
    def route(state: ResearchState) -> str:
        return "third_party" if deps.options.third_party and state.get("empty") else "merge_and_save"

    return route


def build_research_graph(deps: Optional[ResearchDeps] = None):
    """Compile the graph. Nodes use `deps` for the network, the model and the limits."""
    deps = deps or ResearchDeps()
    nodes = Nodes(deps)
    graph = StateGraph(ResearchState)
    graph.add_node("seed", safe("seed", nodes.seed, deps, fatal=True))
    graph.add_node("crawl", safe("crawl", nodes.crawl, deps))  # a failure on the first round sets the error itself
    graph.add_node("pdfs", safe("pdfs", nodes.pdfs, deps))
    graph.add_node("extract", safe("extract", nodes.extract, deps, on_error={"new_docs": []}))
    graph.add_node("gap_check", safe("gap_check", nodes.gap_check, deps, on_error={"targets": []}))
    graph.add_node("own_social", safe("own_social", nodes.own_social, deps))
    graph.add_node("third_party", safe("third_party", nodes.third_party, deps))
    graph.add_node("merge_and_save", safe("merge_and_save", nodes.merge_and_save, deps, fatal=True))
    graph.add_node("finish", nodes.finish)  # writes the run's last state itself; the manager covers a failure here

    graph.set_entry_point("seed")
    graph.add_conditional_edges("seed", route_after_seed, {"crawl": "crawl", "finish": "finish"})
    graph.add_conditional_edges("crawl", route_after_crawl, {"pdfs": "pdfs", "finish": "finish"})
    graph.add_edge("pdfs", "extract")
    graph.add_edge("extract", "gap_check")
    graph.add_conditional_edges("gap_check", route_after_gaps, {"crawl": "crawl", "own_social": "own_social"})
    graph.add_conditional_edges(
        "own_social", make_route_after_social(deps), {"third_party": "third_party", "merge_and_save": "merge_and_save"}
    )
    graph.add_edge("third_party", "merge_and_save")
    graph.add_edge("merge_and_save", "finish")
    graph.add_edge("finish", END)
    return graph.compile()
