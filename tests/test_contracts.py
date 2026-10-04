from edrak.contracts import BusinessRequest, Evidence, Finding, ResearchTask, WorkerResult


def test_research_task_and_worker_result_round_trip():
    task = ResearchTask(
        task_id="t1",
        run_id="r1",
        worker="market",
        goal="Map AI DevOps market trends",
        business_context="GitLab competitive intelligence",
    )
    result = WorkerResult(
        worker="market",
        task_id=task.task_id,
        run_id=task.run_id,
        status="success",
        findings=[
            Finding(
                claim="AI coding assistants are expanding beyond IDE autocomplete.",
                evidence=[Evidence(type="url", source="https://example.com/report")],
                task="Identify current market size",
            )
        ],
    )

    parsed = WorkerResult.model_validate(result.model_dump())
    assert parsed.worker == "market"
    assert parsed.findings[0].evidence[0].source.startswith("https://")


def test_business_request_defaults():
    request = BusinessRequest(request_id="req-1", goal="Compare GitLab Duo and Copilot")
    assert request.business_context == ""
    assert request.company is None
