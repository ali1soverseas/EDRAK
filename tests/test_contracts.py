from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    Evidence,
    EvidenceRef,
    Finding,
    FindingCategory,
    ResearchTask,
    SourceType,
    UseCase,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)


def test_research_task_and_worker_result_round_trip():
    task = ResearchTask(
        task_id="t1",
        parent_request_id="req-1",
        worker=WorkerType.MARKET_INTELLIGENCE,
        goal="Map AI DevOps market trends",
        focus="Market size",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub"],
        ),
    )
    evidence = Evidence(
        evidence_id="ev-1",
        source_type=SourceType.WEB_PAGE,
        source_url="https://example.com/report",
        extracted_fact="AI coding assistants are expanding beyond IDE autocomplete.",
    )
    result = WorkerResult(
        task_id=task.task_id,
        worker=task.worker,
        status=WorkerStatus.COMPLETED,
        findings=[
            Finding(
                statement="AI coding assistants are expanding beyond IDE autocomplete.",
                category=FindingCategory.MARKET_SIGNAL,
                evidence_refs=[EvidenceRef(evidence_id=evidence.evidence_id)],
            )
        ],
        evidence=[evidence],
    )

    parsed = WorkerResult.model_validate(result.model_dump())
    assert parsed.worker is WorkerType.MARKET_INTELLIGENCE
    assert parsed.findings[0].evidence_refs[0].evidence_id == "ev-1"
    assert parsed.evidence[0].source_url.startswith("https://")


def test_business_request_requires_profile_and_context():
    request = BusinessRequest(
        request_id="req-1",
        goal="Compare GitLab Duo and Copilot",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
    )
    assert request.company_profile.name == "GitLab"
    assert request.business_context.use_case is UseCase.COMPETITIVE_INTELLIGENCE
    assert request.extras == {}
