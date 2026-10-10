"""Cross-signal must still run when the replan budget runs out.

One contradictory finding used to end the run at finalize, discarding
cross_signal and decision_analysis along with every verified finding. The
orchestrator now routes exhausted replans to cross_signal, and the contract
accepts REPLAN_REQUIRED provided something verified.

These tests pin that behaviour and, just as importantly, pin what must still
be rejected: CANNOT_COMPLETE, which means nothing passed, and a payload with no
verified findings at all.
"""

import pytest
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    Evidence,
    EvidenceQuality,
    EvidenceRef,
    Finding,
    FindingCategory,
    SourceType,
    UseCase,
    WorkerType,
)
from edrak.contracts.CrossSignal import build_cross_signal_input
from edrak.contracts.verification import (
    FindingCheckStatus,
    FindingVerdict,
    TargetedAction,
    VerificationDecision,
    VerificationResult,
    VerificationStatus,
)
from edrak.orchestration.routing import decide_after_verification


def _request() -> BusinessRequest:
    return BusinessRequest(
        request_id="cs-gate",
        goal="Assess competitors.",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub"],
        ),
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="ev-1",
        source_type=SourceType.WEB_PAGE,
        extracted_fact="Copilot is sold from 10 USD per month.",
    )


def _finding(index: int) -> Finding:
    return Finding(
        finding_id=f"f{index}",
        statement=f"Claim {index}.",
        category=FindingCategory.PRODUCT_FEATURE,
        evidence_refs=[EvidenceRef(evidence_id="ev-1")],
    )


def _result(
    status: VerificationStatus,
    *,
    verified: int = 3,
    insufficient: int = 1,
) -> VerificationResult:
    findings = [
        FindingVerdict(
            finding_id=f"v{i}",
            worker=WorkerType.COMPETITOR_INTELLIGENCE,
            statement=f"Claim {i}.",
            verification_status=FindingCheckStatus.VERIFIED,
            evidence_quality=EvidenceQuality.HIGH,
            contradictions=["Sources disagree on pricing."],
            evidence_ids=["ev-1"],
            confidence=0.8,
        )
        for i in range(verified)
    ] + [
        FindingVerdict(
            finding_id=f"i{i}",
            worker=WorkerType.MARKET_INTELLIGENCE,
            statement=f"Weak {i}.",
            verification_status=FindingCheckStatus.INSUFFICIENT,
            evidence_quality=EvidenceQuality.LOW,
            confidence=0.3,
        )
        for i in range(insufficient)
    ]
    actions = (
        [TargetedAction(worker=WorkerType.COMPETITOR_INTELLIGENCE, reason="conflict")]
        if status is VerificationStatus.REPLAN_REQUIRED
        else []
    )
    return VerificationResult(
        research_run_id="cs-gate",
        decision=VerificationDecision(status=status, summary="s", targeted_actions=actions),
        findings=findings,
    )


def test_exhausted_replan_routes_to_cross_signal_not_finalize():
    """The bug: a replan budget of zero sent the run straight to finalize."""
    decision = VerificationDecision(
        status=VerificationStatus.REPLAN_REQUIRED,
        summary="conflicts",
        targeted_actions=[
            TargetedAction(worker=WorkerType.COMPETITOR_INTELLIGENCE, reason="conflict")
        ],
    )

    assert decide_after_verification({"verification": decision, "replan_count": 1}) == (
        "cross_signal"
    )


def test_replan_with_budget_left_still_replans():
    decision = VerificationDecision(
        status=VerificationStatus.REPLAN_REQUIRED,
        summary="conflicts",
        targeted_actions=[
            TargetedAction(worker=WorkerType.COMPETITOR_INTELLIGENCE, reason="conflict")
        ],
    )

    assert decide_after_verification({"verification": decision, "replan_count": 0}) == "replan"


def test_cannot_complete_still_goes_to_finalize():
    """That status means nothing verified, so there is nothing to analyse."""
    decision = VerificationDecision(
        status=VerificationStatus.CANNOT_COMPLETE, summary="nothing passed"
    )

    assert decide_after_verification({"verification": decision}) == "finalize"


def test_contract_accepts_replan_required_with_verified_findings():
    result = _result(VerificationStatus.REPLAN_REQUIRED, verified=3, insufficient=1)

    built = build_cross_signal_input(result, _request())

    assert len(built.verified_findings) == 3
    assert all(
        item.verification_status is FindingCheckStatus.VERIFIED
        for item in built.verified_findings
    )


def test_contract_still_rejects_cannot_complete():
    result = _result(VerificationStatus.CANNOT_COMPLETE, verified=0, insufficient=2)

    with pytest.raises(ValueError, match="did not complete successfully"):
        build_cross_signal_input(result, _request())


def test_contract_rejects_a_payload_with_nothing_verified():
    """Status is fine but every finding failed: still nothing to analyse."""
    result = _result(VerificationStatus.VERIFIED, verified=0, insufficient=3)

    with pytest.raises(ValueError, match="no finding passed verification"):
        build_cross_signal_input(result, _request())