"""Seed script to index GitLab internal knowledge and handbook documents into ChromaDB."""

import logging
from pathlib import Path
import sys

# Ensure backend/src is on sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
if str(backend_src) not in sys.path:
    sys.path.insert(0, str(backend_src))

from edrak.core.config import settings
from edrak.rag.indexer import InternalIndexer
from edrak.rag.retriever import InternalRetriever

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("Initializing EDRAK Internal Knowledge Ingestion...")
    indexer = InternalIndexer()
    indexer.reset_collection()

    internal_data_dir = settings.get_absolute_internal_data_path()
    logger.info("Scanning directory: %s", internal_data_dir)

    total_chunks = indexer.index_directory(internal_data_dir)
    logger.info("Successfully indexed %d chunks into ChromaDB collection '%s'.", total_chunks, settings.CHROMA_COLLECTION_NAME)

    # Verification query
    retriever = InternalRetriever()
    test_query = "GitLab Duo pricing architecture OKRs"
    evidence_results = retriever.retrieve(query=test_query, top_k=3)
    logger.info("Verification test query '%s' retrieved %d items:", test_query, len(evidence_results))
    for i, ev in enumerate(evidence_results, 1):
        logger.info("  [%d] Title: '%s' | URI: %s | Confidence: %.2f", i, ev.title, ev.source_uri, ev.confidence_score)


if __name__ == "__main__":
    main()
