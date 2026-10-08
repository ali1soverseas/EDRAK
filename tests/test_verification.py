from unittest.mock import patch

from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    Conflict,
    Evidence,
    EvidenceQuality,
    EvidenceRef,
    EvidenceRelation,
    Finding,
    FindingCategory,
    FindingCheckStatus,
    SourceType,
    UseCase,
    VerificationDecision,
    VerificationInput,
    VerificationResult,
    VerificationStatus,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.verification import run


def _request() -> BusinessRequest:
    return BusinessRequest(
        request_id="run_001",
        goal="Evaluate expansion opportunities for AI and agentic software development.",
        company_profile=CompanyProfile(name="GitLab", products=["GitLab Duo"]),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub Copilot"],
            focus_areas=["agentic software development"],
        ),
    )


def _official_competitor() -> WorkerResult:
    evidence = Evidence(
        evidence_id="ev_comp_official",
        source_type=SourceType.OFFICIAL_DOCUMENTATION,
        source_title="GitHub Copilot docs",
        source_url="https://docs.github.com/copilot",
        publisher="GitHub",
        extracted_fact=(
            "GitHub Copilot provides agentic software development capabilities "
            "in the IDE and on github.com."
        ),
    )
    return WorkerResult(
        task_id="task-competitor",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        status=WorkerStatus.COMPLETED,
        findings=[
            Finding(
                finding_id="comp_001",
                statement="GitHub provides agentic software development capabilities.",
                category=FindingCategory.PRODUCT_FEATURE,
                evidence_refs=[EvidenceRef(evidence_id=evidence.evidence_id)],
                confidence=0.9,
            )
        ],
        evidence=[evidence],
        error="none",
    )


def _snippet_only_competitor() -> WorkerResult:
    evidence = Evidence(
        evidence_id="ev_comp_snippet",
        source_type=SourceType.SEARCH_RESULT,
        source_url="https://example.com/search",
        extracted_fact="Search result mentions GitHub is expanding its AI coding agent.",
    )
    return WorkerResult(
        task_id="task-competitor-weak",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        status=WorkerStatus.PARTIAL,
        findings=[
            Finding(
                finding_id="comp_002",
                statement="GitHub's agent can autonomously handle enterprise development.",
                category=FindingCategory.PRODUCT_FEATURE,
                evidence_refs=[EvidenceRef(evidence_id=evidence.evidence_id)],
                confidence=0.4,
            )
        ],
        evidence=[evidence],
        gaps=["Official enterprise documentation was not retrieved."],
        error="none",
    )


def _conflicting_market() -> WorkerResult:
    listed = Evidence(
        evidence_id="ev_price_list",
        source_type=SourceType.NEWS_ARTICLE,
        extracted_fact="GitHub Copilot enterprise feature costs $39 per user each month.",
    )
    included = Evidence(
        evidence_id="ev_price_page",
        source_type=SourceType.PRICING_PAGE,
        extracted_fact="GitHub pricing page says the same feature is included in plan Y at no extra cost.",
    )
    finding = Finding(
        finding_id="mkt_001",
        statement="GitHub Copilot feature costs $39 per user.",
        category=FindingCategory.PRICING_PACKAGING,
        evidence_refs=[
            EvidenceRef(evidence_id=listed.evidence_id, relation=EvidenceRelation.SUPPORTS),
            EvidenceRef(evidence_id=included.evidence_id, relation=EvidenceRelation.CONTRADICTS),
        ],
    )
    return WorkerResult(
        task_id="task-market",
        worker=WorkerType.MARKET_INTELLIGENCE,
        status=WorkerStatus.PARTIAL,
        findings=[finding],
        evidence=[listed, included],
        conflicts=[
            Conflict(
                finding_id=finding.finding_id,
                contradicting_evidence_ids=[included.evidence_id],
                description="Two sources report different pricing.",
            )
        ],
        error="Official pricing page could not be retrieved.",
    )


