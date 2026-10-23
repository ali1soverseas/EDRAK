from __future__ import annotations

import asyncio

from edrak.agents.customer_trends.graph import route_after_gap_check
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.schemas.common import Budget
from edrak.contracts.CrossSignal import (
    CrossSignal,
    CrossSignalInput,
    DecisionRelevance,
    SignalEvidence,
    SignalType,
)
from edrak.contracts.DecisionAnalysis import RecommendationDetails
from edrak.contracts.request import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    UseCase,
)
from edrak.contracts.task import WorkerType
from edrak.contracts.verification import (
    EvidenceQuality,
    FindingCheckStatus,
    FindingVerdict,
)
from edrak.CrossSignal.nodes import CrossSignalNodes
from edrak.CrossSignal.state import CrossSignalSettings
from edrak.DecisionAnalysis.nodes import _ground_recommendation_numbers


def test_customer_trends_reserves_time_for_finding_generation() -> None:
    now = [0.0]
    budget = BudgetTracker(Budget(max_seconds=300), clock=lambda: now[0])
    now[0] = 150.0

    route = route_after_gap_check(
        {
            "gaps": [
                {
                    "id": "demand_missing",
                    "severity": "critical",
                    "description": "Demand data is missing.",
                    "suggested_action": "Collect demand data.",
                }
            ]
        },
        budget,
    )

    assert route == "write_findings"


def _request() -> BusinessRequest:
    return BusinessRequest(
        request_id="cross-signal-test",
        goal="Assess the AI inference market.",
        company_profile=CompanyProfile(name="NVIDIA"),
        business_context=BusinessContext(
            use_case=UseCase.PRODUCT_LAUNCH,
            targets=["Broadcom"],
        ),
    )


def _finding(finding_id: str, worker: WorkerType, statement: str) -> FindingVerdict:
    return FindingVerdict(
        finding_id=finding_id,
        worker=worker,
        statement=statement,
        verification_status=FindingCheckStatus.VERIFIED,
        evidence_quality=EvidenceQuality.HIGH,
        confidence=0.9,
    )


def _signal(signal: str, *finding_ids: str) -> CrossSignal:
    return CrossSignal(
        signal_type=SignalType.CONVERGENCE,
        title="Integrated AI inference offerings",
        signal=signal,
        interpretation=(
            "The findings indicate an integrated market direction."
        ),
        supporting_findings=[
            SignalEvidence(
                finding_id=finding_id,
                relevance="Supports the relationship.",
            )
            for finding_id in finding_ids
        ],
        domains_involved=[WorkerType.COMPETITOR_INTELLIGENCE],
        confidence=0.9,
        evidence_quality=EvidenceQuality.HIGH,
        strategic_relevance="The relationship affects positioning.",
        decision_relevance=DecisionRelevance.BOTH,
    )


def _collect_signal(candidate: CrossSignal, findings: list[FindingVerdict]) -> dict:
    cross_input = CrossSignalInput(
        research_run_id="cross-signal-test",
        business_request=_request(),
        verified_findings=findings,
    )
    state = {
        "input": cross_input,
        "candidate_signals": [candidate],
        "findings_by_id": {finding.finding_id: finding for finding in findings},
        "finding_aliases": {},
    }
    node = CrossSignalNodes(
        llm=None,
        settings=CrossSignalSettings(),
    )  # type: ignore[arg-type]
    return asyncio.run(node.collect_signals(state))


def test_cross_signal_discards_single_worker_convergence() -> None:
    findings = [
        _finding(
            "intel-1",
            WorkerType.COMPETITOR_INTELLIGENCE,
            "Intel has an inference design.",
        ),
        _finding(
            "intel-2",
            WorkerType.COMPETITOR_INTELLIGENCE,
            "Intel expanded its design.",
        ),
    ]

    candidate = _signal(
        "Intel's offerings converge.",
        "intel-1",
        "intel-2",
    )
    result = _collect_signal(candidate, findings)

    assert result["signals"] == []


def test_cross_signal_discards_named_entities_not_in_support() -> None:
    findings = [
        _finding(
            "intel-1",
            WorkerType.COMPETITOR_INTELLIGENCE,
            "Intel has an inference design.",
        ),
        _finding(
            "market-1",
            WorkerType.MARKET_INTELLIGENCE,
            "Demand for inference is growing.",
        ),
    ]
    candidate = _signal(
        "Intel and Broadcom are converging on rack-scale inference.",
        "intel-1",
        "market-1",
    )

    result = _collect_signal(candidate, findings)

    assert result["signals"] == []


def test_cross_signal_reports_unresolved_references_only_for_candidates() -> None:
    findings = [
        _finding(
            "intel-1",
            WorkerType.COMPETITOR_INTELLIGENCE,
            "Intel has an inference design.",
        ),
        _finding(
            "intel-2",
            WorkerType.COMPETITOR_INTELLIGENCE,
            "Intel expanded its design.",
        ),
    ]
    candidate = _signal("Intel's offerings converge.", "intel-1", "missing-finding")

    result = _collect_signal(candidate, findings)

    assert result["signals"] == []
    assert result["stats"]["support_references_unresolved"] == 0
    assert result["stats"]["candidate_support_references_unresolved"] == 1
    assert result["stats"]["unresolved_candidate_support_references"] == [
        "missing-finding"
    ]


def test_decision_analysis_removes_numbers_absent_from_supporting_signals() -> None:
    details = RecommendationDetails(
        decision="Launch in Q3 2026. Validate customer requirements first.",
        rationale="The signal identifies a 2026 competitor milestone.",
        actions=["Finalize H100 GPUs and 200 Gbps networking."],
        success_criteria=["Achieve at least 10% higher throughput."],
        supporting_signal_ids=["signal-1"],
        confidence="high",
    )
    signals = [
        {
            "signal_id": "signal-1",
            "title": "Competitor timing",
            "signal": "A competitor plans shipment in H2 2026.",
            "interpretation": "The launch window is uncertain.",
        }
    ]

    grounded, changed = _ground_recommendation_numbers(details, signals)

    assert changed
    assert grounded.decision == "Validate customer requirements first."
    assert grounded.actions == [
        "Validate launch readiness and customer requirements against "
        "the supporting signals."
    ]
    assert grounded.success_criteria == [
        "Set measurable acceptance criteria from validated customer "
        "requirements before approval."
    ]
    assert grounded.rationale == "The signal identifies a 2026 competitor milestone."
