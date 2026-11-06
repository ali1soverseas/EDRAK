"""The Company Research Agent: a LangGraph graph that crawls the company's site, reads its PDFs,
extracts verified facts, fills gaps, and saves the facts as suggestions for a person to review.

`research_manager.start(workspace_id)` runs it as a tracked background run.
"""

from edrak.company_research.agent.graph import build_research_graph
from edrak.company_research.agent.manager import NoWebsite, ResearchRunManager, research_manager
from edrak.company_research.agent.search import SearchUnavailable
from edrak.company_research.agent.state import ResearchDeps, ResearchOptions, ResearchState, SearchHit

__all__ = [
    "NoWebsite",
    "ResearchDeps",
    "ResearchOptions",
    "ResearchRunManager",
    "ResearchState",
    "SearchHit",
    "SearchUnavailable",
    "build_research_graph",
    "research_manager",
]