def _customer_trend() -> WorkerResult:
    evidence = Evidence(
        evidence_id="ev_cust",
        source_type=SourceType.NEWS_ARTICLE,
        extracted_fact=(
            "Enterprise buyers are asking for AI coding agents that stay inside "
            "existing DevSecOps workflows."
        ),
    )
    return WorkerResult(
        task_id="task-customer",
        worker=WorkerType.CUSTOMER_TRENDS,
        status=WorkerStatus.COMPLETED,
        findings=[
            Finding(
                finding_id="cust_001",
                statement="Customers want AI coding agents inside existing DevSecOps workflows.",
                category=FindingCategory.CUSTOMER_SENTIMENT,
                evidence_refs=[EvidenceRef(evidence_id=evidence.evidence_id)],
            )
        ],
        evidence=[evidence],
        error="none",
    )


def _run(payload: VerificationInput) -> VerificationResult:
    with patch("edrak.verification.nodes._llm_review", return_value=None):
        return run(payload)


def test_verified_official_finding_passes_contract():
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[_official_competitor(), _customer_trend()],
    )
    result = _run(payload)

    assert isinstance(result, VerificationResult)
    assert result.research_run_id == "run_001"
    assert isinstance(result.decision, VerificationDecision)
    assert result.decision.status is VerificationStatus.VERIFIED
    assert result.decision.targeted_actions == []

    official = next(item for item in result.findings if item.finding_id == "comp_001")
    assert official.worker is WorkerType.COMPETITOR_INTELLIGENCE
    assert official.verification_status is FindingCheckStatus.VERIFIED
    assert official.evidence_quality is EvidenceQuality.HIGH
    assert official.contradictions == []
    assert official.confidence >= 0.8


def test_insufficient_snippet_and_missing_details_require_retry():
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[_snippet_only_competitor()],
    )
    result = _run(payload)

    assert result.decision.status is VerificationStatus.RETRY_REQUIRED
    assert result.decision.targeted_actions
    assert result.decision.targeted_actions[0].worker is WorkerType.COMPETITOR_INTELLIGENCE

    weak = result.findings[0]
    assert weak.finding_id == "comp_002"
    assert weak.verification_status is FindingCheckStatus.INSUFFICIENT
    assert weak.evidence_quality is EvidenceQuality.LOW
    assert "Level of autonomy" in weak.missing_information
    assert "Enterprise availability" in weak.missing_information
    assert "Official source beyond a search snippet." in weak.missing_information
    assert result.control_summary.next_research_targets


def test_pricing_conflict_is_flagged_for_replan():
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[_official_competitor(), _conflicting_market()],
    )
    result = _run(payload)

    assert result.decision.status is VerificationStatus.REPLAN_REQUIRED
    market = next(item for item in result.findings if item.finding_id == "mkt_001")
    assert market.verification_status is FindingCheckStatus.INSUFFICIENT
    assert market.contradictions
    assert "Two sources report different pricing." in result.control_summary.conflicts
    assert any("pricing page could not be retrieved" in item for item in result.control_summary.failures)


def test_invented_number_is_rejected():
    payload = VerificationInput(request=_request(), agent_outputs=[_official_competitor()])
    review = {
        "quality": EvidenceQuality.HIGH,
        "contradictions": [],
        "invented": ["$19"],
    }
    with patch("edrak.verification.nodes._llm_review", return_value=review):
        result = run(payload)

    official = result.findings[0]
    assert official.verification_status is FindingCheckStatus.INSUFFICIENT
    assert any("not in the saved source" in note for note in official.contradictions)
    assert result.decision.status is VerificationStatus.REPLAN_REQUIRED


def test_empty_outputs_cannot_complete():
    result = _run(VerificationInput(request=_request(), agent_outputs=[]))
    assert result.decision.status is VerificationStatus.CANNOT_COMPLETE
    assert result.findings == []
    assert result.control_summary.failures
