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
from edrak.verification.quality import quality_from_url, vendor_hosts


def _request(**kwargs) -> BusinessRequest:
    values = dict(
        request_id="run_001",
        goal="Evaluate expansion opportunities for AI and agentic software development.",
        company_profile=CompanyProfile(name="GitLab", products=["GitLab Duo"]),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub Copilot"],
            focus_areas=["agentic software development"],
        ),
    )
    values.update(kwargs)
    return BusinessRequest(**values)


def _finding_result(
    *,
    worker: WorkerType,
    finding_id: str,
    statement: str,
    evidence: list[Evidence],
    confidence: float | None = 0.9,
    limitations: list[str] | None = None,
    status: WorkerStatus = WorkerStatus.COMPLETED,
    gaps: list[str] | None = None,
    conflicts: list[Conflict] | None = None,
    error: str = "none",
    refs: list[EvidenceRef] | None = None,
    category: FindingCategory = FindingCategory.MARKET_SIGNAL,
) -> WorkerResult:
    return WorkerResult(
        task_id=f"task-{finding_id}",
        worker=worker,
        status=status,
        findings=[
            Finding(
                finding_id=finding_id,
                statement=statement,
                category=category,
                evidence_refs=refs
                or [EvidenceRef(evidence_id=item.evidence_id) for item in evidence],
                confidence=confidence,
                limitations=limitations or [],
            )
        ],
        evidence=evidence,
        gaps=gaps or [],
        conflicts=conflicts or [],
        error=error,
    )


def _official_competitor() -> WorkerResult:
    evidence = Evidence(
        evidence_id="ev_comp_official",
        source_type=SourceType.WEB_PAGE,
        source_title="GitHub Copilot docs",
        source_url="https://docs.github.com/copilot",
        publisher="GitHub",
        extracted_fact=(
            "GitHub Copilot provides agentic software development capabilities "
            "in the IDE and on github.com."
        ),
    )
    return _finding_result(
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        finding_id="comp_001",
        statement="GitHub provides agentic software development capabilities.",
        evidence=[evidence],
        category=FindingCategory.PRODUCT_FEATURE,
        limitations=["Derived from research task: catalog Copilot launches"],
    )


def _snippet_only_competitor() -> WorkerResult:
    evidence = Evidence(
        evidence_id="ev_comp_snippet",
        source_type=SourceType.SEARCH_RESULT,
        source_url="https://example.com/search",
        extracted_fact="Search result mentions GitHub is expanding its AI coding agent.",
    )
    return _finding_result(
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        finding_id="comp_002",
        statement="GitHub's agent can autonomously handle enterprise development.",
        evidence=[evidence],
        confidence=0.4,
        status=WorkerStatus.PARTIAL,
        gaps=["Official enterprise documentation was not retrieved."],
        category=FindingCategory.PRODUCT_FEATURE,
    )


def _conflicting_market() -> WorkerResult:
    listed = Evidence(
        evidence_id="ev_price_list",
        source_type=SourceType.NEWS_ARTICLE,
        source_url="https://www.helpnetsecurity.com/copilot-price",
        extracted_fact="GitHub Copilot enterprise feature costs $39 per user each month.",
    )
    included = Evidence(
        evidence_id="ev_price_page",
        source_type=SourceType.PRICING_PAGE,
        source_url="https://github.com/pricing",
        extracted_fact="GitHub pricing page says the same feature is included in plan Y at no extra cost.",
    )
    refs = [
        EvidenceRef(evidence_id=listed.evidence_id, relation=EvidenceRelation.SUPPORTS),
        EvidenceRef(evidence_id=included.evidence_id, relation=EvidenceRelation.CONTRADICTS),
    ]
    finding = Finding(
        finding_id="mkt_001",
        statement="GitHub Copilot feature costs $39 per user.",
        category=FindingCategory.PRICING_PACKAGING,
        evidence_refs=refs,
        confidence=0.8,
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
        source_url="https://www.helpnetsecurity.com/2026/02/24/ai-agents",
        extracted_fact=(
            "Enterprise buyers are asking for AI coding agents that stay inside "
            "existing DevSecOps workflows."
        ),
    )
    return _finding_result(
        worker=WorkerType.CUSTOMER_TRENDS,
        finding_id="cust_001",
        statement="Customers want AI coding agents inside existing DevSecOps workflows.",
        evidence=[evidence],
        confidence=0.8,
        category=FindingCategory.CUSTOMER_SENTIMENT,
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
    assert official.missing_information == []


def test_insufficient_snippet_and_low_confidence_require_retry():
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
    assert any("confidence below" in item.lower() for item in weak.missing_information)
    assert "Level of autonomy" not in weak.missing_information
    assert "Which plan or packaging includes the capability" not in weak.missing_information
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
    official = next(item for item in result.findings if item.finding_id == "comp_001")
    assert official.verification_status is FindingCheckStatus.VERIFIED


def test_invented_number_is_retry_not_replan():
    evidence = Evidence(
        evidence_id="ev_docs",
        source_type=SourceType.WEB_PAGE,
        source_url="https://docs.github.com/copilot",
        extracted_fact="GitHub Copilot provides agentic software development capabilities in the IDE.",
    )
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[
            _finding_result(
                worker=WorkerType.COMPETITOR_INTELLIGENCE,
                finding_id="comp_invented",
                statement="GitHub Copilot costs $19 per user for agentic software development.",
                evidence=[evidence],
            )
        ],
    )
    result = _run(payload)

    official = result.findings[0]
    assert official.verification_status is FindingCheckStatus.INSUFFICIENT
    assert official.contradictions == []
    assert any("not in the saved source" in item or "$19" in item for item in official.missing_information)
    assert result.decision.status is VerificationStatus.RETRY_REQUIRED


