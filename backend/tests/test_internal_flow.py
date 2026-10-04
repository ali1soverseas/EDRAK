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
from edrak.contracts.task import ResearchTask, WorkerRole
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
        logger.info("  [%d] Title: '%s' | URI: %s | Score: %.2f", i, ev.title, ev.source_uri, ev.confidence_score)
        logger.info("      Excerpt: %s...", ev.content[:100].replace("\n", " "))

    assert len(evidence_results) > 0, "Expected retrieval to find relevant evidence"

    # -------------------------------------------------------------------------
    # STEP 5: RUN INTERNAL INTELLIGENCE AGENT (LangGraph Workflow)
    # -------------------------------------------------------------------------
    logger.info("\n>>> [STEP 5/5] Running Internal Intelligence Worker LangGraph Agent...")
    task = ResearchTask(
        task_id="task_internal_gitlab_duo_eval",
        request_id="req_internal_eval_001",
        worker_role=WorkerRole.INTERNAL_INTELLIGENCE,
        objective="Assess GitLab Duo product architecture, pricing tiers, and internal telemetry metrics vs GitHub Copilot",
        scope="Internal GitLab Duo packaging, pricing, privacy architecture, and user retention",
        key_questions=[
            "What is the add-on pricing model for GitLab Duo Pro and Enterprise?",
            "What internal architecture advantages does GitLab Duo offer regarding privacy and multi-model routing?",
            "What do internal telemetry metrics indicate regarding seat adoption and churn?",
        ],
    )

    worker_result: WorkerResult = run_internal_intelligence(task)

    logger.info("\n" + "=" * 70)
    logger.info("WORKER RESULT CONTRACT OUTPUT (Comprehensive Inspection)")
    logger.info("=" * 70)
    logger.info("Task ID:       %s", worker_result.task_id)
    logger.info("Worker Role:   %s", worker_result.worker_role)
    logger.info("Status:        %s", worker_result.status.value)
    logger.info("Completed At:  %s", worker_result.completed_at.isoformat())
    logger.info("Metadata:      %s", json.dumps(worker_result.metadata))

    logger.info("\n--- [1] HIGH-LEVEL SUMMARY ---")
    logger.info("%s", worker_result.summary)

    logger.info("\n--- [2] STRUCTURED FINDINGS (%d items) ---", len(worker_result.findings))
    for i, f in enumerate(worker_result.findings, 1):
        logger.info("  Finding #%d [ID: %s]:", i, f.id)
        logger.info("    Domain Topic:   %s", f.domain_topic)
        logger.info("    Confidence:     %.2f", f.confidence)
        logger.info("    Evidence Refs:  %s", f.evidence_ids)
        logger.info("    Statement:      \"%s\"", f.statement)
        logger.info("    Metadata:       %s", f.metadata)

    logger.info("\n--- [3] CITED EVIDENCE POOL (%d items) ---", len(worker_result.evidence))
    for i, ev in enumerate(worker_result.evidence, 1):
        logger.info("  Evidence #%d [ID: %s]:", i, ev.id)
        logger.info("    Title:          %s", ev.title)
        logger.info("    Source URI:     %s", ev.source_uri)
        logger.info("    Source Type:    %s", ev.source_type)
        logger.info("    Confidence:     %.2f", ev.confidence_score)
        logger.info("    Timestamp:      %s", ev.timestamp.isoformat())
        logger.info("    Content Excerpt:\"%s...\"", ev.content[:140].replace("\n", " "))
        logger.info("    Metadata:       %s", ev.metadata)

    logger.info("\n--- [4] LIMITATIONS AND GAPS (%d items) ---", len(worker_result.limitations_and_gaps))
    for g in worker_result.limitations_and_gaps:
        logger.info("  • %s", g)

    # -------------------------------------------------------------------------
    # VALIDATION ASSERTIONS
    # -------------------------------------------------------------------------
    assert worker_result.status == WorkerStatus.SUCCESS, f"Expected SUCCESS but got {worker_result.status}"
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
