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
    # A chunk written before workspaces existed has no workspace_id; new code can no longer write one.
    indexer.collection.upsert(ids=["old-1"], documents=[ACME], metadatas=[{"source_uri": "internal://old.txt", "title": "old"}])
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


def test_a_search_without_a_workspace_returns_nothing_unless_explicitly_allowed(index):
    _, retriever = index
    assert retriever.retrieve("flagship product price") == []
    assert retriever.retrieve("flagship product price", workspace_id="") == []
    everything = retriever.retrieve("flagship product price", allow_all_workspaces=True)
    assert {"acme" in ev.source_url for ev in everything} == {True, False}  # the escape hatch really spans both


def test_a_new_workspace_sees_none_of_the_other_workspaces_data(index):
    """The NVIDIA-account case: workspaces A and B hold documents, a fresh workspace C holds none."""
    _, retriever = index
    assert retriever.retrieve("flagship product price", workspace_id="ws-nvidia") == []
    assert retriever.retrieve("anything about pumps or software", workspace_id="ws-nvidia") == []


def test_indexing_without_a_workspace_is_refused(tmp_path):
    indexer = InternalIndexer(persist_dir=tmp_path, collection_name="ws_strict")
    with pytest.raises(ValueError):
        indexer.index_document(ACME, "internal://x.txt")
    assert indexer.collection.count() == 0


def test_the_same_file_in_two_workspaces_does_not_overwrite(tmp_path):
    indexer = InternalIndexer(persist_dir=tmp_path, collection_name="ws_same")
    indexer.index_document(ACME, "internal://same.txt", workspace_id="ws-a")
    indexer.index_document(ACME, "internal://same.txt", workspace_id="ws-b")
    got = indexer.collection.get(include=["metadatas"])
    assert sorted(m["workspace_id"] for m in got["metadatas"]) == ["ws-a", "ws-b"]
    assert len(set(got["ids"])) == 2


def test_resetting_one_workspace_leaves_the_others(index):
    indexer, retriever = index
    indexer.delete_workspace_documents("ws-a")
    assert retriever.retrieve("flagship product price", workspace_id="ws-a") == []
    assert retriever.retrieve("flagship product price", workspace_id="ws-b")


def test_a_chunk_cannot_be_fetched_by_id_from_another_workspace(index):
    indexer, retriever = index
    chunk_id = indexer.collection.get(where={"workspace_id": "ws-a"})["ids"][0]
    assert retriever.get_document_by_id(chunk_id, "ws-a") is not None
    assert retriever.get_document_by_id(chunk_id, "ws-b") is None
    assert retriever.get_document_by_id(chunk_id) is None


def test_the_internal_worker_without_a_workspace_reads_nothing(index, monkeypatch):
    from edrak.agents.internal_intelligence import nodes
    from edrak.contracts import BusinessContext, CompanyProfile, ResearchTask, WorkerType

    indexer, real = index

    class NoThreshold:  # the tiny test documents score below the worker's real relevance cutoff
        def retrieve(self, **kw):
            return real.retrieve(**{**kw, "min_score": 0.0})

    monkeypatch.setattr(nodes, "InternalRetriever", lambda: NoThreshold())
    def task(workspace_id):
        return ResearchTask(parent_request_id="r", worker=WorkerType.INTERNAL_INTELLIGENCE, goal="flagship product price", focus="pricing",
                            company_profile=CompanyProfile(name="Acme", workspace_id=workspace_id),
                            business_context=BusinessContext(use_case="competitive_intelligence"))

    state = {"queries": ["flagship product price"]}
    assert nodes.retrieve_evidence_node({**state, "task": task(None)})["retrieved_evidence"] == []
    found = nodes.retrieve_evidence_node({**state, "task": task("ws-a")})["retrieved_evidence"]
    assert found and all("acme" in ev.source_url for ev in found)
