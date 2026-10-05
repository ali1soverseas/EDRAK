"""Tests for EDRAK shared contracts."""

import pytest
from edrak.contracts.base import NonBlankStr
from edrak.contracts.evidence import Evidence, EvidenceRef, EvidenceRelation, SourceType
from edrak.contracts.request import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    TriggerType,
    UseCase,
)
from edrak.contracts.result import (
    Conflict,
    Finding,
    FindingCategory,
    OrchestrationResult,
    RunStatus,
    WorkerResult,
    WorkerStatus,
)
from edrak.contracts.task import ResearchPlan, ResearchTask, WorkerType
from edrak.contracts.verification import (
    TargetedAction,
    VerificationDecision,
    VerificationStatus,
)
from edrak.contracts.worker import WorkerOutcome, WorkerRegistry


def test_business_request_creation():
    company = CompanyProfile(name="GitLab", products=["GitLab Duo", "GitLab CI/CD"])
    context = BusinessContext(
        use_case=UseCase.COMPETITIVE_INTELLIGENCE,
        targets=["GitHub", "Azure DevOps"],
        focus_areas=["AI capabilities", "pricing"],
        trigger=TriggerType.ON_DEMAND,
    )
    req = BusinessRequest(
        goal="Assess competitive risk of GitHub Copilot vs GitLab Duo",
        company_profile=company,
        business_context=context,
    )
    assert req.request_id is not None
    assert req.company_profile.name == "GitLab"
    assert req.business_context.use_case == UseCase.COMPETITIVE_INTELLIGENCE
    assert len(req.business_context.targets) == 2


def test_research_task_creation():
    company = CompanyProfile(name="GitLab")
    context = BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE)
    task = ResearchTask(
        parent_request_id="req-123",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal="Analyze GitLab Duo internal architecture and telemetry metrics",
        focus="GitLab Duo, AI Gateway, Enterprise tier pricing",
        company_profile=company,
        business_context=context,
        attempt=1,
    )
    assert task.task_id is not None
    assert task.worker == WorkerType.INTERNAL_INTELLIGENCE
    assert task.parent_request_id == "req-123"
    assert task.attempt == 1


def test_worker_result_validation():
    ev = Evidence(
        source_type=SourceType.SYNTHETIC_INTERNAL,
        source_title="Pricing Guide",
        source_url="internal://pricing",
        extracted_fact="GitLab Duo Pro is $19/user/month.",
        is_synthetic=True,
    )
    finding = Finding(
        statement="GitLab Duo Pro pricing is $19 per user per month.",
        category=FindingCategory.PRICING_PACKAGING,
        confidence=0.95,
        evidence_refs=[
            EvidenceRef(evidence_id=ev.evidence_id, relation=EvidenceRelation.SUPPORTS)
        ],
    )
    result = WorkerResult(
        task_id="task-456",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        status=WorkerStatus.COMPLETED,
        findings=[finding],
        evidence=[ev],
    )
    assert result.status == WorkerStatus.COMPLETED
    assert len(result.findings) == 1
    assert result.findings[0].evidence_ids == [ev.evidence_id]
    assert result.findings[0].is_supported is True


def test_verification_decision():
    dec_verified = VerificationDecision(
        status=VerificationStatus.VERIFIED,
        summary="All findings verified against grounded evidence.",
    )
    assert dec_verified.status == VerificationStatus.VERIFIED

    dec_retry = VerificationDecision(
        status=VerificationStatus.RETRY_REQUIRED,
        targeted_actions=[
            TargetedAction(
                worker=WorkerType.INTERNAL_INTELLIGENCE,
                reason="Need deeper telemetry data",
            )
        ],
        summary="Internal worker needs retry on telemetry.",
    )
    assert len(dec_retry.targeted_actions) == 1


def test_gitlab_company_profile():
    from edrak.core.profiles import (
        GITLAB_COMPANY_PROFILE,
        get_company_profile,
        get_detailed_gitlab_profile,
        get_gitlab_profile,
    )

    profile = get_gitlab_profile()
    assert profile.name == "GitLab"
    assert "GitLab Duo" in profile.products
    assert "GitLab Duo Agent Platform" in profile.products
    assert "AI Gateway" in profile.products
    assert len(profile.aliases) > 0

    lookup = get_company_profile("gitlab")
    assert lookup.name == "GitLab"

    detailed = get_detailed_gitlab_profile()
    assert detailed["name"] == "GitLab"
    assert "ticker" in detailed
    assert detailed["ticker"] == "NASDAQ: GTLB"
    assert "leadership" in detailed
    assert "financial_profile" in detailed


