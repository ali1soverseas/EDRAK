"""Upload, index and remove knowledge-base documents.

An upload is checked and stored at once (status "uploaded"); indexing then runs in the background:
extract text, chunk it, scan the chunks, write the clean chunks to the vector store and mirror
every chunk into document_chunks. The Sources screen polls the document's status and progress.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from edrak.core.config import settings
from edrak.db.connection import get_db
from edrak.db.repositories import company as company_repo
from edrak.db.repositories import documents as documents_repo
from edrak.db.util import new_id, now_iso
from edrak.knowledge.extract import (
    ALLOWED_EXTENSIONS,
    MIME_BY_EXT,
    TABLE_EXTENSIONS,
    Extracted,
    ExtractionError,
    extension,
    extract,
    sniff_matches_extension,
)

logger = logging.getLogger(__name__)

_MAX_PARALLEL = 2
_semaphore: Optional[asyncio.Semaphore] = None
_jobs: set[asyncio.Task[Any]] = set()


class UploadRejected(ValueError):
    """An upload that was refused before it was stored. `code` is the API error code."""

    def __init__(self, code: str, message: str, existing: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.existing = existing


# A chunk scanner returns, for each chunk text, (flags, exclude). Guardrails plug in here.
ChunkScanner = Callable[[str], tuple[list[str], bool]]


def _no_scan(_text: str) -> tuple[list[str], bool]:
    return [], False


def _scanner() -> ChunkScanner:
    try:
        from edrak.guardrails.documents import scan_chunk

        return scan_chunk
    except ImportError:
        return _no_scan


# ------------------------------------------------------------------ storage


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_storage(storage_uri: str) -> Optional[Path]:
    """The file behind a storage_uri, only if it lies inside the data or uploads folder."""
    if not storage_uri or storage_uri == "handbook":
        return None
    data = settings.get_data_path()
    raw = Path(storage_uri)
    path = (raw if raw.is_absolute() else data / raw).resolve()
    if not (_inside(path, data) or _inside(path, settings.get_uploads_path())):
        return None
    return path if path.is_file() else None


def _store_original(workspace_id: str, document_id: str, filename: str, content: bytes) -> str:
    """Write the original. Stored relative to the data folder when it lies inside it, else absolute."""
    folder = settings.get_uploads_path() / workspace_id
    folder.mkdir(parents=True, exist_ok=True)
    path = (folder / f"{document_id}{extension(filename)}").resolve()
    path.write_bytes(content)
    data = settings.get_data_path()
    return path.relative_to(data).as_posix() if _inside(path, data) else str(path)


def clean_filename(filename: str) -> str:
    name = Path(filename or "").name.strip().replace("\x00", "")
    return name[:200] or "document.txt"


# ------------------------------------------------------------------ upload


async def accept_upload(
    db: Any, *, workspace_id: str, user_id: Optional[str], filename: str, content: bytes
) -> dict[str, Any]:
    """Check and store one upload, then queue it for indexing. Raises UploadRejected."""
    name = clean_filename(filename)
    ext = extension(name)
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadRejected("unsupported_type", f"{ext or 'This file type'} is not supported. Use PDF, Word, Excel, CSV, Markdown, text or JSON.")
    if not content:
        raise UploadRejected("empty_file", "The file is empty.")
    if len(content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise UploadRejected("too_large", f"Files can be up to {settings.MAX_UPLOAD_MB} MB.")
    if not sniff_matches_extension(name, content):
        raise UploadRejected("type_mismatch", "The file's contents do not match its extension.")
    digest = hashlib.sha256(content).hexdigest()
    existing = await documents_repo.find_by_hash(db, workspace_id, digest)
    if existing:
        raise UploadRejected("duplicate", f"This file is already in your knowledge base as {existing['name']}.", existing)

    document_id = new_id()
    storage_uri = _store_original(workspace_id, document_id, name, content)
    doc = await documents_repo.create_document(
        db,
        document_id=document_id,
        workspace_id=workspace_id,
        name=name,
        storage_uri=storage_uri,
        mime_type=MIME_BY_EXT.get(ext),
        size_bytes=len(content),
        content_hash=digest,
        uploaded_by=user_id,
        kind="table" if ext in TABLE_EXTENSIONS else "document",
    )
    schedule_indexing(workspace_id, document_id)
    return doc


def schedule_indexing(workspace_id: str, document_id: str) -> None:
    task = asyncio.get_running_loop().create_task(index_document(workspace_id, document_id))
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)


async def wait_for_indexing() -> None:
    """Tests use this to wait for queued indexing jobs."""
    while _jobs:
        await asyncio.gather(*list(_jobs), return_exceptions=True)


# ------------------------------------------------------------------ indexing


def _progress(workspace_id: str, document_id: str) -> Callable[..., Any]:
    async def update(**fields: Any) -> None:
        async with get_db() as db:
            await documents_repo.update_document(db, document_id, **fields)

    return update


async def index_document(workspace_id: str, document_id: str) -> None:
    """Extract, chunk, scan and index one stored document. Never raises."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_PARALLEL)
    update = _progress(workspace_id, document_id)
    async with _semaphore:
        try:
            async with get_db() as db:
                doc = await documents_repo.get_document(db, workspace_id, document_id)
            if not doc:
                return
            path = resolve_storage(doc["storage_uri"])
            if path is None:
                raise ExtractionError("The original file is missing.")
            await update(status="indexing", index_progress=10, error_message=None)

            content = await asyncio.to_thread(path.read_bytes)
            extracted: Extracted = await asyncio.to_thread(extract, doc["name"], content)
            await update(index_progress=35, page_count=extracted.page_count, row_count=extracted.row_count,
                         kind=extracted.kind)

            if extracted.json_data is not None:
                await _maybe_ingest_profile(workspace_id, extracted.json_data)

            if not extracted.text.strip():
                raise ExtractionError(
                    "No text could be read from this file. If it is a scan, upload a text version."
                )

            chunks = await asyncio.to_thread(_prepare_and_store, doc, extracted.text, workspace_id)
            await update(index_progress=85)
            async with get_db() as db:
                await documents_repo.replace_chunks(
                    db, workspace_id=workspace_id, document_id=document_id, chunks=chunks,
                    embedding_model=settings.EMBEDDING_MODEL_NAME,
                )
                flagged = [c for c in chunks if c["flags"]]
                excluded = [c for c in chunks if c["excluded"]]
                notes = sorted({f for c in flagged for f in c["flags"]})
                if chunks and len(excluded) / len(chunks) > 0.3:
                    status = "quarantined"
                elif flagged:
                    status = "flagged"
                else:
                    status = "clean"
                await documents_repo.update_document(
                    db, document_id, status="indexed", index_progress=100, indexed_at=now_iso(),
                    guardrail_status=status, guardrail_notes=notes,
                )
                if flagged:
                    from edrak.db.repositories import guardrails as guardrails_repo

                    await guardrails_repo.record(
                        db, workspace_id=workspace_id, stage="document", document_id=document_id,
                        check_name="document_scan",
                        severity="high" if excluded else "medium",
                        action="blocked" if status == "quarantined" else "flagged",
                        detail={"chunks": len(chunks), "flagged": len(flagged), "excluded": len(excluded), "notes": notes[:10]},
                    )
            await _after_indexing(workspace_id, document_id)
        except ExtractionError as exc:
            await update(status="failed", error_message=str(exc), index_progress=0)
        except Exception as exc:  # noqa: BLE001 - a crash must show as a failed file, not hang
            logger.exception("Indexing %s failed", document_id)
            await update(status="failed", error_message=f"Indexing failed: {exc}"[:300], index_progress=0)


