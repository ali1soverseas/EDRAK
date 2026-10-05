"""Tests for RAG indexer and retriever."""

import pytest
import tempfile
from pathlib import Path
from edrak.rag.indexer import InternalIndexer
from edrak.rag.retriever import InternalRetriever


def test_internal_indexer_and_retriever():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp_path = Path(tmpdir)
        collection_name = "test_collection"

        indexer = InternalIndexer(persist_dir=tmp_path, collection_name=collection_name)
        indexer.reset_collection()

        sample_content = (
            "# GitLab Duo Pricing\n\n"
            "GitLab Duo Pro costs $19 per user per month.\n\n"
            "GitLab Duo Enterprise costs $39 per user per month and requires GitLab Ultimate."
        )

        chunks_indexed = indexer.index_document(
            content=sample_content,
            source_uri="internal://test/pricing.md",
            title="GitLab Duo Pricing Guide",
            doc_type="internal_doc",
        )
        assert chunks_indexed > 0

        retriever = InternalRetriever(persist_dir=tmp_path, collection_name=collection_name)
        results = retriever.retrieve(query="How much does GitLab Duo Pro cost?", top_k=2)

        assert len(results) > 0
        assert "19" in (results[0].excerpt or results[0].extracted_fact)
        assert results[0].source_url == "internal://test/pricing.md"
