"""Knowledge-base documents: upload, list, view, download, retry, remove.

/documents is the API the Data sources screen uses. /company/documents keeps the MVP paths.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from urllib.parse import quote

import aiosqlite
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from edrak.api.deps import get_current_session, get_db_conn
from edrak.api.schemas import CompanyDocumentOut, DocumentSize, SessionOut
from edrak.db.repositories import documents as documents_repo
from edrak.knowledge.extract import ExtractionError, extension, extract
from edrak.knowledge.pipeline import UploadRejected, accept_upload, remove_document, resolve_storage, retry_document

router = APIRouter(tags=["documents"])

PREVIEW_TEXT_LIMIT = 200_000


def document_out(doc: dict[str, Any]) -> CompanyDocumentOut:
    size = documents_repo.size_label(doc)
    status = doc["status"]
    view_status = "indexing" if status in ("uploaded", "indexing") else status
    return CompanyDocumentOut(
        document_id=doc["id"],
        filename=doc["name"],
        size=DocumentSize(**size) if size else None,
        status=view_status,
        kind=doc.get("kind") or "document",
        mime_type=doc.get("mime_type"),
        size_bytes=doc.get("size_bytes"),
        index_progress=doc.get("index_progress"),
        error_message=doc.get("error_message"),
        added_by=doc.get("uploaded_by_name"),
        added_at=doc.get("created_at"),
        used_in_runs=int(doc.get("used_in_runs") or 0),
        guardrail_status=doc.get("guardrail_status") or "clean",
        guardrail_notes=doc.get("guardrail_notes") or [],
        retrievability=doc.get("retrievability"),
        is_synthetic=bool(doc.get("is_synthetic")),
    )


class UploadError(BaseModel):
    filename: str
    code: str
    message: str


class UploadOut(BaseModel):
    documents: list[CompanyDocumentOut] = Field(default_factory=list)
    errors: list[UploadError] = Field(default_factory=list)


async def _upload(files: list[UploadFile], session: SessionOut, db: aiosqlite.Connection) -> UploadOut:
    out = UploadOut()
    for file in files:
        content = await file.read()
        try:
            doc = await accept_upload(
                db, workspace_id=session.workspace.workspace_id, user_id=session.user.user_id,
                filename=file.filename or "document.txt", content=content,
            )
            out.documents.append(document_out(doc))
        except UploadRejected as exc:
            out.errors.append(UploadError(filename=file.filename or "", code=exc.code, message=str(exc)))
    return out


@router.post("/documents", response_model=UploadOut)
async def upload_documents(
    files: list[UploadFile] = File(...),
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> UploadOut:
    return await _upload(files, session, db)


@router.get("/documents", response_model=list[CompanyDocumentOut])
async def list_documents(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[CompanyDocumentOut]:
    return [document_out(d) for d in await documents_repo.list_documents(db, session.workspace.workspace_id)]


async def _require(db: aiosqlite.Connection, session: SessionOut, document_id: str) -> dict[str, Any]:
    doc = await documents_repo.get_document(db, session.workspace.workspace_id, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="not_found")
    return doc


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    if not await remove_document(db, session.workspace.workspace_id, document_id):
        raise HTTPException(status_code=404, detail="not_found")
    return {"status": "deleted"}


@router.post("/documents/{document_id}/retry", response_model=CompanyDocumentOut)
async def retry(
    document_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> CompanyDocumentOut:
    await _require(db, session, document_id)
    if not await retry_document(db, session.workspace.workspace_id, document_id):
        raise HTTPException(status_code=409, detail="wrong_state")
    return document_out(await _require(db, session, document_id))


@router.get("/documents/{document_id}/file")
async def download_file(
    document_id: str,
    download: bool = False,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> FileResponse:
    """The original file. Inline by default so the viewer can show PDFs; ?download=1 saves it."""
    doc = await _require(db, session, document_id)
    path = resolve_storage(doc["storage_uri"])
    if path is None:
        raise HTTPException(status_code=404, detail="file_missing")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path,
        media_type=doc.get("mime_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(doc['name'])}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300",
        },
    )


class PreviewOut(BaseModel):
    document: CompanyDocumentOut
    view: str                     # "pdf" | "table" | "text" | "unavailable"
    text: Optional[str] = None
    truncated: bool = False
    sheets: list[dict[str, Any]] = Field(default_factory=list)
    chunks: list[dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None


@router.get("/documents/{document_id}/preview", response_model=PreviewOut)
async def preview(
    document_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> PreviewOut:
    """What the file viewer shows: the readable content, and how Edrak chunked and checked it."""
    doc = await _require(db, session, document_id)
    chunks = await documents_repo.list_chunks(db, session.workspace.workspace_id, document_id)
    out = PreviewOut(document=document_out(doc), view="unavailable", chunks=chunks)
    path = resolve_storage(doc["storage_uri"])
    if path is None:
        if chunks:
            out.view, out.text = "text", "\n\n".join(c["content"] for c in chunks)
        else:
            out.message = "The original file is not stored on this server."
        return out
    ext = extension(doc["name"])
    if ext == ".pdf":
        out.view = "pdf"
        return out
    try:
        content = await asyncio.to_thread(path.read_bytes)
        extracted = await asyncio.to_thread(extract, doc["name"], content)
    except ExtractionError as exc:
        out.message = str(exc)
        return out
    if extracted.kind == "table":
        out.view = "table"
        out.sheets = extracted.sheets
    else:
        out.view = "text"
        out.text = extracted.text[:PREVIEW_TEXT_LIMIT]
        out.truncated = len(extracted.text) > PREVIEW_TEXT_LIMIT
    return out


# ------------------------------------------------------------------ MVP paths


@router.get("/company/documents", response_model=list[CompanyDocumentOut], include_in_schema=False)
async def list_documents_legacy(
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[CompanyDocumentOut]:
    return await list_documents(session, db)


@router.post("/company/documents", response_model=list[CompanyDocumentOut], include_in_schema=False)
async def upload_documents_legacy(
    files: list[UploadFile] = File(...),
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> list[CompanyDocumentOut]:
    out = await _upload(files, session, db)
    if out.errors and not out.documents:
        raise HTTPException(status_code=400, detail=out.errors[0].code)
    return out.documents


@router.delete("/company/documents/{document_id}", include_in_schema=False)
async def delete_document_legacy(
    document_id: str,
    session: SessionOut = Depends(get_current_session),
    db: aiosqlite.Connection = Depends(get_db_conn),
) -> dict[str, str]:
    return await delete_document(document_id, session, db)