def _prepare_and_store(doc: dict[str, Any], text: str, workspace_id: str) -> list[dict[str, Any]]:
    """Blocking: chunk, scan, write the clean chunks to Chroma. Returns every chunk for the mirror table."""
    from edrak.rag.indexer import InternalIndexer

    indexer = InternalIndexer()
    prepared = indexer.prepare_chunks(
        content=text,
        source_uri=f"internal://{doc['id']}/{doc['name']}",
        title=doc["name"],
        doc_type="internal_doc",
        extra_metadata={"filename": doc["name"], "document_id": doc["id"]},
        is_synthetic=bool(doc.get("is_synthetic")),
        fallback_title=doc["name"],
        workspace_id=workspace_id,
    )
    scan = _scanner()
    rows: list[dict[str, Any]] = []
    clean = []
    for chunk in prepared:
        flags, exclude = scan(chunk.content)
        rows.append({
            "id": chunk.chunk_id,
            "content": chunk.content,
            "locator": chunk.metadata.get("heading") or None,
            "flags": flags,
            "excluded": exclude,
        })
        if not exclude:
            clean.append(chunk)
    # Drop anything this document had indexed before, then write the clean chunks.
    indexer.delete_workspace_documents(workspace_id, document_id=doc["id"])
    indexer.upsert_chunks(clean)
    return rows


