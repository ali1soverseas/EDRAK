"""Internal Data and Handbook Ingestion Script for EDRAK ChromaDB Vector Store.

Ingests pre-computed handbook chunks from data/handbook/chunked/ as well as
internal markdown documents from data/internal/ into ChromaDB using local Hugging Face embeddings.
"""

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List

# Ensure backend/src is on sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
if str(backend_src) not in sys.path:
    sys.path.insert(0, str(backend_src))

from edrak.core.config import settings
from edrak.rag.indexer import InternalIndexer
from edrak.rag.retriever import InternalRetriever

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_internal_data")


def ingest_chunked_handbook(indexer: InternalIndexer, chunk_dir: Path) -> int:
    """Loads pre-chunked JSON files and inserts them into ChromaDB."""
    if not chunk_dir.exists():
        logger.warning("Handbook chunk directory not found: %s", chunk_dir)
        return 0

    chunk_files = list(chunk_dir.glob("*_chunks.json"))
    if not chunk_files:
        logger.warning("No *_chunks.json files found in %s", chunk_dir)
        return 0

    total_chunks = 0
    all_ids: List[str] = []
    all_documents: List[str] = []
    all_metadatas: List[Dict[str, Any]] = []

    seen_ids = set()
    for chunk_file in chunk_files:
        try:
            chunks = json.loads(chunk_file.read_text(encoding="utf-8"))
            for chunk in chunks:
                chunk_id = chunk["chunk_id"]
                if chunk_id in seen_ids:
                    chunk_id = f"{chunk_id}_{len(seen_ids)}"
                seen_ids.add(chunk_id)

                content = chunk["content"]
                metadata = {
                    "source_uri": chunk.get("source_uri", ""),
                    "title": chunk.get("title", ""),
                    "section": chunk.get("section", ""),
                    "heading": chunk.get("heading", ""),
                    "doc_type": chunk.get("doc_type", "internal_handbook"),
                }
                all_ids.append(chunk_id)
                all_documents.append(content)
                all_metadatas.append(metadata)
        except Exception as e:
            logger.error("Error reading %s: %s", chunk_file.name, e)

    if all_ids:
        # Upsert in batches of 100 for efficiency
        batch_size = 100
        for i in range(0, len(all_ids), batch_size):
            b_ids = all_ids[i : i + batch_size]
            b_docs = all_documents[i : i + batch_size]
            b_metas = all_metadatas[i : i + batch_size]
            indexer.collection.upsert(
                ids=b_ids,
                documents=b_docs,
                metadatas=b_metas,
            )
        total_chunks = len(all_ids)
        logger.info("Ingested %d handbook chunks from %s", total_chunks, chunk_dir)

    return total_chunks


def main():
    parser = argparse.ArgumentParser(description="Ingest internal data and handbook chunks into ChromaDB.")
    parser.add_argument("--reset", action="store_true", help="Reset ChromaDB collection before ingestion")
    args = parser.parse_args()

    logger.info("==================================================================")
    logger.info("EDRAK Internal Knowledge Ingestion (ChromaDB + Local HuggingFace)")
    logger.info("==================================================================")
    logger.info("Embedding Model: %s (Dimension: %d)", settings.EMBEDDING_MODEL_NAME, settings.EMBEDDING_DIMENSION)
    logger.info("Vector Store Path: %s", settings.get_absolute_vector_store_path())

    indexer = InternalIndexer()
    if args.reset:
        logger.info("Resetting collection '%s'...", settings.CHROMA_COLLECTION_NAME)
        indexer.reset_collection()

    # 1. Ingest handbook chunks if present
    chunk_dir = settings.get_absolute_chunked_handbook_path()
    handbook_chunks_count = ingest_chunked_handbook(indexer, chunk_dir)

    # 2. Ingest internal markdown documents
    internal_data_dir = settings.get_absolute_internal_data_path()
    logger.info("Indexing internal documents from %s...", internal_data_dir)
    internal_chunks_count = indexer.index_directory(internal_data_dir)

    total_chunks = handbook_chunks_count + internal_chunks_count
    logger.info("Ingestion completed! Total chunks indexed: %d (Handbook: %d, Internal Docs: %d)",
                total_chunks, handbook_chunks_count, internal_chunks_count)

    # 3. Verification queries
    logger.info("Running verification retrieval checks...")
    retriever = InternalRetriever()
    test_queries = [
        "GitLab Duo pricing tier and Enterprise seat packaging",
        "GitLab internal architecture code suggestions latency",
        "GitLab handbook values and company strategy",
    ]
    for query in test_queries:
        logger.info("\n--- Query: '%s' ---", query)
        results = retriever.retrieve(query=query, top_k=2)
        if not results:
            logger.warning("  No evidence retrieved.")
        for i, ev in enumerate(results, 1):
            logger.info("  [%d] Title: '%s'", i, ev.source_title)
            logger.info("      URI: %s (Type: %s)", ev.source_url, ev.source_type.value)
            snippet = (ev.excerpt or ev.extracted_fact)[:140].replace("\n", " ")
            logger.info("      Snippet: %s...", snippet)


if __name__ == "__main__":
    main()
