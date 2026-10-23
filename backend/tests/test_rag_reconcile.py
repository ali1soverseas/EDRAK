import sqlite3

from edrak.core.config import settings
from edrak.rag.indexer import InternalIndexer
from edrak.rag.reconcile import restore_missing_workspace_chunks
from edrak.rag.retriever import InternalRetriever


def test_restores_missing_workspace_chunks_without_touching_other_workspaces(tmp_path, monkeypatch):
    workspace_id = "workspace-current"
    other_workspace_id = "workspace-other"
    db_path = tmp_path / "edrak.db"
    monkeypatch.setattr(settings, "EDRAK_DB_PATH", db_path)

    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE documents (
                id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, origin TEXT,
                is_synthetic INTEGER, status TEXT
            );
            CREATE TABLE document_chunks (
                id TEXT PRIMARY KEY, workspace_id TEXT, document_id TEXT,
                chunk_index INTEGER, content TEXT, locator TEXT, excluded INTEGER
            );
            """
        )
        db.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
            ("doc-1", workspace_id, "nvidia-brief.md", "upload", 0, "indexed"),
        )
        db.executemany(
            "INSERT INTO document_chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "chunk-1", workspace_id, "doc-1", 0,
                    "NVIDIA competes in accelerated AI computing and data-center platforms.",
                    "Competitive position", 0,
                ),
                ("chunk-excluded", workspace_id, "doc-1", 1, "Excluded text", None, 1),
            ],
        )

    indexer = InternalIndexer(persist_dir=tmp_path / "vectors", collection_name="reconcile_test")
    indexer.collection.upsert(
        ids=["other-chunk"],
        documents=["A separate workspace's indexed document."],
        metadatas=[{"workspace_id": other_workspace_id, "origin": "upload"}],
    )

    assert restore_missing_workspace_chunks(workspace_id, indexer.collection) == 1
    assert restore_missing_workspace_chunks(workspace_id, indexer.collection) == 0

    restored = indexer.collection.get(
        where={"workspace_id": workspace_id}, include=["documents", "metadatas"]
    )
    assert restored["ids"] == ["chunk-1"]
    assert restored["metadatas"][0]["document_id"] == "doc-1"
    assert restored["metadatas"][0]["origin"] == "upload"
    assert indexer.collection.get(ids=["other-chunk"])["ids"] == ["other-chunk"]

    retriever = InternalRetriever(persist_dir=tmp_path / "vectors", collection_name="reconcile_test")
    results = retriever.retrieve(
        "NVIDIA accelerated AI computing platforms",
        top_k=1,
        min_score=0,
        workspace_id=workspace_id,
    )
    assert results
    assert results[0].metadata["document_id"] == "doc-1"
