"""Tests for Internal Intelligence Worker."""

from pathlib import Path
import tempfile
import pytest

from edrak.agents.internal_intelligence.graph import run_internal_intelligence
from edrak.contracts.evidence import SourceType
from edrak.contracts.request import BusinessContext, CompanyProfile, UseCase
from edrak.contracts.result import FindingCategory, WorkerStatus
from edrak.contracts.task import ResearchTask, WorkerType
from edrak.core.profiles import get_gitlab_profile
from edrak.mcp.client import InternalDataToolClient
from edrak.rag.indexer import InternalIndexer
from edrak.rag.retriever import InternalRetriever


def test_mock_partial_coverage_scenario(monkeypatch):
    """Verifies behavior when internal corpus only covers 1 topic (partial coverage)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp_path = Path(tmpdir)
        collection_name = "test_internal_partial"

        indexer = InternalIndexer(persist_dir=tmp_path, collection_name=collection_name)
        indexer.reset_collection()

        sample_doc = (
            "# GitLab AI Gateway & Enterprise Privacy Framework\n\n"
            "The GitLab AI Gateway provides multi-model routing with strict zero-retention data privacy agreements.\n"
            "Customers can run GitLab Duo Self-Hosted in air-gapped VPCs without external internet connectivity."
        )
        indexer.index_document(
            content=sample_doc,
            source_uri="internal://architecture/ai_gateway_and_privacy.md",
            title="GitLab AI Gateway and Privacy",
            doc_type="internal_doc",
        )

        test_retriever = InternalRetriever(persist_dir=tmp_path, collection_name=collection_name)
        monkeypatch.setattr(
            "edrak.agents.internal_intelligence.nodes.InternalDataToolClient",
            lambda *args, **kwargs: InternalDataToolClient(retriever=test_retriever),
        )

        task = ResearchTask(
            parent_request_id="req-test-partial-1",
            worker=WorkerType.INTERNAL_INTELLIGENCE,
            goal="Assess GitLab Duo product architecture, packaging, and internal OKRs vs GitHub Copilot",
            focus="GitLab Duo Agent Platform, AI Gateway, zero retention data privacy, GitLab Credits, and self-hosted readiness",
            company_profile=CompanyProfile(name="GitLab"),
            business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
        )

        result = run_internal_intelligence(task)

        assert result.task_id == task.task_id
        assert result.worker == WorkerType.INTERNAL_INTELLIGENCE
        # Status must be PARTIAL because 4 of the 5 focus areas are missing
        assert result.status == WorkerStatus.PARTIAL
        assert len(result.evidence) > 0
        assert len(result.findings) > 0

        # Category check: AI Gateway / Privacy must map to PRODUCT_FEATURE (not pricing_packaging)
        finding = result.findings[0]
        assert finding.category == FindingCategory.PRODUCT_FEATURE

        # Realistic confidence: capped for synthetic data (<= 0.60)
        assert finding.confidence <= 0.60
        assert result.confidence <= 0.60

        # Limitations must contain synthetic data notice
        assert len(finding.limitations) > 0
        assert "synthetic" in finding.limitations[0].lower() or "adapted" in finding.limitations[0].lower()

        # Evidence publisher and timestamps must be present
        ev = result.evidence[0]
        assert ev.publisher is not None
        assert result.started_at is not None
        assert result.completed_at is not None

        # Gaps list must explicitly name missing focus areas
        uncovered_gaps = [g for g in result.gaps if "No internal evidence retrieved" in g]
        assert len(uncovered_gaps) >= 3


def test_mock_all_focus_areas_completed_scenario(monkeypatch):
    """Verifies behavior when all 5 strategic focus areas are seeded and indexed."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp_path = Path(tmpdir)
        collection_name = "test_internal_full"

        indexer = InternalIndexer(persist_dir=tmp_path, collection_name=collection_name)
        indexer.reset_collection()

        docs = [
            (
                "# GitLab Duo Agent Platform\n\nThe GitLab Duo Agent Platform reached General Availability in January 2026. "
                "It orchestrates autonomous lifecycle agents across the software development lifecycle.",
                "internal://product/duo_agent_platform.md",
                "Duo Agent Platform Architecture",
            ),
            (
                "# GitLab Credits and Monetization\n\nGitLab Credits are priced at $1.00 per credit on demand. "
                "Premium tier includes $12/user/month allowance and Ultimate tier includes $24/user/month allowance.",
                "internal://pricing/gitlab_credits_and_packaging.md",
                "GitLab Credits Pricing and Packaging",
            ),
            (
                "# GitLab AI Gateway & Privacy\n\nGitLab AI Gateway provides multi-model routing with strict zero-retention data privacy. "
                "Self-hosted AI Gateway is fully supported for air-gapped deployments.",
                "internal://architecture/ai_gateway_and_privacy.md",
                "AI Gateway and Privacy Architecture",
            ),
            (
                "# GitLab Internal Strategic OKRs\n\nQ2 FY2027 OKRs show paid consumption grew 50% QoQ. "
                "Enterprise ARR customers over $100k reached 1,519 organizations and NRR stabilized at 118%.",
                "internal://strategy/okrs_and_telemetry.md",
                "Internal Strategic OKRs and Telemetry",
            ),
            (
                "# Competitive Assessment: Duo vs GitHub Copilot\n\nGitLab Duo provides end-to-end SDLC coverage with a single unified data model, "
                "whereas GitHub Copilot focuses on IDE-centric code completions.",
                "internal://competitive/gitlab_duo_vs_github_copilot.md",
                "GitLab Duo vs GitHub Copilot Battlecard",
            ),
        ]

        for content, uri, title in docs:
            indexer.index_document(content=content, source_uri=uri, title=title, doc_type="internal_doc")

        test_retriever = InternalRetriever(persist_dir=tmp_path, collection_name=collection_name)
        monkeypatch.setattr(
            "edrak.agents.internal_intelligence.nodes.InternalDataToolClient",
            lambda *args, **kwargs: InternalDataToolClient(retriever=test_retriever),
        )

        task = ResearchTask(
            parent_request_id="req-test-full-1",
            worker=WorkerType.INTERNAL_INTELLIGENCE,
            goal="Assess GitLab Duo product architecture, packaging, and internal OKRs vs GitHub Copilot",
            focus="GitLab Duo Agent Platform, AI Gateway, zero retention data privacy, GitLab Credits, and self-hosted readiness",
            company_profile=get_gitlab_profile(),
            business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
        )

        result = run_internal_intelligence(task)

        assert result.task_id == task.task_id
        assert result.worker == WorkerType.INTERNAL_INTELLIGENCE
        # Status must be COMPLETED because all 5 focus areas are covered
        assert result.status == WorkerStatus.COMPLETED
        assert len(result.evidence) >= 5
        assert len(result.findings) >= 5

        # No missing focus area gaps
        uncovered_gaps = [g for g in result.gaps if "No internal evidence retrieved" in g]
        assert len(uncovered_gaps) == 0

        # Categories should include product_feature, pricing_packaging, market_signal, positioning
        categories = {f.category for f in result.findings}
        assert FindingCategory.PRODUCT_FEATURE in categories
        assert FindingCategory.PRICING_PACKAGING in categories
        assert FindingCategory.MARKET_SIGNAL in categories
        assert FindingCategory.POSITIONING in categories


def test_live_knowledge_base_query():
    """Runs a live end-to-end query against the full pre-indexed ChromaDB vector store."""
    task = ResearchTask(
        parent_request_id="req-live-eval-001",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal="Assess GitLab Duo product architecture, packaging, and internal OKRs vs GitHub Copilot",
        focus="GitLab Duo Agent Platform, AI Gateway, zero retention data privacy, GitLab Credits, and self-hosted readiness",
        company_profile=get_gitlab_profile(),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub Copilot", "Microsoft Azure DevOps"],
        ),
    )

    result = run_internal_intelligence(task)

    assert result.task_id == task.task_id
    assert result.worker == WorkerType.INTERNAL_INTELLIGENCE
    assert result.status == WorkerStatus.COMPLETED
    assert len(result.evidence) >= 5
    assert len(result.findings) >= 5
    assert result.confidence is not None
    assert result.confidence <= 0.60  # synthetic capped
    assert result.started_at is not None
    assert result.completed_at is not None
    for f in result.findings:
        for ref in f.evidence_refs:
            assert ref.evidence_id in [e.evidence_id for e in result.evidence]



