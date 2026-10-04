"""Internal knowledge indexer for EDRAK RAG subsystem.

Indexes internal documents, handbook markdown files, and synthetic internal data
into ChromaDB vector database.
"""

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

import chromadb
from chromadb.config import Settings as ChromaSettings

from edrak.core.config import settings
from edrak.rag.embeddings import get_embedding_function

logger = logging.getLogger(__name__)


@dataclass
class DocumentChunk:
    """Represents a chunk of indexed text with metadata."""

    chunk_id: str
    content: str
    source_uri: str
    title: str
    doc_type: str
    metadata: Dict[str, Any]


class InternalIndexer:
    """Manages document chunking and vector indexing for internal company knowledge."""

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        collection_name: Optional[str] = None,
    ):
        self.persist_dir = persist_dir or settings.get_absolute_vector_store_path()
        self.collection_name = collection_name or settings.CHROMA_COLLECTION_NAME
        self.embedding_fn = get_embedding_function()

        # Ensure persist directory exists
        self.persist_dir.mkdir(parents=True, exist_ok=True)

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
            # If collection exists with different embedding function, delete and re-create
            logger.warning("Embedding function mismatch on collection '%s'. Resetting collection...", self.collection_name)
            try:
                self._client.delete_collection(self.collection_name)
            except Exception:
                pass
            self.collection = self._client.create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn,
                metadata={"description": "EDRAK Internal Knowledge Base & Handbook"},
            )

    def chunk_text(
        self,
        text: str,
        source_uri: str,
        title: str = "",
        doc_type: str = "internal_doc",
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[DocumentChunk]:
        """Splits markdown or plain text by headers or sliding window into logical chunks."""
        extra_meta = extra_metadata or {}
        chunks: List[DocumentChunk] = []

        # Try splitting on markdown headings first if text contains headings
        paragraphs = text.split("\n\n")
        current_chunk = []
        current_length = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_len = len(para)
            if current_length + para_len > chunk_size and current_chunk:
                combined = "\n\n".join(current_chunk)
                chunk_hash = hashlib.md5(f"{source_uri}:{combined[:50]}".encode()).hexdigest()[:12]
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"chunk_{chunk_hash}",
                        content=combined,
                        source_uri=source_uri,
                        title=title,
                        doc_type=doc_type,
                        metadata={
                            "source_uri": source_uri,
                            "title": title,
                            "doc_type": doc_type,
                            **extra_meta,
                        },
                    )
                )
                # Keep small overlap from end of previous chunk if helpful
                current_chunk = [para]
                current_length = para_len
            else:
                current_chunk.append(para)
                current_length += para_len + 2

        if current_chunk:
            combined = "\n\n".join(current_chunk)
            chunk_hash = hashlib.md5(f"{source_uri}:{combined[:50]}".encode()).hexdigest()[:12]
            chunks.append(
                DocumentChunk(
                    chunk_id=f"chunk_{chunk_hash}",
                    content=combined,
                    source_uri=source_uri,
                    title=title,
                    doc_type=doc_type,
                    metadata={
                        "source_uri": source_uri,
                        "title": title,
                        "doc_type": doc_type,
                        **extra_meta,
                    },
                )
            )

        return chunks

    def index_document(
        self,
        content: str,
        source_uri: str,
        title: str = "",
        doc_type: str = "internal_doc",
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Chunks and stores a single document into the vector collection."""
        chunks = self.chunk_text(
            text=content,
            source_uri=source_uri,
            title=title,
            doc_type=doc_type,
            extra_metadata=extra_metadata,
        )
        if not chunks:
            return 0

        ids = [c.chunk_id for c in chunks]
        documents = [c.content for c in chunks]
        metadatas = [
            {
                k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v))
                for k, v in c.metadata.items()
            }
            for c in chunks
        ]

        self.collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info("Indexed %d chunks for '%s'", len(chunks), source_uri)
        return len(chunks)

    def index_file(self, file_path: Path, doc_type: Optional[str] = None) -> int:
        """Reads and indexes a single markdown or text file."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        dtype = doc_type or ("internal_handbook" if "handbook" in file_path.name.lower() else "internal_doc")
        content = file_path.read_text(encoding="utf-8")
        title = file_path.stem.replace("_", " ").title()

        return self.index_document(
            content=content,
            source_uri=str(file_path.as_posix()),
            title=title,
            doc_type=dtype,
            extra_metadata={"filename": file_path.name},
        )

    def index_directory(self, dir_path: Path) -> int:
        """Recursively scans and indexes all .md, .txt files in a directory."""
        if not dir_path.exists():
            return 0

        total_chunks = 0
        for ext in ("*.md", "*.txt"):
            for file_path in dir_path.rglob(ext):
                try:
                    count = self.index_file(file_path)
                    total_chunks += count
                except Exception as e:
                    logger.error("Failed to index %s: %s", file_path, e)

        return total_chunks

    def reset_collection(self) -> None:
        """Empties the current vector collection."""
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self._client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_fn,
            metadata={"description": "EDRAK Internal Knowledge Base & Handbook"},
        )