def test_empty_outputs_cannot_complete():
    result = _run(VerificationInput(request=_request(), agent_outputs=[]))
    assert result.decision.status is VerificationStatus.CANNOT_COMPLETE
    assert result.findings == []
    assert result.control_summary.failures


def test_market_web_page_on_analyst_url_can_verify():
    evidence = Evidence(
        evidence_id="ev_gartner",
        source_type=SourceType.WEB_PAGE,
        source_url="https://www.gartner.com/en/newsroom/press-releases/ai-coding-agents",
        extracted_fact="The market for enterprise AI coding agents is entering a new phase of expansion.",
    )
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[
            _finding_result(
                worker=WorkerType.MARKET_INTELLIGENCE,
                finding_id="mkt_analyst",
                statement="The market for enterprise AI coding agents is entering a new phase of expansion.",
                evidence=[evidence],
                confidence=0.7,
            )
        ],
    )
    with patch("edrak.verification.nodes._llm_review") as review:
        review.return_value = None
        result = run(payload)
        review.assert_not_called()

    finding = result.findings[0]
    assert finding.verification_status is FindingCheckStatus.VERIFIED
    assert finding.evidence_quality is EvidenceQuality.HIGH
    assert result.decision.status is VerificationStatus.VERIFIED


def test_market_blog_url_is_low_quality_even_when_grounded():
    evidence = Evidence(
        evidence_id="ev_blog",
        source_type=SourceType.WEB_PAGE,
        source_url="https://jessehouwing.net/github-copilot-now-with-data-residency-in-europe",
        extracted_fact="GitHub Copilot now supports data residency for US and EU regions.",
    )
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[
            _finding_result(
                worker=WorkerType.MARKET_INTELLIGENCE,
                finding_id="mkt_blog",
                statement="GitHub Copilot now supports data residency for US and EU regions.",
                evidence=[evidence],
                confidence=0.7,
            )
        ],
    )
    result = _run(payload)
    finding = result.findings[0]
    assert finding.verification_status is FindingCheckStatus.INSUFFICIENT
    assert finding.evidence_quality is EvidenceQuality.LOW
    assert result.decision.status is VerificationStatus.RETRY_REQUIRED


def test_limitations_are_not_copied_as_missing_information():
    result = _run(VerificationInput(request=_request(), agent_outputs=[_official_competitor()]))
    finding = result.findings[0]
    assert finding.verification_status is FindingCheckStatus.VERIFIED
    assert finding.missing_information == []
    assert not any("Derived from research task" in item for item in result.control_summary.missing_information)


def test_planning_substring_does_not_invent_packaging_gap():
    evidence = Evidence(
        evidence_id="ev_sdlc",
        source_type=SourceType.WEB_PAGE,
        source_url="https://www.mckinsey.com/capabilities/tech-and-ai/agentic-sdlc",
        extracted_fact=(
            "The agentic software development lifecycle involves AI agents participating "
            "across planning, coding, reviewing, deploying, and operating."
        ),
    )
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[
            _finding_result(
                worker=WorkerType.MARKET_INTELLIGENCE,
                finding_id="mkt_sdlc",
                statement=(
                    "The agentic software development lifecycle involves AI agents participating "
                    "across planning, coding, reviewing, deploying, and operating."
                ),
                evidence=[evidence],
                confidence=0.7,
            )
        ],
    )
    result = _run(payload)
    finding = result.findings[0]
    assert finding.verification_status is FindingCheckStatus.VERIFIED
    assert "Which plan or packaging includes the capability" not in finding.missing_information


