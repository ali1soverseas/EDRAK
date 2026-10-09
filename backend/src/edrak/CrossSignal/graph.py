from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from ..contracts.CrossSignal import (
    CrossSignalInput,
    CrossSignalOutput,
)
from ..core.llm import get_chat_model
from .nodes import CrossSignalNodes
from .state import CrossSignalSettings, CrossSignalState


def build_cross_signal_graph(
    llm: BaseChatModel | None = None,
    settings: CrossSignalSettings | None = None,
):
    """
    Build and compile the Cross-Signal LangGraph.

    Pipeline:

        START
          ↓
        prepare
          ↓
        detect_signals
          ↓
        collect_signals
          ↓
        summarize
          ↓
        assemble
          ↓
        END

    Responsibilities:

    - prepare:
        Validate and prepare verified findings.
        Build lookup tables, aliases, and compact LLM context.

    - detect_signals:
        Ask the LLM to identify meaningful relationships between
        verified findings.

    - collect_signals:
        Perform deterministic structural normalization:
        resolving finding references, removing duplicates,
        validating minimum supporting evidence, and deriving
        domains/evidence quality.

    - summarize:
        Ask the LLM to summarize the normalized signals.

    - assemble:
        Build the final CrossSignalOutput.

    Important:
        This graph does NOT perform verification.
        This graph does NOT make recommendations.
        This graph does NOT use hardcoded stopwords or
        recommendation-language filtering.
    """

    settings = settings or CrossSignalSettings()

    if llm is None:
        llm = get_chat_model(settings.model, temperature=settings.temperature)

    nodes = CrossSignalNodes(
        llm=llm,
        settings=settings,
    )

    graph = StateGraph(CrossSignalState)

    # ---------------------------------------------------------
    # Nodes
    # ---------------------------------------------------------

    graph.add_node(
        "prepare",
        nodes.prepare,
    )

    graph.add_node(
        "detect_signals",
        nodes.detect_signals,
    )

    graph.add_node(
        "collect_signals",
        nodes.collect_signals,
    )

    graph.add_node(
        "summarize",
        nodes.summarize,
    )

    graph.add_node(
        "assemble",
        nodes.assemble,
    )

    # ---------------------------------------------------------
    # Entry point
    # ---------------------------------------------------------

    graph.add_edge(
        START,
        "prepare",
    )

    # ---------------------------------------------------------
    # Preparation routing
    # ---------------------------------------------------------

    graph.add_conditional_edges(
        "prepare",
        nodes.route_after_prepare,
        {
            "detect_signals": "detect_signals",
            "assemble": "assemble",
        },
    )

    # ---------------------------------------------------------
    # Signal detection
    # ---------------------------------------------------------

    graph.add_edge(
        "detect_signals",
        "collect_signals",
    )

    # ---------------------------------------------------------
    # Deterministic normalization
    # ---------------------------------------------------------

    graph.add_edge(
        "collect_signals",
        "summarize",
    )

    # ---------------------------------------------------------
    # Summary generation
    # ---------------------------------------------------------

    graph.add_edge(
        "summarize",
        "assemble",
    )

    # ---------------------------------------------------------
    # Final output
    # ---------------------------------------------------------

    graph.add_edge(
        "assemble",
        END,
    )

    return graph.compile()


async def run_cross_signal(
    cross_signal_input: CrossSignalInput,
    graph=None,
) -> CrossSignalOutput:
    """
    Execute the Cross-Signal graph.

    Parameters
    ----------
    cross_signal_input:
        Validated CrossSignalInput containing only verified findings.

    graph:
        Optional precompiled LangGraph instance.
        Useful for dependency injection and testing.

    Returns
    -------
    CrossSignalOutput
        Fully validated Cross-Signal result.
    """
    print("\n[cross_signal] Running Cross Signal Agent")

    if not isinstance(cross_signal_input, CrossSignalInput):
        cross_signal_input = CrossSignalInput.model_validate(
            cross_signal_input
        )

    if graph is None:
        graph = build_cross_signal_graph()

    final_state = await graph.ainvoke(
        {
            "input": cross_signal_input,
        }
    )

    if not isinstance(final_state, dict):
        raise RuntimeError(
            "Cross-Signal graph returned an invalid state."
        )

    raw_output = final_state.get("output")

    if raw_output is None:
        raise RuntimeError(
            "Cross-Signal graph completed without producing an output."
        )

    if isinstance(raw_output, CrossSignalOutput):
        return raw_output

    return CrossSignalOutput.model_validate(raw_output)