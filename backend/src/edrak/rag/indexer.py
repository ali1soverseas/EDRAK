"""Internal knowledge indexer for EDRAK RAG subsystem.

Indexes internal documents, handbook markdown files, and synthetic internal data
into ChromaDB vector database.

Features:
  * Document header (# Title, **Document ID:**, **Last Updated:** ...) is parsed into
    metadata instead of being indexed as body text.
  * Heading-aware chunking; heading is stored in metadata and prepended to the chunk.
  * chunk_overlap is actually honoured (carry last paragraph into next chunk).
  * is_synthetic is explicit and stored as "true"/"false" (parse with as_bool()).
  * source_uri is an internal:// URI, not an absolute local path.
  * Collection uses cosine distance so scores are interpretable.
"""

from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from edrak.core.config import settings
from edrak.rag.embeddings import get_embedding_function
from edrak.rag.text_utils import (
    parse_doc_header,
    parse_frontmatter,
    split_sections,
    split_sentences,
)

logger = logging.getLogger(__name__)

COLLECTION_METADATA = {
    "description": "EDRAK Internal Knowledge Base & Handbook",
    "hnsw:space": "cosine",
}


@dataclass
class DocumentChunk:
    """Represents a chunk of indexed text with metadata."""

    chunk_id: str
    content: str
    source_uri: str
    title: str
    doc_type: str
    metadata: Dict[str, Any]


def _meta_value(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


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
        logger.info("Embedding function in use: %s", type(self.embedding_fn).__name__)

        self.persist_dir.mkdir(parents=True, exist_ok=True)
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
        except ValueError:
            logger.warning(
                "Embedding function mismatch on '%s'. Resetting collection...",
                self.collection_name,
            )
            self._drop_and_create()

    def _drop_and_create(self) -> None:
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self._client.create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_fn,
            metadata=COLLECTION_METADATA,
        )

    # ------------------------------------------------------------------ chunking
    @staticmethod
    def _pack(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
        """Pack paragraphs into <= chunk_size pieces, carrying a small overlap."""
        paras: List[str] = []
        for p in (x.strip() for x in text.split("\n\n")):
            if not p:
                continue
            if len(p) > chunk_size * 1.5:  # very long paragraph: split by sentence
                paras.extend(split_sentences(p) or [p])
            else:
                paras.append(p)

        pieces: List[str] = []
        cur: List[str] = []
        cur_len = 0
        for p in paras:
            if cur and cur_len + len(p) > chunk_size:
                pieces.append("\n\n".join(cur))
                tail = cur[-1]
                cur = [tail] if 0 < chunk_overlap and len(tail) <= chunk_overlap else []
                cur_len = sum(len(x) + 2 for x in cur)
            cur.append(p)
            cur_len += len(p) + 2
        if cur:
            pieces.append("\n\n".join(cur))
        return pieces

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
        """Heading-aware chunking. `text` should already have its header stripped."""
        extra_meta = extra_metadata or {}
        chunks: List[DocumentChunk] = []
        idx = 0
        clean_title = title.strip() if title else ""
        for heading, section_text in split_sections(text, default_heading=title):
            clean_heading = heading.strip() if heading else ""
            if clean_title and clean_heading and clean_heading.lower() != clean_title.lower():
                prefix = f"# {clean_title}\n## {clean_heading}\n\n"
            elif clean_title:
                prefix = f"# {clean_title}\n\n"
            elif clean_heading:
                prefix = f"## {clean_heading}\n\n"
            else:
                prefix = ""

            for piece in self._pack(section_text, chunk_size, chunk_overlap):
                content = f"{prefix}{piece}" if prefix else piece
                h = hashlib.md5(f"{source_uri}:{idx}:{heading}:{piece[:50]}".encode()).hexdigest()[:12]
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"chunk_{h}",
                        content=content,
                        source_uri=source_uri,
                        title=title,
                        doc_type=doc_type,
                        metadata={
                            "source_uri": source_uri,
                            "title": title,
                            "doc_type": doc_type,
                            "heading": heading,
                            **extra_meta,
                        },
                    )
                )
                idx += 1
        return chunks

    # ------------------------------------------------------------------ indexing
    def index_document(
        self,
        content: str,
        source_uri: str,
        title: str = "",
        doc_type: str = "internal_doc",
        extra_metadata: Optional[Dict[str, Any]] = None,
        is_synthetic: Optional[bool] = None,
        fallback_title: str = "Internal Document",
    ) -> int:
        """Parse header, chunk by heading, and upsert. Returns number of chunks."""
        fm, body = parse_frontmatter(content)
        header, body = parse_doc_header(body)

        resolved_title = title or fm.get("title") or header.get("title") or fallback_title
        if is_synthetic is None:
            is_synthetic = "synthetic" in header.get("classification", "").lower()

        doc_meta: Dict[str, Any] = {
            k: v for k, v in header.items() if k != "title"
        }  # document_id, classification, last_updated, author
        doc_meta.update({k: v for k, v in fm.items() if k not in ("title", "source_url")})
        doc_meta.update(extra_metadata or {})
        doc_meta["is_synthetic"] = bool(is_synthetic)
        doc_meta["origin"] = "synthetic_internal" if is_synthetic else doc_meta.get("origin", "internal")

        chunks = self.chunk_text(
            text=body,
            source_uri=source_uri,
            title=resolved_title,
            doc_type=doc_type,
            extra_metadata=doc_meta,
        )
        if not chunks:
            return 0

        metadatas = []
        for c in chunks:
            md = {k: _meta_value(v) for k, v in c.metadata.items()}
            metadatas.append({k: v for k, v in md.items() if v is not None})

        self.collection.upsert(
            ids=[c.chunk_id for c in chunks],
            documents=[c.content for c in chunks],
            metadatas=metadatas,
        )
        logger.info("Indexed %d chunks for '%s'", len(chunks), source_uri)
        return len(chunks)

    def index_file(
        self,
        file_path: Path,
        doc_type: Optional[str] = None,
        is_synthetic: Optional[bool] = None,
        base_dir: Optional[Path] = None,
    ) -> int:
        """Reads and indexes a single markdown or text file."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        dtype = doc_type or ("internal_handbook" if "handbook" in file_path.name.lower() else "internal_doc")
        content = file_path.read_text(encoding="utf-8")

        rel = file_path.name
        if base_dir is not None:
            try:
                rel = file_path.resolve().relative_to(base_dir.resolve()).as_posix()
            except ValueError:
                pass

        return self.index_document(
            content=content,
            source_uri=f"internal://{rel}",
            title="",
            doc_type=dtype,
            extra_metadata={"filename": file_path.name},
            is_synthetic=is_synthetic,
            fallback_title=file_path.stem.replace("_", " ").title(),
        )

    def index_directory(self, dir_path: Path, is_synthetic: Optional[bool] = True) -> int:
        """Index all .md/.txt files. Everything under data/internal is synthetic in this pilot."""
        if not dir_path.exists():
            return 0
        total = 0
        for ext in ("*.md", "*.txt"):
            for fp in dir_path.rglob(ext):
                try:
                    total += self.index_file(fp, is_synthetic=is_synthetic, base_dir=dir_path)
                except Exception as e:
                    logger.error("Failed to index %s: %s", fp, e)
        return total

    def reset_collection(self) -> None:
        """Empties the collection. NOTE: this also removes any handbook chunks - re-run
        the handbook indexing step afterwards."""
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self._client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_fn,
            metadata=COLLECTION_METADATA,
        )
