
from __future__ import annotations

import logging
from typing import Any

from ..contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
    DecisionAnalysisResult,
)
from .graph import build_decision_analysis_graph

logger = logging.getLogger(__name__)


def _get_value(obj: Any, field: str, default: Any = None) -> Any:
    """Safely read a field from a Pydantic model or dictionary."""
    if isinstance(obj, dict):
        return obj.get(field, default)

    return getattr(obj, field, default)


def _format_value(value: Any) -> str:
    """Format enums and other values for readable terminal output."""
    if value is None:
        return "(none)"

    if hasattr(value, "value"):
        return str(value.value)

    return str(value)


def _print_list(title: str, items: Any) -> None:
    """Print a list of values in a readable terminal format."""
    print(f"\n  {title}:")

    if not items:
        print("    (none)")
        return

    if isinstance(items, str):
        items = [items]

    for index, item in enumerate(items, start=1):
        print(f"    {index}. {_format_value(item)}")


def _print_recommendation(
    recommendation: Any,
    index: int,
) -> None:
    """Print one complete question-level recommendation."""
    question = _get_value(recommendation, "question", "(unknown question)")
    details = _get_value(recommendation, "recommendation")

    print(f"\n  Recommendation {index}")
    print("  " + "-" * 68)
    print(f"  Question: {question}")

    if details is None:
        print("\n  No recommendation details were returned.")
        return

    print("\n  Recommended decision:")
    print(f"    {_format_value(_get_value(details, 'decision'))}")

    print("\n  Rationale:")
    print(f"    {_format_value(_get_value(details, 'rationale'))}")

    _print_list(
        "Recommended actions",
        _get_value(details, "actions", []),
    )

    _print_list(
        "Opportunities",
        _get_value(details, "opportunities", []),
    )

    _print_list(
        "Risks",
        _get_value(details, "risks", []),
    )

    _print_list(
        "Success criteria",
        _get_value(details, "success_criteria", []),
    )

    _print_list(
        "Supporting signal IDs",
        _get_value(details, "supporting_signal_ids", []),
    )

    confidence = _format_value(
        _get_value(details, "confidence", "unknown")
    )
    print(f"\n  Confidence: {confidence}")

    limitations = _get_value(details, "limitations", [])
    if limitations:
        _print_list("Limitations", limitations)



def _print_result(result: DecisionAnalysisResult) -> None:
    """Print the complete Decision Analysis result in a readable format."""

    def print_value(label: str, value: Any, indent: int = 4) -> None:
        prefix = " " * indent
        print(f"{prefix}{label}: {_format_value(value)}")

    def print_list(label: str, items: Any, indent: int = 4) -> None:
        print(f"\n{' ' * indent}{label}:")

        if not items:
            print(f"{' ' * (indent + 2)}(none)")
            return

        for index, item in enumerate(items, start=1):
            print(f"\n{' ' * (indent + 2)}{index}.")
            item_data = (
                item.model_dump(mode="json")
                if hasattr(item, "model_dump")
                else item
            )

            if isinstance(item_data, dict):
                for key, value in item_data.items():
                    if value is None or value == [] or value == "":
                        continue

                    formatted_key = key.replace("_", " ").capitalize()

                    if isinstance(value, list):
                        print(f"{' ' * (indent + 4)}{formatted_key}:")
                        for entry in value:
                            if isinstance(entry, dict):
                                for sub_key, sub_value in entry.items():
                                    print(
                                        f"{' ' * (indent + 6)}"
                                        f"{sub_key.replace('_', ' ').capitalize()}: "
                                        f"{_format_value(sub_value)}"
                                    )
                            else:
                                print(
                                    f"{' ' * (indent + 6)}- "
                                    f"{_format_value(entry)}"
                                )
                    elif isinstance(value, dict):
                        print(f"{' ' * (indent + 4)}{formatted_key}:")
                        for sub_key, sub_value in value.items():
                            print(
                                f"{' ' * (indent + 6)}"
                                f"{sub_key.replace('_', ' ').capitalize()}: "
                                f"{_format_value(sub_value)}"
                            )
                    else:
                        print(
                            f"{' ' * (indent + 4)}{formatted_key}: "
                            f"{_format_value(value)}"
                        )
            else:
                print(f"{' ' * (indent + 4)}{_format_value(item_data)}")

    print("\n" + "=" * 72)
    print("DECISION ANALYSIS RESULTS")
    print("=" * 72)

    print_value("Run ID", result.research_run_id, indent=2)
    print_value("Analysis ID", result.analysis_id, indent=2)
    print_value("Status", result.status, indent=2)
    print_value("Business goal", result.business_goal, indent=2)

    print("\n  Executive summary:")
    print(f"    {result.executive_summary or '(none)'}")

    print_list("Question-level recommendations", result.question_recommendations)
    print_list("Opportunities", result.opportunities)
    print_list("Risks", result.risks)
    print_list("Priorities", result.priorities)
    print_list("Recommended actions", result.recommended_actions)
    print_list("Decision questions", result.decision_questions)
    print_list(
        "Additional information needed",
        result.additional_information_needed,
    )
    print_list("Limitations", result.limitations)
    print_list("Warnings", result.warnings)

    if result.metadata:
        print_list("Metadata", [result.metadata])

    print("\n" + "=" * 72)


async def run_decision_analysis(
    decision_input: DecisionAnalysisInput,
) -> DecisionAnalysisResult:
    """
    Run the Decision Analysis graph using completed Cross-Signal output.

    Args:
        decision_input:
            Business request and completed Cross-Signal output.

    Returns:
        A validated DecisionAnalysisResult.
    """
    run_id = decision_input.research_run_id

    print(f"\n[decision_analysis] Starting for run_id={run_id}")

    cross_signal = decision_input.cross_signal
    cross_signal_status = str(
        _format_value(_get_value(cross_signal, "status", ""))
    ).strip().lower()

    if cross_signal_status != "completed":
        raise ValueError(
            "Decision Analysis requires completed Cross-Signal output. "
            f"Received status: {cross_signal_status or 'missing'}"
        )

    graph = build_decision_analysis_graph()
    print(f"[decision_analysis] Graph built for run_id={run_id}")

    try:
        final_state = await graph.ainvoke(
            {
                "input": decision_input,
                "warnings": [],
            }
        )

    except Exception:
        logger.exception(
            "Decision Analysis graph failed for run_id=%s",
            run_id,
        )
        raise

    result = final_state.get("result")
    print("****************[decision_analysis] FINAL RESULT:" , result)

    if result is None:
        error = final_state.get("error")
        raise RuntimeError(
            "Decision Analysis graph finished without a result. "
            f"Graph error: {error or 'No error details provided.'}"
        )

    if not isinstance(result, DecisionAnalysisResult):
        result = DecisionAnalysisResult.model_validate(result)

    logger.info(
        "Decision Analysis completed: run_id=%s, status=%s, "
        "opportunities=%d, risks=%d, actions=%d, recommendations=%d",
        result.research_run_id,
        _format_value(result.status),
        len(result.opportunities or []),
        len(result.risks or []),
        len(result.recommended_actions or []),
        len(result.question_recommendations or []),
    )

    _print_result(result)

    return result