def test_two_prices_on_the_same_page_are_not_a_conflict():
    excerpt = "$19/user/month, including $19 in monthly AI Credits. $39/user/month, including $39 in monthly AI Credits."
    first = Evidence(
        evidence_id="ev_19",
        source_type=SourceType.WEB_PAGE,
        source_url="https://github.blog/news-insights/company-news/github-copilot-is-moving-to-usage-based-billing/",
        extracted_fact=excerpt,
        excerpt=excerpt,
    )
    second = Evidence(
        evidence_id="ev_39",
        source_type=SourceType.WEB_PAGE,
        source_url="https://github.blog/news-insights/company-news/github-copilot-is-moving-to-usage-based-billing/",
        extracted_fact=excerpt,
        excerpt=excerpt,
    )
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[
            _finding_result(
                worker=WorkerType.MARKET_INTELLIGENCE,
                finding_id="mkt_price",
                statement="$19 per user per month includes $19 in monthly AI Credits.",
                evidence=[first, second],
                confidence=0.8,
            )
        ],
    )
    result = _run(payload)
    finding = result.findings[0]
    assert finding.contradictions == []
    assert finding.evidence_ids == ["ev_19"]
    assert finding.verification_status is FindingCheckStatus.VERIFIED


def test_low_confidence_skips_llm():
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[_snippet_only_competitor()],
    )
    with patch("edrak.verification.nodes._llm_review") as review:
        result = run(payload)
        review.assert_not_called()
    assert result.findings[0].verification_status is FindingCheckStatus.INSUFFICIENT


def test_mildly_good_grounded_claim_skips_llm():
    payload = VerificationInput(request=_request(), agent_outputs=[_official_competitor()])
    with patch("edrak.verification.nodes._llm_review") as review:
        result = run(payload)
        review.assert_not_called()
    assert result.findings[0].verification_status is FindingCheckStatus.VERIFIED


def test_llm_invented_details_retry_when_grounding_is_uncertain():
    evidence = Evidence(
        evidence_id="ev_mck",
        source_type=SourceType.WEB_PAGE,
        source_url="https://www.mckinsey.com/capabilities/tech-and-ai/our-insights/agents",
        extracted_fact="Agentic AI is growing among software teams in several regions.",
    )
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[
            _finding_result(
                worker=WorkerType.MARKET_INTELLIGENCE,
                finding_id="mkt_uncertain",
                statement="Enterprises are adopting agentic AI across the software lifecycle.",
                evidence=[evidence],
                confidence=0.7,
            )
        ],
    )
    review = {"supported": True, "contradictions": [], "invented": ["80%"]}
    with patch("edrak.verification.nodes._llm_review", return_value=review) as mocked:
        result = run(payload)
        mocked.assert_called_once()
    finding = result.findings[0]
    assert finding.verification_status is FindingCheckStatus.INSUFFICIENT
    assert finding.contradictions == []
    assert any("80%" in item for item in finding.missing_information)
    assert result.decision.status is VerificationStatus.RETRY_REQUIRED


def test_each_worker_is_verified_independently():
    payload = VerificationInput(
        request=_request(),
        agent_outputs=[_official_competitor(), _snippet_only_competitor(), _customer_trend()],
    )
    result = _run(payload)
    by_id = {item.finding_id: item for item in result.findings}
    assert by_id["comp_001"].verification_status is FindingCheckStatus.VERIFIED
    assert by_id["cust_001"].verification_status is FindingCheckStatus.VERIFIED
    assert by_id["comp_002"].verification_status is FindingCheckStatus.INSUFFICIENT
    assert result.decision.status is VerificationStatus.RETRY_REQUIRED
    assert all(action.worker is WorkerType.COMPETITOR_INTELLIGENCE for action in result.decision.targeted_actions)
    assert "12 verified" not in result.decision.summary
    assert "need targeted follow-up" in result.decision.summary


