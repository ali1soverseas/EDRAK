from edrak.contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
    CrossSignalOutput,
)

from edrak.contracts.DecisionAnalysis import CrossSignalOutput

from edrak.DecisionAnalysis.nodes import _extract_questions


def test_decision_analysis_input_requires_cross_signal():
    try:
        DecisionAnalysisInput.model_validate({
            "research_run_id": "test-run",
            "business_request": {},
        })
    except Exception:
        print("PASS: Cross-Signal input is required.")
    else:
        raise AssertionError(
            "DecisionAnalysisInput should require cross_signal."
        )


def test_cross_signal_output_contract():
    fields = CrossSignalOutput.model_fields

    required_fields = {
        "research_run_id",
        "status",
        "signals",
        "summary",
        "decision_ready_context",
    }

    missing = required_fields - set(fields)

    assert not missing, (
        f"CrossSignalOutput is missing fields: {missing}"
    )

    print("PASS: CrossSignalOutput contract is available.")


def test_extract_questions_handles_null_decision_questions():
    cross_signal = {
        "decision_ready_context": {"decision_questions": None},
        "decision_questions": None,
    }

    assert _extract_questions(cross_signal) == []


def test_extract_questions_keeps_a_single_question_as_one_item():
    assert _extract_questions({"decision_questions": "What should we do?"}) == [
        "What should we do?"
    ]


if __name__ == "__main__":
    test_decision_analysis_input_requires_cross_signal()
    test_cross_signal_output_contract()