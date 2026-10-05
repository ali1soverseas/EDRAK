"""Internal knowledge retriever for EDRAK RAG subsystem.

Queries the ChromaDB collection and produces typed Evidence objects with provenance.

Features:
  * Relevance threshold + over-fetch + dedupe by (source, heading); no more "always
    return top_k no matter how irrelevant" and no 0.5 confidence floor.
  * extracted_fact = most query-relevant complete sentence (not the first non-# line,
    which was '**Document ID:** ...' for the synthetic docs).
  * is_synthetic parsed correctly (bool("False") is True!) and defaults to False.
  * Retriever never deletes the collection on an embedding mismatch; it fails loudly.
  * Score computed from the collection's actual distance metric.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings

from edrak.contracts.evidence import Evidence, SourceType
from edrak.core.config import settings
from edrak.rag.embeddings import get_embedding_function
from edrak.rag.text_utils import as_bool, best_fact

logger = logging.getLogger(__name__)

COLLECTION_METADATA = {
    "description": "EDRAK Internal Knowledge Base & Handbook",
    "hnsw:space": "cosine",
}


class InternalRetriever:
    """Retrieves relevant internal documentation and generates structured Evidence."""

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        collection_name: Optional[str] = None,
        min_score: Optional[float] = None,
    ):
        self.persist_dir = persist_dir or settings.get_absolute_vector_store_path()
        self.collection_name = collection_name or settings.CHROMA_COLLECTION_NAME
        self.min_score = (
            min_score if min_score is not None else float(getattr(settings, "RETRIEVAL_MIN_SCORE", 0.35))
        )
        self.embedding_fn = get_embedding_function()
        logger.info("Retriever embedding function: %s", type(self.embedding_fn).__name__)

        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        try:
            self.collection = self._client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn,
                metadata=COLLECTION_METADATA,
            )
        except ValueError as e:
            # Do NOT delete the persisted index from a read path.
            raise RuntimeError(
                f"Embedding function mismatch for collection '{self.collection_name}'. "
                "Re-run the seeding script to rebuild the index with the current embedding settings."
            ) from e

    # ------------------------------------------------------------------ helpers
    def _score(self, dist: float) -> float:
        space = (getattr(self.collection, "metadata", None) or {}).get("hnsw:space", "l2")
        s = 1.0 - dist if space in ("cosine", "ip") else 1.0 - dist / 2.0
        return max(0.0, min(1.0, s))

    @staticmethod
    def _publisher(is_syn: bool, meta: Dict[str, Any]) -> str:
        if is_syn:
            return "GitLab Internal Knowledge (Synthetic)"
        uri = str(meta.get("source_uri", ""))
        if meta.get("doc_type") == "internal_handbook" or uri.startswith("http"):
            return "GitLab Handbook (public)"
        return "GitLab Internal Knowledge"

    def _to_evidence(
        self, doc: str, meta: Dict[str, Any], query: str = "", score: Optional[float] = None
    ) -> Evidence:
        title = str(meta.get("title", "Internal Document"))
        source_uri = str(meta.get("source_uri", "internal://unknown"))
        is_syn = as_bool(meta.get("is_synthetic"), default=False)
        source_type = SourceType.SYNTHETIC_INTERNAL if is_syn else SourceType.INTERNAL_DOCUMENT

        extracted_fact = best_fact(doc, query) or f"Internal intelligence finding from {title}"

        md: Dict[str, Any] = {**meta, "search_query": query}
        if score is not None:
            md["confidence_score"] = round(score, 2)
            md["relevance_score"] = round(score, 2)

        return Evidence(
            source_type=source_type,
            source_title=title,
            source_url=source_uri,
            publisher=self._publisher(is_syn, meta),
            extracted_fact=extracted_fact,
            excerpt=doc,
            is_synthetic=is_syn,
            metadata=md,
        )

    # ------------------------------------------------------------------ public API
    def retrieve(
        self,
        query: str,
        top_k: int = 4,
        where_filter: Optional[Dict[str, Any]] = None,
        min_score: Optional[float] = None,
    ) -> List[Evidence]:
        """Semantic search -> thresholded, de-duplicated Evidence (best first)."""
        if not query.strip():
            return []
        threshold = self.min_score if min_score is None else min_score

        try:
            count = self.collection.count()
            if count == 0:
                logger.warning("Internal vector collection is empty. Returning 0 evidence.")
                return []
            kwargs: Dict[str, Any] = {
                "query_texts": [query],
                "n_results": min(max(top_k * 3, top_k), count),  # over-fetch, then filter
            }
            if where_filter:
                kwargs["where"] = where_filter
            results = self.collection.query(**kwargs)
        except Exception as e:
            logger.error("Error querying ChromaDB vector store: %s", e)
            return []

        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0] or [0.5] * len(documents)

        best: Dict[Tuple[str, str, str], Tuple[float, str, Dict[str, Any]]] = {}
        dropped = 0
        for i, doc in enumerate(documents):
            meta = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
            score = self._score(distances[i] if i < len(distances) else 0.5)
            if score < threshold:
                dropped += 1
                continue
            normalized_snippet = " ".join(doc.split())[:200]
            key = (str(meta.get("source_uri", "")), str(meta.get("heading", "")), normalized_snippet)
            if key not in best or score > best[key][0]:
                best[key] = (score, doc, meta)

        ranked = sorted(best.values(), key=lambda t: t[0], reverse=True)[:top_k]
        logger.info(
            "retrieve(%r): %d candidates, %d below %.2f, %d returned",
            query, len(documents), dropped, threshold, len(ranked),
        )
        return [self._to_evidence(doc, meta, query, score) for score, doc, meta in ranked]

    def get_document_by_id(self, chunk_id: str) -> Optional[Evidence]:
        """Retrieves an exact chunk by its ID."""
        try:
            res = self.collection.get(ids=[chunk_id])
            if res and res.get("documents"):
                meta = res["metadatas"][0] if res.get("metadatas") else {}
                return self._to_evidence(res["documents"][0], meta)
        except Exception as e:
            logger.error("Error retrieving chunk %s: %s", chunk_id, e)
        return None
