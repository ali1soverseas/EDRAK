"""Tests for EDRAK shared contracts."""

import pytest
from edrak.contracts.evidence import Evidence
from edrak.contracts.request import BusinessRequest
from edrak.contracts.result import Finding, WorkerResult, WorkerStatus
from edrak.contracts.task import ResearchTask, WorkerRole


def test_business_request_creation():
    req = BusinessRequest(
        title="GitLab Duo vs GitHub Copilot Strategy",
        user_goal="Assess competitive risk and identify product roadmap differentiation.",
        target_company="GitLab",
    )
    assert req.request_id is not None
    assert req.target_company == "GitLab"
    assert req.focus_domain == "Competitive Intelligence & Monitoring"


def test_research_task_creation():
    task = ResearchTask(
        request_id="req-123",
        worker_role=WorkerRole.INTERNAL_INTELLIGENCE,
        objective="Analyze GitLab Duo internal architecture and telemetry metrics",
        scope="GitLab Duo, AI Gateway, Enterprise tier pricing",
        key_questions=["What is current seat adoption?", "What are tier prerequisites?"],
    )
    assert task.task_id is not None
    assert task.worker_role == WorkerRole.INTERNAL_INTELLIGENCE
    assert len(task.key_questions) == 2


def test_worker_result_validation():
    ev = Evidence(
        source_uri="internal://pricing",
        source_type="internal_doc",
        title="Pricing Guide",
        content="GitLab Duo Pro is $19/user/month.",
    )
    finding = Finding(
        statement="GitLab Duo Pro pricing is $19 per user per month.",
        domain_topic="pricing_and_commercials",
        confidence=0.95,
        evidence_ids=[ev.id],
    )
    result = WorkerResult(
        task_id="task-456",
        worker_role="internal_intelligence",
        status=WorkerStatus.SUCCESS,
        summary="Analysis completed successfully.",
        findings=[finding],
        evidence=[ev],
    )
    assert result.status == WorkerStatus.SUCCESS
    assert len(result.findings) == 1
    assert result.findings[0].evidence_ids == [ev.id]
