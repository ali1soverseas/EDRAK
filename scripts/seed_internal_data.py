"""Seed script to index NVIDIA internal knowledge into ChromaDB."""

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
    logger.info("==================================================================")
    logger.info("Initializing EDRAK Internal Knowledge Seeding (NVIDIA)...")
    logger.info("==================================================================")

    indexer = InternalIndexer()
    indexer.reset_collection()

    internal_data_dir = settings.get_absolute_internal_data_path()
    logger.info("Scanning directory: %s", internal_data_dir)

    total_chunks = indexer.index_directory(internal_data_dir, is_synthetic=True, workspace_id=settings.DEMO_WORKSPACE_ID)
    logger.info("Successfully indexed %d chunks into ChromaDB collection '%s'.", total_chunks, settings.CHROMA_COLLECTION_NAME)

    retriever = InternalRetriever()

    # Diagnostics print
    emb_type = type(indexer.embedding_fn).__name__
    space = (getattr(indexer.collection, "metadata", None) or {}).get("hnsw:space", "cosine")
    logger.info("Embedding class: %s | Distance metric: %s", emb_type, space)

    # Inspect collection documents
    data = indexer.collection.get(include=["metadatas"])
    metadatas = data.get("metadatas", [])
    origin_counts = {}
    syn_uri_counts = {}
    for m in metadatas:
        origin = m.get("origin", "unknown")
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
        if m.get("is_synthetic") in (True, "true", "True"):
            uri = m.get("source_uri", "unknown")
            syn_uri_counts[uri] = syn_uri_counts.get(uri, 0) + 1

    logger.info("Chunk counts per origin: %s", origin_counts)
    logger.info("Synthetic documents indexed (%d total chunks):", sum(syn_uri_counts.values()))
    for uri, count in syn_uri_counts.items():
        logger.info("  - %s (%d chunks)", uri, count)

    # Verification probe queries
    probes = [
        ("NVIDIA H100 H200 B200 GPU accelerator portfolio specifications", "nvidia_accelerator_portfolio"),
        ("CUDA cuDNN TensorRT software ecosystem framework compatibility", "nvidia_software_ecosystem"),
        ("NVIDIA pricing GPU cost per unit cloud rental AI Enterprise", "nvidia_pricing_commercial"),
        ("Microsoft Azure Amazon AWS Google Cloud NVIDIA GPU deployment adoption", "nvidia_customer_adoption"),
        ("Europe United States regional availability supply chain lead time", "nvidia_regional_availability"),
    ]

    logger.info("\n--- Running Probe Query Verifications ---")
    all_passed = True
    for query, expected_sub in probes:
        results = retriever.retrieve(query=query, top_k=5, workspace_id=settings.DEMO_WORKSPACE_ID)
        uris = [ev.source_url for ev in results]
        passed = any(expected_sub in u for u in uris)
        if passed:
            logger.info("  [PASS] Probe: '%s' -> Found '%s' in top-5 URIs", query, expected_sub)
        else:
            logger.error("  [FAIL] Probe: '%s' -> Did NOT find '%s' in top-5 URIs: %s", query, expected_sub, uris)
            all_passed = False

    if not all_passed:
        logger.error("Verification probes failed. Check embeddings and indexed chunks.")
        sys.exit(1)
    else:
        logger.info("All 5 verification probes PASSED successfully!")


if __name__ == "__main__":
    main()
