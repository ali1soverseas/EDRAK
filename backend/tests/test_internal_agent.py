"""Tests for Internal Intelligence Worker."""

import pytest
import tempfile
from pathlib import Path

from edrak.agents.internal_intelligence.graph import run_internal_intelligence
from edrak.contracts.request import BusinessContext, CompanyProfile, UseCase
from edrak.contracts.task import ResearchTask, WorkerType
from edrak.contracts.result import WorkerStatus
from edrak.rag.indexer import InternalIndexer


def test_internal_intelligence_worker_run():
    # Setup knowledge base with sample docs
    indexer = InternalIndexer()
    indexer.reset_collection()

    sample_doc = (
        "# GitLab Duo Architecture\n\n"
        "GitLab Duo operates via the AI Gateway, offering zero retention data privacy.\n\n"
        "Key capabilities include Code Suggestions, Duo Chat, and CI/CD Root Cause Analysis."
    )
    indexer.index_document(
        content=sample_doc,
        source_uri="internal://product/duo_architecture.md",
        title="GitLab Duo Architecture",
        doc_type="internal_doc",
    )

    task = ResearchTask(
        parent_request_id="req-test-1",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal="Investigate GitLab Duo architecture and privacy framework",
        focus="GitLab Duo, AI Gateway",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
    )

    result = run_internal_intelligence(task)

    assert result.task_id == task.task_id
    assert result.worker == WorkerType.INTERNAL_INTELLIGENCE
    assert result.status == WorkerStatus.COMPLETED
    assert len(result.evidence) > 0
    assert len(result.findings) > 0
    assert result.findings[0].evidence_ids[0] in [e.evidence_id for e in result.evidence]

