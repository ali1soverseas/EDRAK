"""Complete End-to-End Test for EDRAK Internal Intelligence Subsystem.

Tests the full flow after fetching:
1. Clean: Raw HTML -> Markdown (data/handbook/cleaned/)
2. Chunk: Markdown -> Semantic Chunks (data/handbook/chunked/)
3. Ingest: Chunks & Internal Docs -> ChromaDB with Local Hugging Face Embeddings
4. Retrieve: Semantic vector queries -> Typed Evidence objects with provenance
5. Agent Execution: Run LangGraph InternalIntelligenceWorker -> Valid WorkerResult contract
"""

import json
import logging
from pathlib import Path
import sys
import pytest

# Ensure repo_root, backend/src, and scripts are on sys.path
repo_root = Path(__file__).resolve().parent.parent.parent
backend_src = repo_root / "backend" / "src"
scripts_dir = repo_root / "scripts"

for p in [str(repo_root), str(backend_src), str(scripts_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from clean_gitlab_handbook import HandbookCleaner
from chunk_gitlab_handbook import HandbookChunker
from ingest_internal_data import ingest_chunked_handbook
from edrak.core.config import settings
from edrak.contracts.request import BusinessContext, CompanyProfile, UseCase
from edrak.contracts.task import ResearchTask, WorkerType
from edrak.contracts.result import WorkerResult, WorkerStatus
from edrak.rag.indexer import InternalIndexer
from edrak.rag.retriever import InternalRetriever
from edrak.agents.internal_intelligence.graph import run_internal_intelligence

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_internal_flow")


def test_full_internal_flow():
    """Validates the full post-fetch lifecycle: clean -> chunk -> ingest -> retrieve -> agent."""
    logger.info("==================================================================")
    logger.info("TESTING COMPLETE INTERNAL FLOW: CLEAN -> CHUNK -> INGEST -> RETRIEVE -> AGENT")
    logger.info("==================================================================")

    # -------------------------------------------------------------------------
    # STEP 1: CLEAN RAW DATA
    # -------------------------------------------------------------------------
    logger.info("\n>>> [STEP 1/5] Cleaning Raw HTML to Markdown...")
    cleaner = HandbookCleaner()
    cleaned_count = cleaner.process_all()
    logger.info("Step 1 Complete: %d files cleaned into %s", cleaned_count, settings.get_absolute_cleaned_handbook_path())
    assert cleaned_count > 0, "Expected at least 1 cleaned file"

    # -------------------------------------------------------------------------
    # STEP 2: CHUNK CLEANED DATA
    # -------------------------------------------------------------------------
    logger.info("\n>>> [STEP 2/5] Chunking Cleaned Markdown Documents...")
    chunker = HandbookChunker(chunk_size=1000, chunk_overlap=150)
    chunk_count = chunker.process_all()
    logger.info("Step 2 Complete: %d semantic chunks generated in %s", chunk_count, settings.get_absolute_chunked_handbook_path())
    assert chunk_count > 0, "Expected at least 1 generated chunk"

    # -------------------------------------------------------------------------
    # STEP 3: INGEST INTO CHROMADB (Vector Store)
    # -------------------------------------------------------------------------
    logger.info("\n>>> [STEP 3/5] Ingesting Chunks & Internal Docs into ChromaDB...")
    indexer = InternalIndexer()
    indexer.reset_collection()

    hb_chunks = ingest_chunked_handbook(indexer, settings.get_absolute_chunked_handbook_path())
    internal_chunks = indexer.index_directory(settings.get_absolute_internal_data_path())
    total_indexed = hb_chunks + internal_chunks

    logger.info("Step 3 Complete: Indexed %d total chunks into collection '%s'",
                total_indexed, settings.CHROMA_COLLECTION_NAME)
    assert total_indexed > 0, "Expected ChromaDB collection to have indexed chunks"

    # -------------------------------------------------------------------------
    # STEP 4: RETRIEVE EVIDENCE VIA VECTOR SIMILARITY
    # -------------------------------------------------------------------------
    logger.info("\n>>> [STEP 4/5] Testing Semantic Retrieval via InternalRetriever...")
    retriever = InternalRetriever()
    test_query = "GitLab Duo Enterprise pricing and seat cost"
    evidence_results = retriever.retrieve(query=test_query, top_k=3)

    logger.info("Query: '%s' returned %d evidence items:", test_query, len(evidence_results))
    for i, ev in enumerate(evidence_results, 1):
        logger.info("  [%d] Title: '%s' | URL: %s | Synthetic: %s", i, ev.source_title, ev.source_url, ev.is_synthetic)
        logger.info("      Fact: %s...", ev.extracted_fact[:100].replace("\n", " "))

    assert len(evidence_results) > 0, "Expected retrieval to find relevant evidence"

    # -------------------------------------------------------------------------
    # STEP 5: RUN INTERNAL INTELLIGENCE AGENT (LangGraph Workflow)
    # -------------------------------------------------------------------------
    logger.info("\n>>> [STEP 5/5] Running Internal Intelligence Worker LangGraph Agent...")
    task = ResearchTask(
        task_id="task_internal_gitlab_duo_eval",
        parent_request_id="req_internal_eval_001",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal="Assess GitLab Duo product architecture, pricing tiers, and internal telemetry metrics vs GitHub Copilot",
        focus="Internal GitLab Duo packaging, pricing, privacy architecture, and user retention",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub Copilot", "GitLab Duo"],
        ),
    )

    worker_result: WorkerResult = run_internal_intelligence(task)

    logger.info("\n" + "=" * 70)
    logger.info("WORKER RESULT CONTRACT OUTPUT (Comprehensive Inspection)")
    logger.info("=" * 70)
    logger.info("Task ID:       %s", worker_result.task_id)
    logger.info("Worker:        %s", worker_result.worker.value)
    logger.info("Status:        %s", worker_result.status.value)
    logger.info("Metadata:      %s", json.dumps(worker_result.metadata))

    logger.info("\n--- [1] STRUCTURED FINDINGS (%d items) ---", len(worker_result.findings))
    for i, f in enumerate(worker_result.findings, 1):
        logger.info("  Finding #%d [ID: %s]:", i, f.finding_id)
        logger.info("    Category:       %s", f.category.value)
        logger.info("    Confidence:     %s", f.confidence)
        logger.info("    Evidence Refs:  %s", f.evidence_ids)
        logger.info("    Statement:      \"%s\"", f.statement)

    logger.info("\n--- [2] CITED EVIDENCE POOL (%d items) ---", len(worker_result.evidence))
    for i, ev in enumerate(worker_result.evidence, 1):
        logger.info("  Evidence #%d [ID: %s]:", i, ev.evidence_id)
        logger.info("    Title:          %s", ev.source_title)
        logger.info("    Source URL:     %s", ev.source_url)
        logger.info("    Source Type:    %s", ev.source_type.value)
        logger.info("    Retrieved At:   %s", ev.retrieved_at.isoformat())
        logger.info("    Extracted Fact: \"%s...\"", ev.extracted_fact[:140].replace("\n", " "))
        logger.info("    Metadata:       %s", ev.metadata)

    logger.info("\n--- [3] LIMITATIONS AND GAPS (%d items) ---", len(worker_result.gaps))
    for g in worker_result.gaps:
        logger.info("  • %s", g)

    # -------------------------------------------------------------------------
    # VALIDATION ASSERTIONS
    # -------------------------------------------------------------------------
    assert worker_result.status == WorkerStatus.COMPLETED, f"Expected COMPLETED but got {worker_result.status}"
    assert len(worker_result.evidence) > 0, "Worker result must contain evidence items"
    assert len(worker_result.findings) > 0, "Worker result must contain structured findings"
    assert worker_result.task_id == task.task_id, "Task ID mismatch"
    assert all(len(f.evidence_ids) > 0 for f in worker_result.findings), "All findings must link to at least one evidence ID"

    # Demonstrate strict Pydantic JSON Serialization conforming to contract schema
    result_json = worker_result.model_dump_json(indent=2)
    assert "findings" in result_json and "evidence" in result_json
    logger.info("\n[JSON Schema Validation] WorkerResult successfully validates and serializes to standard contract JSON.")

    logger.info("\n" + "=" * 70)
    logger.info("ALL FLOW TESTS PASSED SUCCESSFULLY! (Clean -> Chunk -> Ingest -> Retrieve -> Agent)")
    logger.info("=" * 70)


if __name__ == "__main__":
    test_full_internal_flow()
