"""LangGraph assembly for the Cross-Signal Agent.

Flow:

    START -> prepare --(too few findings)--> finalize_empty --+
                |                                              |
                +--(Send x4 lenses, parallel)--> detect_signals|
                                                     |         |
                                               merge_validate  |
                                                     |         |
                                                 summarize     |
                                                     |         |
                                                     +--> assemble --> END
"""

from __future__ import annotations
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from ..contracts.CrossSignal import (
    CrossSignalInput,
    CrossSignalOutput,
)

from .nodes import CrossSignalNodes
from .state import CrossSignalSettings, CrossSignalState

load_dotenv()  # Load environment variables from .env file

def build_cross_signal_graph(
    llm: BaseChatModel | None = None,
    settings: CrossSignalSettings | None = None,
):
    settings = settings or CrossSignalSettings()
    llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=os.getenv("OPENAI_API_KEY"),
    max_retries=4,
)
    nodes = CrossSignalNodes(llm, settings)

    g = StateGraph(CrossSignalState)

    g.add_node("prepare", nodes.prepare)
    g.add_node("detect_signals", nodes.detect_signals)
    g.add_node("merge_validate", nodes.merge_validate)
    g.add_node("summarize", nodes.summarize)
    g.add_node("finalize_empty", nodes.finalize_empty)
    g.add_node("assemble", nodes.assemble)

    g.add_edge(START, "prepare")
    g.add_conditional_edges(
        "prepare",
        nodes.route_after_prepare,
        ["detect_signals", "finalize_empty"],
    )
    # All parallel lens branches join here (LangGraph waits for every Send).
    g.add_edge("detect_signals", "merge_validate")
    g.add_edge("merge_validate", "summarize")
    g.add_edge("summarize", "assemble")
    g.add_edge("finalize_empty", "assemble")
    g.add_edge("assemble", END)

    return g.compile()


async def run_cross_signal(
    cross_signal_input: CrossSignalInput,
    graph=None,
) -> CrossSignalOutput:
    graph = graph or build_cross_signal_graph()
    final_state = await graph.ainvoke({"input": cross_signal_input})
    return final_state["output"]


# # --------------------------------------------------------------------------
# # Example
# # --------------------------------------------------------------------------
# if __name__ == "__main__":
#     import asyncio

#     from backend.src.edrak.contracts.cross_signal import build_cross_signal_input

#     async def main(verification_result, business_request):
#         cs_input = build_cross_signal_input(verification_result, business_request)
#         output = await run_cross_signal(cs_input)
#         print(output.model_dump_json(indent=2))

#     # asyncio.run(main(verification_result, business_request))
