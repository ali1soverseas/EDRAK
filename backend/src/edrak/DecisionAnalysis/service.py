from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ..contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
    DecisionAnalysisResult,
)
from .graph import build_decision_analysis_graph


logger = logging.getLogger(__name__)

llm=ChatOpenAI(temperature=0, model_name="gpt-4o-mini")
async def run_decision_analysis(
    decision_input: DecisionAnalysisInput,
) -> DecisionAnalysisResult:
    """
    Run the Decision Analysis graph.

    Args:
        decision_input:
            Business request and completed Cross-Signal output.
        llm:
            Configured LangChain chat model.

    Returns:
        A validated DecisionAnalysisResult.
    """
    print( f"\n[decision_analysis] Starting for run_id={decision_input.research_run_id}"  )
    if decision_input.cross_signal.status.lower() != "completed":
        raise ValueError(
            "Decision Analysis requires completed Cross-Signal output."
        )

    graph = build_decision_analysis_graph()
    print(f"[decision_analysis] Graph built for run_id={decision_input.research_run_id}")
    final_state = await graph.ainvoke({
        "input": decision_input,
        "warnings": [],
    })

    result = final_state.get("result")

    if result is None:
        error = final_state.get("error")

        raise RuntimeError(
            "Decision Analysis graph finished without a result. "
            f"Graph error: {error or 'No error details provided.'}"
        )

    if isinstance(result, dict):
        result = DecisionAnalysisResult.model_validate(result)

    if not isinstance(result, DecisionAnalysisResult):
        result = DecisionAnalysisResult.model_validate(result)

    logger.info(
        "Decision Analysis completed: run_id=%s, status=%s, "
        "opportunities=%d, risks=%d, actions=%d",
        result.research_run_id,
        result.status.value,
        len(result.opportunities),
        len(result.risks),
        len(result.recommended_actions),
    )

    return result