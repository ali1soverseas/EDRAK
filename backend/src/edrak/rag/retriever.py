"""Internal knowledge retriever for EDRAK RAG subsystem.

Queries the ChromaDB collection and produces typed Evidence objects with provenance.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

import chromadb
from chromadb.config import Settings as ChromaSettings

from edrak.contracts.evidence import Evidence, SourceType
from edrak.core.config import settings
from edrak.rag.embeddings import get_embedding_function

logger = logging.getLogger(__name__)


class InternalRetriever:
    """Retrieves relevant internal documentation and generates structured Evidence."""

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        collection_name: Optional[str] = None,
    ):
        self.persist_dir = persist_dir or settings.get_absolute_vector_store_path()
        self.collection_name = collection_name or settings.CHROMA_COLLECTION_NAME
        self.embedding_fn = get_embedding_function()

        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        try:
            self.collection = self._client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn,
                metadata={"description": "EDRAK Internal Knowledge Base & Handbook"},
            )
        except ValueError:
            logger.warning("Embedding function mismatch on collection '%s' in retriever. Re-getting...", self.collection_name)
            try:
                self._client.delete_collection(self.collection_name)
            except Exception:
                pass
            self.collection = self._client.create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn,
                metadata={"description": "EDRAK Internal Knowledge Base & Handbook"},
            )

    def retrieve(
        self,
        query: str,
        top_k: int = 4,
        where_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Evidence]:
        """Queries the vector store for semantic matches and returns Evidence objects."""
        if not query.strip():
            return []

        try:
            # Check collection count
            count = self.collection.count()
            if count == 0:
                logger.warning("Internal vector collection is empty. Returning 0 evidence.")
                return []

            actual_k = min(top_k, count)
            kwargs: Dict[str, Any] = {
                "query_texts": [query],
                "n_results": actual_k,
            }
            if where_filter:
                kwargs["where"] = where_filter

            results = self.collection.query(**kwargs)
        except Exception as e:
            logger.error("Error querying ChromaDB vector store: %s", e)
            return []

        evidence_items: List[Evidence] = []

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0] if "distances" in results and results["distances"] else [0.0] * len(documents)

        for i, doc in enumerate(documents):
            meta = metadatas[i] if i < len(metadatas) else {}
            dist = distances[i] if i < len(distances) else 0.5

            # Convert distance to rough confidence score between 0.5 and 0.99
            confidence = max(0.5, min(0.99, 1.0 - (dist / 2.0)))

            source_uri = str(meta.get("source_uri", "internal://handbook/doc"))
            title = str(meta.get("title", "Internal Document"))
            is_syn = bool(meta.get("is_synthetic", True))
            source_type = SourceType.SYNTHETIC_INTERNAL if is_syn else SourceType.INTERNAL_DOCUMENT

            # Extract first meaningful sentence as extracted_fact
            fact_lines = [l.strip() for l in doc.split("\n") if l.strip() and not l.startswith("#")]
            extracted_fact = fact_lines[0] if fact_lines else doc.strip()[:200]
            if not extracted_fact:
                extracted_fact = f"Internal intelligence finding from {title}"

            ev = Evidence(
                source_type=source_type,
                source_title=title,
                source_url=source_uri,
                extracted_fact=extracted_fact,
                excerpt=doc,
                is_synthetic=is_syn,
                metadata={
                    **meta,
                    "search_query": query,
                    "confidence_score": round(confidence, 2),
                },
            )
            evidence_items.append(ev)

        return evidence_items

    def get_document_by_id(self, chunk_id: str) -> Optional[Evidence]:
        """Retrieves an exact chunk by its ID."""
        try:
            res = self.collection.get(ids=[chunk_id])
            if res and res.get("documents") and res["documents"]:
                doc = res["documents"][0]
                meta = res["metadatas"][0] if res.get("metadatas") else {}
                title = str(meta.get("title", "Internal Document"))
                source_uri = str(meta.get("source_uri", "internal://handbook"))
                is_syn = bool(meta.get("is_synthetic", True))
                source_type = SourceType.SYNTHETIC_INTERNAL if is_syn else SourceType.INTERNAL_DOCUMENT
                
                fact_lines = [l.strip() for l in doc.split("\n") if l.strip() and not l.startswith("#")]
                extracted_fact = fact_lines[0] if fact_lines else doc.strip()[:200]
                if not extracted_fact:
                    extracted_fact = f"Internal intelligence finding from {title}"

                return Evidence(
                    source_type=source_type,
                    source_title=title,
                    source_url=source_uri,
                    extracted_fact=extracted_fact,
                    excerpt=doc,
                    is_synthetic=is_syn,
                    metadata=meta,
                )
        except Exception as e:
            logger.error("Error retrieving chunk %s: %s", chunk_id, e)
        return None