async def _maybe_ingest_profile(workspace_id: str, data: Any) -> None:
    """A JSON file that looks like a company profile fills the profile's empty fields."""
    if not isinstance(data, dict) or "name" not in data:
        return
    keys = ("products", "offerings", "aliases", "description", "notes", "strategic_priorities", "differentiators")
    if not any(k in data for k in keys):
        return
    async with get_db() as db:
        current = await company_repo.get_company(db, workspace_id)
        fields: dict[str, Any] = {}
        if not current["name"]:
            fields["name"] = str(data.get("name") or "").strip()
        if not current["aliases"] and data.get("aliases"):
            fields["aliases"] = list(data["aliases"])
        if not current["description"] and (data.get("description") or data.get("notes")):
            fields["description"] = str(data.get("description") or data.get("notes"))
        if not current["offerings"] and (data.get("offerings") or data.get("products")):
            fields["offerings"] = list(data.get("offerings") or data.get("products") or [])
        if not current["industry"] and data.get("industry"):
            fields["industry"] = str(data["industry"])
        if not current["markets"]:
            markets = list(data.get("markets") or [])
            if not markets and isinstance(data.get("tam"), dict) and data["tam"].get("core_market"):
                markets = [str(data["tam"]["core_market"])]
            if markets:
                fields["markets"] = [{"name": m, "relation": "serves"} for m in markets]
        if not current["goals"]:
            goals = data.get("strategic_goals") or data.get("strategic_priorities")
            if isinstance(goals, str):
                goals = [goals]
            if goals:
                fields["goals"] = list(goals)
        if not current["website"] and data.get("website"):
            fields["website"] = str(data["website"])
        if fields:
            await company_repo.save_company(db, workspace_id, fields)


async def _after_indexing(workspace_id: str, document_id: str) -> None:
    """Hooks that run once a file is indexed (retrievability scoring, notifications)."""
    try:
        from edrak.evaluation.documents import score_retrievability

        await score_retrievability(workspace_id, document_id)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - scoring is best effort
        logger.exception("Retrievability scoring failed for %s", document_id)


# ------------------------------------------------------------------ removal


async def remove_document(db: Any, workspace_id: str, document_id: str) -> bool:
    doc = await documents_repo.get_document(db, workspace_id, document_id)
    if not doc:
        return False
    chunk_ids = [c["id"] for c in await documents_repo.list_chunks(db, workspace_id, document_id)]

    def purge_index() -> None:
        from edrak.rag.indexer import InternalIndexer

        indexer = InternalIndexer()
        if chunk_ids:
            indexer.delete_document_chunks(chunk_ids)
        indexer.delete_workspace_documents(workspace_id, document_id=document_id)
        if doc["storage_uri"] == "handbook":
            indexer.delete_workspace_documents(workspace_id, origin="public_handbook")
        else:
            # Documents indexed before chunk ids were recorded are found by file name.
            indexer.delete_workspace_documents(workspace_id, filename=doc["name"])

    try:
        await asyncio.to_thread(purge_index)
    except Exception as exc:  # noqa: BLE001 - the record still goes; stale vectors are filtered by workspace
        logger.warning("Could not remove vectors for %s: %s", document_id, exc)

    path = resolve_storage(doc["storage_uri"])
    if path is not None and _inside(path, settings.get_uploads_path()):
        path.unlink(missing_ok=True)   # only uploads are deleted; seeded files stay
    return await documents_repo.delete_document(db, workspace_id, document_id)


async def retry_document(db: Any, workspace_id: str, document_id: str) -> bool:
    doc = await documents_repo.get_document(db, workspace_id, document_id)
    if not doc or doc["status"] == "indexing":
        return False
    await documents_repo.update_document(db, document_id, status="uploaded", index_progress=0, error_message=None)
    schedule_indexing(workspace_id, document_id)
    return True