def test_multiple_insufficient_findings_each_create_an_action():
    weak = Evidence(
        evidence_id="ev_weak",
        source_type=SourceType.WEB_PAGE,
        source_url="https://example.com/a",
        extracted_fact="A short mention of agents.",
    )
    other = Evidence(
        evidence_id="ev_weak2",
        source_type=SourceType.WEB_PAGE,
        source_url="https://example.com/b",
        extracted_fact="Another short mention of agents.",
    )
    first = _finding_result(
        worker=WorkerType.MARKET_INTELLIGENCE,
        finding_id="mkt_a",
        statement="Agentic platforms will replace every DevSecOps toolchain by 2028.",
        evidence=[weak],
        confidence=0.4,
    )
    second = _finding_result(
        worker=WorkerType.MARKET_INTELLIGENCE,
        finding_id="mkt_b",
        statement="Every enterprise already funds lifecycle-wide agents.",
        evidence=[other],
        confidence=0.4,
    )
    combined = WorkerResult(
        task_id="task-multi",
        worker=WorkerType.MARKET_INTELLIGENCE,
        status=WorkerStatus.PARTIAL,
        findings=first.findings + second.findings,
        evidence=first.evidence + second.evidence,
        error="none",
    )
    result = _run(VerificationInput(request=_request(), agent_outputs=[combined]))
    market_actions = [item for item in result.decision.targeted_actions if item.worker is WorkerType.MARKET_INTELLIGENCE]
    assert len(market_actions) == 2


def test_absent_worker_does_not_block_verification():
    payload = VerificationInput(
        request=_request(
            goal=(
                "Analyze six topics: TAM and growth of agentic AI in DevSecOps; "
                "enterprise demand for lifecycle-wide agents; GitHub Copilot product launches."
            )
        ),
        agent_outputs=[
            _finding_result(
                worker=WorkerType.MARKET_INTELLIGENCE,
                finding_id="mkt_demand",
                statement="Enterprise buyers are asking for AI coding agents inside existing DevSecOps workflows.",
                evidence=[
                    Evidence(
                        evidence_id="ev_demand",
                        source_type=SourceType.WEB_PAGE,
                        source_url="https://www.mckinsey.com/capabilities/tech-and-ai/demand",
                        extracted_fact="Enterprise buyers are asking for AI coding agents inside existing DevSecOps workflows.",
                    )
                ],
                confidence=0.7,
            )
        ],
    )
    result = _run(payload)
    assert WorkerType.COMPETITOR_INTELLIGENCE.value not in result.metadata["workers"]
    assert not any(action.worker is WorkerType.COMPETITOR_INTELLIGENCE for action in result.decision.targeted_actions)


def test_request_coverage_retries_present_worker_for_missing_topics():
    payload = VerificationInput(
        request=_request(
            goal=(
                "Analyze six topics: TAM growth and spend for agentic AI in DevSecOps; "
                "enterprise demand for lifecycle-wide agents versus IDE copilots."
            )
        ),
        agent_outputs=[
            _finding_result(
                worker=WorkerType.MARKET_INTELLIGENCE,
                finding_id="mkt_define",
                statement="Agentic AI is the transition from single-step generative models to planning systems.",
                evidence=[
                    Evidence(
                        evidence_id="ev_define",
                        source_type=SourceType.WEB_PAGE,
                        source_url="https://www.mckinsey.com/capabilities/tech-and-ai/define",
                        extracted_fact="Agentic AI is the transition from single-step generative models to planning systems.",
                    )
                ],
                confidence=0.7,
            )
        ],
    )
    result = _run(payload)
    assert result.decision.status is VerificationStatus.RETRY_REQUIRED
    assert any("No finding covering" in item for item in result.control_summary.missing_information)
    assert any("No finding covering" in action.reason for action in result.decision.targeted_actions)


def test_quality_follows_url_tiers_not_worker_enum():
    hosts = vendor_hosts(_request())
    assert quality_from_url(
        "https://www.gartner.com/en/newsroom/ai",
        source_type=SourceType.WEB_PAGE,
        vendor_domains=hosts,
    ) is EvidenceQuality.HIGH
    assert quality_from_url(
        "https://github.blog/news-insights/company-news/copilot-billing/",
        source_type=SourceType.WEB_PAGE,
        vendor_domains=hosts,
    ) is EvidenceQuality.HIGH
    assert quality_from_url(
        "https://jessehouwing.net/copilot",
        source_type=SourceType.OFFICIAL_DOCUMENTATION,
        vendor_domains=hosts,
    ) is EvidenceQuality.LOW
    assert quality_from_url(
        "https://docs.github.com/copilot",
        source_type=SourceType.WEB_PAGE,
        vendor_domains=hosts,
    ) is EvidenceQuality.HIGH
