"""Each workspace searches only its own indexed documents."""

from __future__ import annotations

import pytest

from edrak.rag.indexer import InternalIndexer
from edrak.rag.retriever import InternalRetriever

ACME = "Acme sells industrial pumps. Our flagship pump model AX-9 costs 4200 dollars per unit."
GLOBEX = "Globex sells enterprise software. Our flagship product Orbit costs 90 dollars per seat."


@pytest.fixture
def index(tmp_path):
    indexer = InternalIndexer(persist_dir=tmp_path, collection_name="ws_test")
    indexer.index_document(ACME, "internal://acme.txt", extra_metadata={"filename": "acme.txt"}, workspace_id="ws-a")
    indexer.index_document(GLOBEX, "internal://globex.txt", extra_metadata={"filename": "globex.txt"}, workspace_id="ws-b")
    return indexer, InternalRetriever(persist_dir=tmp_path, collection_name="ws_test", min_score=0.0)


def test_retrieval_is_scoped_to_the_workspace(index):
    _, retriever = index
    only_a = retriever.retrieve("flagship product price", workspace_id="ws-a")
    only_b = retriever.retrieve("flagship product price", workspace_id="ws-b")
    assert only_a and all("acme" in ev.source_url for ev in only_a)
    assert only_b and all("globex" in ev.source_url for ev in only_b)
    assert retriever.retrieve("flagship product price", workspace_id="ws-none") == []


def test_scope_combines_with_other_filters(index):
    _, retriever = index
    hits = retriever.retrieve(
        "flagship product price", workspace_id="ws-a", where_filter={"origin": {"$ne": "public_handbook"}}
    )
    assert hits and all("acme" in ev.source_url for ev in hits)


def test_untagged_chunks_go_to_the_demo_workspace(tmp_path):
    indexer = InternalIndexer(persist_dir=tmp_path, collection_name="ws_legacy")
    indexer.index_document(ACME, "internal://old.txt")
    assert indexer.backfill_workspace("ws-demo") == 1
    assert indexer.backfill_workspace("ws-demo") == 0
    assert [d["filename"] for d in indexer.workspace_documents("ws-demo")] == []  # no filename recorded
    assert indexer.collection.get(where={"workspace_id": "ws-demo"})["ids"]


def test_documents_listing_and_delete(index):
    indexer, _ = index
    assert indexer.workspace_documents("ws-a") == [{"filename": "acme.txt", "chunks": 1, "kind": "file"}]
    indexer.delete_workspace_documents("ws-a", filename="acme.txt")
    assert indexer.workspace_documents("ws-a") == []
    assert indexer.workspace_documents("ws-b")
