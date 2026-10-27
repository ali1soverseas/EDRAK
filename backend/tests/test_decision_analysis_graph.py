import asyncio

from edrak.contracts.CrossSignal import (
    CrossSignal,
    CrossSignalOutput,
    CrossSignalSummary,
    DecisionReadyContext,
    DecisionRelevance,
    SignalEvidence,
    SignalType,
)
from edrak.contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
    DecisionAnalysisStatus,
    DecisionOption,
)
from edrak.contracts.request import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    UseCase,
)
from edrak.contracts.task import WorkerType
from edrak.contracts.verification import EvidenceQuality
from edrak.DecisionAnalysis.graph import build_decision_analysis_graph
from edrak.DecisionAnalysis.schemas import DecisionAnalysisDraft


class FakeModel:
    def __init__(self, result=None):
        self.result = result

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        return self.result


def test_decision_analysis_graph_uses_current_node_factories() -> None:
    graph = build_decision_analysis_graph(FakeModel())

    assert {
        "prepare_context",
        "analyze",
        "draft_options",
        "validate_and_assemble",
    } <= set(graph.nodes)


def _decision_input() -> DecisionAnalysisInput:
    signal_id = "signal-verified"
    signal = CrossSignal(
        signal_id=signal_id,
        signal_type=SignalType.CONVERGENCE,
        title="Competitors invest in AI accelerators",
        signal="Competitors are expanding accelerator capacity.",
        interpretation="Competition is intensifying in AI infrastructure.",
        supporting_findings=[
            SignalEvidence(finding_id="finding-1", relevance="Supports expansion"),
            SignalEvidence(finding_id="finding-2", relevance="Supports expansion"),
        ],
        domains_involved=[WorkerType.MARKET_INTELLIGENCE],
        confidence=0.8,
        evidence_quality=EvidenceQuality.MEDIUM,
        strategic_relevance="Relevant to accelerator positioning.",
        decision_relevance=DecisionRelevance.BOTH,
    )
    request = BusinessRequest(
        goal="Choose a competitive response for AI accelerators.",
        company_profile=CompanyProfile(name="NVIDIA"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["AMD", "Intel"],
        ),
    )
    return DecisionAnalysisInput(
        research_run_id="run-options",
        business_request=request,
        cross_signal=CrossSignalOutput(
            research_run_id="run-options",
            status="completed",
            signals=[signal],
            summary=CrossSignalSummary(evidence_gaps=["Regional adoption data is missing."]),
            decision_ready_context=DecisionReadyContext(
                business_goal="Choose a competitive response for AI accelerators.",
                evidence_gaps=["Customer deployment evidence is missing."],
            ),
        ),
        metadata={"blocking_gaps": ["Pricing data is incomplete."]},
    )


def test_graph_generates_and_assembles_options(monkeypatch) -> None:
    captured: dict = {}
    option = DecisionOption(
        key="A",
        title="Differentiate on performance",
        thesis="Focus on evidenced performance differentiation.",
        supporting_signal_ids=["signal-verified"],
    )

    async def fake_generate_options(context):
        captured.update(context)
        return [option], []

    monkeypatch.setattr(
        "edrak.DecisionAnalysis.nodes.generate_options",
        fake_generate_options,
    )
    decision_input = _decision_input()
    draft = DecisionAnalysisDraft(
        executive_summary="Competition is intensifying.",
        decision_questions=[
            (
                "Question: How should NVIDIA respond?\n"
                "Recommendation: Differentiate where evidence supports an advantage.\n"
                "Evidence basis: signal-verified supports intensifying competition.\n"
                "Remaining uncertainty: Pricing remains unclear."
            )
        ],
    )
    graph = build_decision_analysis_graph(FakeModel(draft))
    result_state = asyncio.run(graph.ainvoke({
        "input": decision_input,
        "warnings": [],
    }))

    assert captured["recommendation_summaries"] == [{
        "question": "How should NVIDIA respond?",
        "decision": "Differentiate where evidence supports an advantage.",
        "signal_ids": ["signal-verified"],
    }]
    assert "Regional adoption data is missing." in captured["input_metadata"]["blocking_gaps"]
    assert "Customer deployment evidence is missing." in captured["input_metadata"]["blocking_gaps"]
    assert len(result_state["result"].options) == 1
    assert result_state["result"].options[0].key == "A"
    assert result_state["result"].metadata["validated_option_count"] == 1
    assert result_state["result"].status is DecisionAnalysisStatus.COMPLETED
