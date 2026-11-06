"""Uploaded PDF in, ProfileDraft out. The file is read from a temporary copy that is deleted, and never stored."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Optional

from edrak.company_profile.draft import build_profile_draft
from edrak.company_profile.extractor import LLMCall
from edrak.company_profile.models import ProfileDraft
from edrak.company_profile.pdf_import.errors import PdfRejected
from edrak.company_profile.pdf_import.extract import ExtractedPdf, extract_document
from edrak.company_profile.pdf_import.links import links_from_pages
from edrak.company_profile.pdf_import.select import page_document, select_pages
from edrak.core.config import settings

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


def check_upload(content: bytes) -> None:
    """Raise PdfRejected unless `content` is a non-empty PDF within the size limit."""
    if not content:
        raise PdfRejected("empty_file", "The file is empty.")
    if len(content) > settings.PROFILE_IMPORT_PDF_MAX_MB * 1024 * 1024:
        raise PdfRejected("too_large", f"PDF files can be up to {settings.PROFILE_IMPORT_PDF_MAX_MB} MB.")
    if not content.startswith(PDF_MAGIC):
        raise PdfRejected("not_a_pdf", "This file is not a PDF. Its contents do not match a PDF document.")


def extract_bytes(content: bytes, **options) -> ExtractedPdf:
    """The pages of a PDF given as bytes, read from a temporary file that is gone when this returns.

    `options` go to `extract_document`.
    """
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        return extract_document(path, **options)
    finally:
        try:
            os.unlink(path)
        except OSError:
            logger.warning("Could not remove the temporary PDF %s", path)


def _collect(content: bytes):
    """Everything the model call needs."""
    extracted = extract_bytes(content)
    chosen, selection_warnings = select_pages(extracted.pages)
    return (
        [page_document(p) for p in chosen],
        links_from_pages(extracted.pages),
        extracted.warnings + selection_warnings,
    )


async def import_profile_from_pdf(content: bytes, *, llm: Optional[LLMCall] = None) -> ProfileDraft:
    """A draft company profile read from the bytes of an uploaded PDF.

    Raises PdfRejected for a file that cannot be imported, and the core's ProfileLLMError or
    ProfileExtractionError when the model step fails. Nothing is saved.
    """
    check_upload(content)
    documents, links, warnings = await asyncio.to_thread(_collect, content)
    if not documents:
        warnings.append("No readable text was found in this PDF.")
    draft = await asyncio.to_thread(build_profile_draft, documents, links, llm=llm)
    draft.warnings = warnings + draft.warnings
    return draft
