from edrak.contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
    CrossSignalOutput,
)

from edrak.contracts.DecisionAnalysis import CrossSignalOutput


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


if __name__ == "__main__":
    test_decision_analysis_input_requires_cross_signal()
    test_cross_signal_output_contract()